"""Server-side media pipeline for One Team Movie Recap.

Nothing in this module imports Streamlit. It is shared by the FastAPI routes and
keeps browser interaction separate from slow Gemini and FFmpeg operations.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import wave
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import requests
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "fonts"
TEXT_MODEL = "gemini-3.7-flash"
MICROSOFT_VOICES = {
    "Nilar": "my-MM-NilarNeural", "Thiha": "my-MM-ThihaNeural",
    "Jenny": "en-US-JennyNeural", "Guy": "en-US-GuyNeural", "Aria": "en-US-AriaNeural",
    "Davis": "en-US-DavisNeural", "Sonia": "en-GB-SoniaNeural", "Ryan": "en-GB-RyanNeural",
    "Xiaoxiao": "zh-CN-XiaoxiaoNeural", "Nanami": "ja-JP-NanamiNeural",
    "SunHi": "ko-KR-SunHiNeural", "Premwadee": "th-TH-PremwadeeNeural", "Swara": "hi-IN-SwaraNeural",
}
GEMINI_VOICES = {
    "Kore": "Kore", "Puck": "Puck", "Charon": "Charon", "Fenrir": "Fenrir",
    "Aoede": "Aoede", "Leda": "Leda", "Orus": "Orus", "Zephyr": "Zephyr",
    "Achernar": "Achernar", "Enceladus": "Enceladus",
}
DEFAULT_MICROSOFT_VOICE = "my-MM-NilarNeural"
FONT_FILES = {
    "Noto Sans Myanmar": FONTS / "NotoSansMyanmar-Regular.ttf",
    "Pyidaungsu": FONTS / "pyidaungsu_regular.ttf",
    "Pyidaungsu Bold": FONTS / "pyidaungsu_bold.ttf",
    "Pyidaungsu 2.5.3 Bold": FONTS / "pyidaungsu_253_bold.ttf",
}
FONT_FAMILIES = {
    "Noto Sans Myanmar": "Noto Sans Myanmar",
    "Pyidaungsu": "Pyidaungsu Green",
    "Pyidaungsu Bold": "Pyidaungsu Green",
    "Pyidaungsu 2.5.3 Bold": "Pyidaungsu",
}
LATIN_FONT = FONTS / "DejaVuSans.ttf"


def _run(command: list[str], timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "FFmpeg failed")[-1800:])
    return result


def probe_video(path: Path) -> dict[str, float | int]:
    """Return visible width/height and duration, including phone rotation."""
    try:
        result = _run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration:stream_tags=rotate:stream_side_data=rotation",
            "-of", "json", str(path),
        ], timeout=45)
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        rotation = 0
        for candidate in [
            (stream.get("tags") or {}).get("rotate"),
            *((item or {}).get("rotation") for item in stream.get("side_data_list") or []),
        ]:
            try:
                rotation = int(float(candidate)) % 360
                break
            except (TypeError, ValueError):
                continue
        if rotation in {90, 270}:
            width, height = height, width
        duration = float(stream.get("duration") or 0)
        if not duration:
            duration_result = _run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path),
            ], timeout=45)
            duration = float(duration_result.stdout.strip() or 0)
        if width < 2 or height < 2 or duration <= 0:
            raise ValueError("missing video metadata")
        return {"width": width, "height": height, "duration": duration}
    except Exception as exc:
        raise RuntimeError("Video information ကိုမဖတ်နိုင်ပါ။ MP4/MOV ဖိုင်ကိုစစ်ပါ။") from exc


def _json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.ok:
        return payload
    message = str((payload.get("error") or {}).get("message") or response.text or f"HTTP {response.status_code}")
    raise RuntimeError(f"Gemini {response.status_code}: {message}")


def upload_to_gemini(api_key: str, video_path: Path, mime_type: str) -> dict[str, Any]:
    """Use the Gemini resumable Files API for full-duration video analysis."""
    file_size = video_path.stat().st_size
    start = requests.post(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        params={"key": api_key},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": video_path.name}},
        timeout=(10, 40),
    )
    _json_response(start)
    upload_url = start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini upload URL မရပါ။")
    with video_path.open("rb") as handle:
        finish = requests.post(
            upload_url,
            headers={"X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0", "Content-Length": str(file_size)},
            data=handle,
            timeout=(10, 240),
        )
    record = _json_response(finish).get("file") or {}
    name = str(record.get("name") or "")
    if not name:
        raise RuntimeError("Gemini video upload မအောင်မြင်ပါ။")
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        poll = requests.get(f"https://generativelanguage.googleapis.com/v1beta/{name}", params={"key": api_key}, timeout=(8, 25))
        record = _json_response(poll).get("file") or {}
        state = str((record.get("state") or "").upper())
        if state == "ACTIVE":
            return record
        if state == "FAILED":
            raise RuntimeError("Gemini က video analysis မလုပ်နိုင်ပါ။")
        time.sleep(2)
    raise RuntimeError("Gemini video analysis အချိန်ကုန်သွားပါသည်။ ခဏနောက်ပြန်စမ်းပါ။")


def _generate_content(api_key: str, model: str, parts: list[dict[str, Any]], *, config: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": config},
        timeout=(10, timeout),
    )
    return _json_response(response)


def _response_text(payload: dict[str, Any]) -> str:
    text = "\n".join(
        str(part.get("text") or "")
        for candidate in payload.get("candidates") or []
        for part in ((candidate.get("content") or {}).get("parts") or [])
    ).strip()
    if not text:
        raise RuntimeError("Gemini Script မပြန်လာပါ။")
    return text


def generate_script(api_key: str, video_path: Path, mime_type: str, language: str, duration_seconds: int, tone: str) -> str:
    media = upload_to_gemini(api_key, video_path, mime_type)
    prompt = (
        f"Write one accurate movie recap narration in {language}. Target runtime: about {duration_seconds} seconds. "
        f"Tone: {tone}. Analyze the complete supplied video, describe only visible or inferable events, do not quote dialogue, "
        "do not add headings, timestamps, scene numbers, warnings, or markdown. Return narration only."
    )
    return _response_text(_generate_content(
        api_key, TEXT_MODEL,
        [{"fileData": {"mimeType": str(media.get("mimeType") or mime_type), "fileUri": str(media.get("uri") or "")}}, {"text": prompt}],
        config={"temperature": 0.65}, timeout=90,
    ))


def microsoft_voice_name(value: str) -> str:
    """Allow only the curated Microsoft voice options sent by the editor."""
    return MICROSOFT_VOICES.get(str(value or "").strip(), DEFAULT_MICROSOFT_VOICE)


def azure_speech_endpoint() -> str:
    configured = os.getenv("AZURE_SPEECH_ENDPOINT", "").strip().rstrip("/")
    if configured:
        return configured if configured.endswith("/cognitiveservices/v1") else f"{configured}/cognitiveservices/v1"
    region = os.getenv("AZURE_SPEECH_REGION", "").strip()
    if not region:
        raise RuntimeError("Microsoft Voice region မသတ်မှတ်ရသေးပါ")
    return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"


def _pcm_to_wav(pcm: bytes, *, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    if not pcm:
        raise RuntimeError("Gemini Voice audio data မပြန်လာပါ")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def generate_gemini_tts(script: str, voice: str, api_key: str) -> bytes:
    """Generate PCM narration with the customer's Gemini key and return WAV bytes."""
    narration = str(script or "").strip()
    if not narration:
        raise RuntimeError("Narration စာသားမရှိပါ")
    selected_voice = GEMINI_VOICES.get(str(voice or "").strip())
    if not selected_voice:
        raise RuntimeError("Gemini Voice မမှန်ပါ")
    try:
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": str(api_key).strip(), "Content-Type": "application/json"},
            json={
                "model": "gemini-3.1-flash-tts-preview",
                "input": narration,
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": selected_voice}]},
            },
            timeout=(10, 120),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Gemini Voice network timeout: {type(exc).__name__}") from exc
    if response.status_code in {401, 403}:
        raise RuntimeError("Gemini Voice Key မမှန်ပါ သို့မဟုတ် ခွင့်ပြုချက်မရှိပါ")
    if response.status_code == 429:
        raise RuntimeError("Gemini Voice အသုံးပြုမှု limit ပြည့်နေပါသည်။ ခဏနောက်ပြန်စမ်းပါ")
    if not response.ok:
        details = response.text.strip()[-400:]
        raise RuntimeError(f"Gemini Voice {response.status_code}: {details or 'Audio မထုတ်နိုင်ပါ'}")
    try:
        payload = response.json()
        encoded = str(((payload.get("output_audio") or {}).get("data")) or "")
        return _pcm_to_wav(base64.b64decode(encoded, validate=True))
    except (ValueError, KeyError, TypeError, base64.binascii.Error) as exc:
        raise RuntimeError("Gemini Voice audio format မမှန်ပါ") from exc


def generate_microsoft_tts(script: str, voice: str) -> bytes:
    """Create MP3 narration without ever exposing the Azure Speech key to a user."""
    key = os.getenv("AZURE_SPEECH_KEY", "").strip()
    if not key:
        raise RuntimeError("Microsoft Voice setting မသတ်မှတ်ရသေးပါ။ Admin ကိုဆက်သွယ်ပါ")
    narration = str(script or "").strip()
    if not narration:
        raise RuntimeError("Narration စာသားမရှိပါ")
    selected_voice = microsoft_voice_name(voice)
    language = selected_voice.rsplit("-", 1)[0] if "-" in selected_voice else "my-MM"
    ssml = (
        f'<speak version="1.0" xml:lang="{language}" '
        'xmlns="http://www.w3.org/2001/10/synthesis">'
        f'<voice name="{selected_voice}">{xml_escape(narration)}</voice></speak>'
    )
    try:
        response = requests.post(
            azure_speech_endpoint(),
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "one-team-movie-recap",
            },
            data=ssml.encode("utf-8"),
            timeout=(10, 80),
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Microsoft Voice network timeout: {type(exc).__name__}") from exc
    if response.status_code == 401:
        raise RuntimeError("Microsoft Voice Key သို့မဟုတ် Region မမှန်ပါ")
    if response.status_code == 429:
        raise RuntimeError("Microsoft Voice အသုံးပြုမှု limit ပြည့်နေပါသည်။ ခဏနောက်ပြန်စမ်းပါ")
    if not response.ok:
        details = response.text.strip()[-400:]
        raise RuntimeError(f"Microsoft Voice {response.status_code}: {details or 'Audio မထုတ်နိုင်ပါ'}")
    if len(response.content) < 256:
        raise RuntimeError("Microsoft Voice audio data မပြန်လာပါ")
    return response.content


def make_srt(script: str, duration: float) -> str:
    words = [item for item in re.split(r"\s+", script.strip()) if item]
    if not words:
        return ""
    pieces = [" ".join(words[index:index + 12]) for index in range(0, len(words), 12)]
    cue_seconds = max(1.4, duration / max(1, len(pieces)))

    def clock(value: float) -> str:
        ms = int(max(0, value) * 1000)
        return f"{ms // 3_600_000:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"

    return "\n\n".join(
        f"{index + 1}\n{clock(index * cue_seconds)} --> {clock(min(duration, (index + 1) * cue_seconds))}\n{line}"
        for index, line in enumerate(pieces)
    )


def ass_color(value: str, alpha: int = 0) -> str:
    color = re.sub(r"[^0-9a-fA-F]", "", value or "")[-6:].rjust(6, "F")
    return f"&H{max(0, min(255, alpha)):02X}{color[4:6]}{color[2:4]}{color[:2]}"


def fitted_source_rect(source: dict[str, float | int], out_width: int, out_height: int) -> dict[str, int]:
    """Return the visible source-video rectangle after contain scaling on the output canvas."""
    source_width = max(1, int(source["width"]))
    source_height = max(1, int(source["height"]))
    scale = min(out_width / source_width, out_height / source_height)
    width = max(2, round(source_width * scale))
    height = max(2, round(source_height * scale))
    return {"x": (out_width - width) // 2, "y": (out_height - height) // 2, "width": width, "height": height}


def srt_to_ass(srt: str, settings: dict[str, Any], width: int, height: int, source_rect: dict[str, int] | None = None) -> str:
    font_size = max(34, min(132, round(float(settings.get("subtitle_size", 24)) * 2.1)))
    visible = source_rect or {"x": 0, "y": 0, "width": width, "height": height}
    x = int(visible["x"]) + math.floor(int(visible["width"]) * max(0, min(100, int(settings.get("subtitle_x", 50)))) / 100 + 0.5)
    y = int(visible["y"]) + math.floor(int(visible["height"]) * max(0, min(100, int(settings.get("subtitle_y", 82)))) / 100 + 0.5)
    mode = str(settings.get("subtitle_background", "Transparent"))
    border_style = 3 if mode == "Solid background" else 1
    opacity = max(0, min(100, int(settings.get("subtitle_opacity", 55))))
    back_alpha = 255 - round(opacity * 255 / 100)
    font = FONT_FAMILIES.get(str(settings.get("subtitle_font", "Noto Sans Myanmar")), "Noto Sans Myanmar")
    header = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: Default,{font},{font_size},{ass_color(str(settings.get('subtitle_color', '#FFD166')))},{ass_color(str(settings.get('subtitle_color', '#FFD166')))},{ass_color(str(settings.get('subtitle_outline', '#000000')) )},{ass_color(str(settings.get('subtitle_background_color', '#000000')), back_alpha)},0,0,0,0,100,100,0,0,{border_style},{0 if border_style == 3 else 2},1,5,20,20,20,1",
        "", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    events: list[str] = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = (piece.strip() for piece in lines[1].split("-->", 1))
        try:
            def convert(value: str) -> str:
                hours, minutes, seconds_ms = value.split(":")
                seconds, milliseconds = seconds_ms.split(",")
                return f"{int(hours)}:{int(minutes):02d}:{int(seconds):02d}.{int(milliseconds) // 10:02d}"
            caption = "\\N".join(lines[2:]).replace("{", "\\{").replace("}", "\\}")
            events.append(f"Dialogue: 0,{convert(start)},{convert(end)},Default,,0,0,0,,{{\\an5\\pos({x},{y})}}{caption}")
        except ValueError:
            continue
    return "\n".join(header + events) + "\n"


def _is_myanmar(char: str) -> bool:
    return "MYANMAR" in unicodedata.name(char, "")


def render_text_logo(text: str) -> Path | None:
    text = " ".join(str(text or "").split())
    if not text:
        return None
    latin = ImageFont.truetype(str(LATIN_FONT), 44)
    mm_file = FONT_FILES["Noto Sans Myanmar"]
    myanmar = ImageFont.truetype(str(mm_file), 44)
    runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for char in text:
        font = myanmar if _is_myanmar(char) else latin
        if runs and runs[-1][1].path == font.path:
            runs[-1] = (runs[-1][0] + char, font)
        else:
            runs.append((char, font))
    widths = [round(font.getlength(run)) for run, font in runs]
    canvas = Image.new("RGBA", (max(1, sum(widths) + 36), 82), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    x = 18
    for (run, font), width in zip(runs, widths):
        draw.text((x, 17), run, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 215))
        x += width
    target = Path(tempfile.mktemp(suffix="-one-team-logo.png"))
    canvas.save(target)
    return target


def _ratio(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def render_final(video_path: Path, tts_wav: bytes, settings: dict[str, Any], output_path: Path) -> Path:
    """Render one final MP4 from settings delivered by the direct browser editor."""
    source = probe_video(video_path)
    aspect = str(settings.get("aspect", "9:16"))
    quality = str(settings.get("quality", "720p"))
    scale = 1280 if quality == "1080p" else 720
    if aspect == "16:9":
        out_w, out_h = round(scale * 16 / 9), scale
    elif aspect == "1:1":
        out_w, out_h = scale, scale
    else:
        out_w, out_h = scale, round(scale * 16 / 9)
    out_w -= out_w % 2
    out_h -= out_h % 2
    visible_rect = fitted_source_rect(source, out_w, out_h)
    temp_paths: list[Path] = []
    audio_path = Path(tempfile.mktemp(suffix=".wav"))
    audio_path.write_bytes(tts_wav)
    temp_paths.append(audio_path)
    ass_path: Path | None = None
    text_logo: Path | None = None
    try:
        video_filters = ["setpts=PTS-STARTPTS"]
        if settings.get("mirror"):
            video_filters.append("hflip")
        if settings.get("auto_zoom"):
            video_filters.extend(["scale=iw*1.08:ih*1.08", "crop=iw/1.08:ih/1.08"])
        if settings.get("color_filter"):
            video_filters.append("eq=contrast=1.04:brightness=0.02:saturation=1.12")
        if settings.get("background_blur"):
            graph = (
                f"[0:v]{','.join(video_filters)},split=2[bgsrc][fgsrc];"
                f"[bgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=20:10[bg];"
                f"[fgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]"
            )
        else:
            video_filters.extend([
                f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease",
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black",
            ])
            graph = f"[0:v]{','.join(video_filters)}[v0]"
        label = "v0"
        for index, mask in enumerate(settings.get("blur_masks") or []):
            x = int(visible_rect["x"]) + round(_ratio(mask.get("x"), 0.2) * int(visible_rect["width"]))
            y = int(visible_rect["y"]) + round(_ratio(mask.get("y"), 0.2) * int(visible_rect["height"]))
            width = max(24, round(_ratio(mask.get("width"), 0.2) * int(visible_rect["width"])))
            height = max(24, round(_ratio(mask.get("height"), 0.12) * int(visible_rect["height"])))
            width -= width % 2
            height -= height % 2
            x = min(max(int(visible_rect["x"]), x), int(visible_rect["x"]) + int(visible_rect["width"]) - width)
            y = min(max(int(visible_rect["y"]), y), int(visible_rect["y"]) + int(visible_rect["height"]) - height)
            next_label = f"vblur{index}"
            graph += f";[{label}]split=2[base{index}][crop{index}];[crop{index}]crop={width}:{height}:{x}:{y},boxblur=10:2[mask{index}];[base{index}][mask{index}]overlay={x}:{y}[{next_label}]"
            label = next_label
        if settings.get("subtitles", True):
            srt = str(settings.get("subtitle_srt") or make_srt(str(settings.get("script") or ""), float(source["duration"])))
            ass_path = Path(tempfile.mktemp(suffix=".ass"))
            ass_path.write_text(srt_to_ass(srt, settings, out_w, out_h, visible_rect), encoding="utf-8", newline="\n")
            temp_paths.append(ass_path)
            font_file = FONT_FILES.get(str(settings.get("subtitle_font")), FONT_FILES["Noto Sans Myanmar"])
            font_dir = Path(tempfile.mkdtemp(prefix="one-team-fonts-"))
            shutil.copy2(font_file, font_dir / font_file.name)
            temp_paths.append(font_dir)
            escaped_ass = str(ass_path).replace("'", "\\'").replace(":", "\\:")
            escaped_fonts = str(font_dir).replace("'", "\\'").replace(":", "\\:")
            graph += f";[{label}]ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'[vsub]"
            label = "vsub"
        logo_enabled = bool(settings.get("logo_enabled", True))
        logo_file = Path(str(settings.get("logo_path") or "")) if logo_enabled else Path()
        if not logo_file.is_file():
            logo_file = None
        text_logo = render_text_logo(str(settings.get("logo_text") or "")) if logo_enabled else None
        if text_logo:
            temp_paths.append(text_logo)
        inputs = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path)]
        music_file = Path(str(settings.get("music_path") or ""))
        music_index: int | None = None
        if music_file.is_file():
            music_index = 2
            inputs.extend(["-stream_loop", "-1", "-i", str(music_file)])
        logo_size = max(10, min(45, int(settings.get("logo_size", 22)))) / 100
        logo_width = max(72, round(int(visible_rect["width"]) * logo_size))
        logo_x = int(visible_rect["x"]) + round(int(visible_rect["width"]) * max(0, min(100, int(settings.get("logo_x", 84)))) / 100)
        logo_y = int(visible_rect["y"]) + round(int(visible_rect["height"]) * max(0, min(100, int(settings.get("logo_y", 86)))) / 100)
        overlays: list[tuple[Path, str, str, int]] = []
        if logo_file:
            overlays.append((logo_file, f"{logo_x}-w/2", f"{logo_y}-h/2", logo_width))
        if text_logo:
            overlays.append((text_logo, f"{logo_x}-w/2", f"{logo_y}-h/2", logo_width))
        overlay_start_index = 2 + (1 if music_index is not None else 0)
        for overlay, _, _, _ in overlays:
            inputs.extend(["-loop", "1", "-i", str(overlay)])
        for offset, (_, x, y, target_width) in enumerate(overlays):
            index = overlay_start_index + offset
            next_label = "vfinal" if offset == len(overlays) - 1 else f"voverlay{index}"
            scaled = f"logo{index}"
            graph += f";[{index}:v]scale={target_width}:-1[{scaled}];[{label}][{scaled}]overlay=x='{x}':y='{y}':shortest=1[{next_label}]"
            label = next_label
        narration_filters = ["aresample=async=1:first_pts=0"]
        if settings.get("pitch_alter"):
            narration_filters.extend(["asetrate=24960", "aresample=24000", "atempo=0.961538"])
        narration_filters.append("volume=1")
        audio_filter = f"[1:a]{','.join(narration_filters)}[narration]"
        audio_inputs = "[narration]"
        original_mode = str(settings.get("original_audio", "Mute"))
        if original_mode != "Mute":
            original_volume = "0.13" if original_mode == "Low" else "0.28"
            audio_filter += f";[0:a]volume={original_volume}[original];[narration][original]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            audio_inputs = "[aout]"
        if music_index is not None:
            music_volume = max(0, min(100, int(settings.get("music_volume", 24)))) / 100
            music_label = "music"
            mix_label = "amixout"
            audio_filter += f";[{music_index}:a]volume={music_volume:.2f}[{music_label}];{audio_inputs}[{music_label}]amix=inputs=2:duration=first:dropout_transition=2[{mix_label}]"
            audio_inputs = f"[{mix_label}]"
        command = inputs + [
            "-filter_complex", graph + ";" + audio_filter,
            "-map", f"[{label}]", "-map", audio_inputs,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output_path),
        ]
        _run(command, timeout=1800)
        if not output_path.exists() or output_path.stat().st_size < 1024:
            raise RuntimeError("Final MP4 မဖန်တီးနိုင်ပါ။")
        return output_path
    finally:
        for path in temp_paths:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
