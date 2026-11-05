"""Server-side media pipeline for One Team Movie Recap."""

from __future__ import annotations
import base64, io, json, math, os, wave, re, shutil, subprocess, tempfile, time, unicodedata, hashlib
import urllib.parse
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent
FONTS = ROOT / "fonts"

FALLBACK_TEXT_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash"]
DEFAULT_MICROSOFT_VOICE = "my-MM-NilarNeural"

GEMINI_VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Leda", "Orus", "Puck", "Zephyr", "Achernar", "Enceladus"]
MICROSOFT_VOICES = {
    "Nilar": "my-MM-NilarNeural", "Thiha": "my-MM-ThihaNeural",
    "Ava": "en-US-AvaMultilingualNeural", "Andrew": "en-US-AndrewMultilingualNeural",
    "Emma": "en-US-EmmaMultilingualNeural", "Brian": "en-US-BrianMultilingualNeural",
    "Vivienne": "fr-FR-VivienneMultilingualNeural", "Remy": "fr-FR-RemyMultilingualNeural",
    "Seraphina": "de-DE-SeraphinaMultilingualNeural", "Florian": "de-DE-FlorianMultilingualNeural",
    "Xiaoxiao": "zh-CN-XiaoxiaoMultilingualNeural", "Giuseppe": "it-IT-GiuseppeMultilingualNeural"
}

FONT_FILES = {
    "Noto Sans Myanmar": FONTS / "NotoSansMyanmar-Regular.ttf",
    "Pyidaungsu": FONTS / "pyidaungsu_253_bold.ttf",
    "Pyidaungsu Bold": FONTS / "pyidaungsu_bold.ttf",
    "A13_WenYoe Regular": FONTS / "A13_WenYoe-Regular.ttf",
    "Z10-Cartoon": FONTS / "Z10-Cartoon (1).ttf",
    
    # Logo အတွက် ဖောင့်အသစ် ၆ ခု
    "Beloved Bunny": FONTS / "Beloved_Bunny.ttf",
    "Devroye Unicode": FONTS / "Devroye.Unicode.ttf",
    "Great Vibes": FONTS / "Great Vibes.ttf",
    "Wreck Pixel": FONTS / "WRECK_PIXEL.ttf",
    "Sail": FONTS / "Sail.ttf",
    "Sweetie": FONTS / "Sweetie.ttf"
}
FONT_FAMILIES = {
    "Noto Sans Myanmar": "Noto Sans Myanmar",
    "Pyidaungsu": "Pyidaungsu Green",
    "Pyidaungsu Bold": "Pyidaungsu Green",
    "A13_WenYoe Regular": "A13_WenYoe Regular",
    "Z10-Cartoon": "Z10-Cartoon"
}
def get_background_audio_only(video_path: Path, temp_dir: Path) -> Path | None:
    try:
        extracted_audio = temp_dir / "extracted_original.mp3"

        _run([
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-q:a", "0",
            "-map", "a",
            str(extracted_audio)
        ], timeout=120)

        api_url = "http://169.58.181.87:8000/separate"

        with open(extracted_audio, "rb") as f:
            response = requests.post(
                api_url,
                files={
                    "file": (
                        extracted_audio.name,
                        f,
                        "audio/mpeg"
                    )
                },
                timeout=1200
            )

        if response.status_code == 200:
            bg_audio_path = temp_dir / "demucs_sfx.wav"

            with open(bg_audio_path, "wb") as f:
                f.write(response.content)

            return bg_audio_path

        raise RuntimeError(
            f"[DEMUCS_ERROR] နောက်ခံအသံမရရှိပါ: "
            f"{response.text[:200]}"
        )

    except Exception as e:
        raise RuntimeError(
            f"[DEMUCS_ERROR] {str(e)[:250]}"
        )
def _run(command: list[str], timeout: int = 1200) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0: raise RuntimeError((result.stderr or "FFmpeg failed")[-1800:])
    return result

def get_audio_duration(audio_path: Path) -> float:
    try:
        res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio_path)], timeout=15)
        return float(res.stdout.strip() or 0.0)
    except:
        return 0.0

def probe_video(path: Path) -> dict[str, Any]:
    try:
        result = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration:stream_tags=rotate:stream_side_data=rotation", "-of", "json", str(path)], timeout=45)
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        rotation = 0
        for candidate in [(stream.get("tags") or {}).get("rotate"), *((item or {}).get("rotation") for item in stream.get("side_data_list") or [])]:
            try: rotation = int(float(candidate)) % 360; break
            except (TypeError, ValueError): continue
        if rotation in {90, 270}: width, height = height, width
        duration = float(stream.get("duration") or 0)
        if not duration:
            dur_res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], timeout=45)
            duration = float(dur_res.stdout.strip() or 0)
            
        has_audio = False
        try:
            a_res = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)], timeout=15)
            if "audio" in a_res.stdout.lower(): has_audio = True
        except: pass
        
        if width < 2 or height < 2 or duration <= 0: raise ValueError("missing video metadata")
        return {"width": width, "height": height, "duration": duration, "has_audio": has_audio}
    except Exception as exc:
        raise RuntimeError("Video information ကိုမဖတ်နိုင်ပါ။") from exc

def upload_media_to_gemini(file_path: Path, api_key: str, mime_type: str, progress=None) -> dict[str, str]:
    if progress: progress(30, "AI ဆာဗာသို့ ဖိုင် တိုက်ရိုက်ပေးပို့နေပါသည်...")
    file_size = file_path.stat().st_size
    upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {"X-Goog-Upload-Protocol": "raw", "X-Goog-Upload-Header-Content-Length": str(file_size), "X-Goog-Upload-Header-Content-Type": mime_type, "Content-Type": mime_type}
    with open(file_path, "rb") as f:
        res = requests.post(upload_url, headers=headers, data=f, timeout=1200)
    if not res.ok:
        try:
            error_obj = res.json().get("error", {})
            detail = _gemini_error_myanmar(
                res.status_code,
                error_obj.get("status", ""),
                error_obj.get("message", res.text[:400]),
            )
        except Exception:
            detail = _gemini_error_myanmar(res.status_code)
        raise RuntimeError(f"AI ဆာဗာသို့ ဖိုင်တင်ခြင်း မအောင်မြင်ပါ။ {detail}")
    file_info = res.json().get("file", {})
    file_name = file_info.get("name")
    file_uri = file_info.get("uri")
    
    if progress: progress(45, "AI မှ ဖိုင်ကို ကြည့်ရှုလေ့လာနေပါသည်...")
    get_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    for _ in range(30):
        chk = requests.get(get_url, timeout=30).json()
        state = chk.get("state")
        if state == "ACTIVE": return {"fileUri": file_uri, "mimeType": mime_type, "name": file_name}
        if state == "FAILED": raise RuntimeError("AI မှ ဖိုင်ကို လေ့လာခြင်း မအောင်မြင်ပါ။")
        time.sleep(5)
    return {"fileUri": file_uri, "mimeType": mime_type, "name": file_name}

def delete_gemini_file(file_name: str, api_key: str):
    try: requests.delete(f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}", timeout=10)
    except: pass

def _gemini_error_myanmar(status_code: int, api_status: str = "", api_message: str = "") -> str:
    """Convert common Gemini API errors into a user-friendly Burmese message."""
    msg = (api_message or "").lower()
    status = (api_status or "").upper()

    if "api key not valid" in msg or "invalid api key" in msg or status_code == 401:
        return "Gemini API Key မမှန်ပါ။ Key ကို မှန်ကန်စွာ ထည့်ထားခြင်းရှိမရှိ စစ်ဆေးပါ။"
    if status_code == 403 or "permission_denied" in status or "permission" in msg or "not authorized" in msg:
        return "Gemini API အသုံးပြုခွင့် မရှိပါ။ API Key၊ မော်ဒယ်အသုံးပြုခွင့်နှင့် Google Cloud သတ်မှတ်ချက်များကို စစ်ဆေးပါ။"
    if status_code == 429 or "resource_exhausted" in status or "quota" in msg or "rate limit" in msg:
        return "Gemini API အသုံးပြုခွင့်ကန့်သတ်ချက် ပြည့်နေပါသည်။ ခဏစောင့်ပြီး ပြန်စမ်းပါ၊ သို့မဟုတ် သင့်အသုံးပြုခွင့်နှင့် quota ကို စစ်ဆေးပါ။"
    if status_code == 404 or "not_found" in status or "model" in msg and ("not found" in msg or "does not exist" in msg):
        return "ခေါ်ယူထားသော Gemini မော်ဒယ်ကို မတွေ့ပါ သို့မဟုတ် အသုံးပြုခွင့်မရှိပါ။ မော်ဒယ်အမည်ကို စစ်ဆေးပါ။"
    if status_code == 400 or "invalid_argument" in status or "invalid" in msg:
        return "Gemini သို့ ပေးပို့သော တောင်းဆိုချက် သို့မဟုတ် အချက်အလက် မမှန်ပါ။ ထည့်သွင်းထားသောစာသားနှင့် ဆက်တင်များကို စစ်ဆေးပါ။"
    if status_code in (408, 500, 502, 503, 504) or "unavailable" in status or "overloaded" in msg or "timeout" in msg:
        return "Gemini ဆာဗာ ခေတ္တအလုပ်များနေခြင်း သို့မဟုတ် အချိန်ကုန်သွားခြင်း ဖြစ်နိုင်ပါသည်။ ခဏစောင့်ပြီး ပြန်စမ်းပါ။"
    if status_code == 0:
        return "Gemini ဆာဗာသို့ ချိတ်ဆက်မရပါ။ အင်တာနက်ချိတ်ဆက်မှုကို စစ်ဆေးပြီး ပြန်စမ်းပါ။"
    return f"Gemini ဝန်ဆောင်မှုတွင် အမှားတစ်ခု ဖြစ်ပွားနေပါသည်။ (အမှားကုဒ်: HTTP {status_code}) ခဏအကြာတွင် ပြန်စမ်းပါ။"


def _generate_content(api_key: str, parts: list[dict[str, Any]], *, config: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Call Gemini with limited retries and preserve the real API error."""
    last_error = "မသိရှိရသေးသော Gemini API အမှား"

    for current_model in FALLBACK_TEXT_MODELS:
        for attempt in range(3):
            try:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent",
                    params={"key": api_key},
                    json={
                        "contents": [{"role": "user", "parts": parts}],
                        "generationConfig": config,
                    },
                    timeout=(10, timeout),
                )

                if response.ok:
                    return response.json()

                try:
                    body = response.json()
                    api_message = body.get("error", {}).get("message", response.text[:500])
                    api_status = body.get("error", {}).get("status", "")
                except Exception:
                    api_message = response.text[:500]
                    api_status = ""

                last_error = _gemini_error_myanmar(
                    response.status_code, api_status, api_message
                )

                # Retry only errors that may clear by waiting.
                if response.status_code in (408, 429, 500, 502, 503, 504):
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue

                # Invalid key/permission/request won't be fixed by retrying.
                if response.status_code in (400, 401, 403):
                    raise RuntimeError(last_error)

                # Try the next model for unavailable/unknown model responses.
                break

            except requests.RequestException:
                last_error = _gemini_error_myanmar(0)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                break

    raise RuntimeError(last_error)

def get_vocals_only(audio_path: Path, temp_dir: Path) -> Path | None:
    import requests
    try:
        api_url = "http://169.58.181.87:8000/vocals"
        with open(audio_path, "rb") as f:
            response = requests.post(
                api_url,
                files={"file": (audio_path.name, f, "audio/mpeg")},
                timeout=1200
            )
        if response.status_code == 200:
            # [အသစ်] Error JSON ကို 200 ဖြင့် ပြန်လာပါက ဖမ်းပေးမည့်စနစ်
            if "application/json" in response.headers.get("Content-Type", ""):
                raise RuntimeError(f"VPS ဆာဗာအတွင်း အမှားဖြစ်နေပါသည်: {response.json().get('error', response.text)}")
                
            vocals_path = temp_dir / "demucs_vocals.wav"
            with open(vocals_path, "wb") as f:
                f.write(response.content)
            return vocals_path
        raise RuntimeError(f"[DEMUCS_ERROR] လူသံမရရှိပါ: {response.text[:200]}")
    except Exception as e:
        raise RuntimeError(f"[DEMUCS_ERROR] {str(e)[:250]}")
def generate_script(api_key: str, video_path: Path, mime_type: str, language: str, duration_seconds: int, tone: str, recap_type: str, timing_mode: str, progress=None, groq_api_key: str = "") -> str:
    gemini_file = None
    temp_audio_path = None
    try:
        vid_info = probe_video(video_path)
        has_audio = vid_info.get("has_audio", False)
        
        # ==========================================
        # GROQ ROUTING FOR MOVIE SUBTITLE
        # ==========================================
        if recap_type == "Movie Subtitle":
            if not groq_api_key:
                raise RuntimeError("Movie Subtitle အတွက် Groq API Key လိုအပ်ပါသည်။ 🔑 ဆက်တင်တွင် သွားထည့်ပါ။")
            if not has_audio:
                raise RuntimeError("Movie Subtitle ပြုလုပ်ရန် Video တွင် အသံ (Audio) ပါဝင်ရပါမည်။")
                
            if progress: progress(25, "⚡ [Movie Subtitle] MP3 ခွဲထုတ်နေပါသည်...")
            temp_audio_path = Path(tempfile.mktemp(suffix=".mp3"))
            _run(["ffmpeg", "-y", "-i", str(video_path), "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-map", "a", str(temp_audio_path)], timeout=120)
            
            if progress: progress(45, "⚡ Groq Whisper သို့ MP3 ပေးပို့၍ အချိန်မှတ်ယူနေပါသည်...")
            groq_headers = {"Authorization": f"Bearer {groq_api_key}"}
            
            with open(temp_audio_path, "rb") as f:
                files = {"file": (temp_audio_path.name, f, "audio/mpeg")}
                data = {"model": "whisper-large-v3", "response_format": "verbose_json"}
                res = requests.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=groq_headers, files=files, data=data, timeout=180)
            
            if not res.ok: raise RuntimeError(f"Groq API (Whisper) ချိတ်ဆက်မှု မအောင်မြင်ပါ: {res.text[:150]}")
                
            segments = res.json().get("segments", [])
            transcript_text = ""
            for seg in segments:
                start_str = f"{int(seg['start'])//60:02d}:{int(seg['start'])%60:02d}"
                end_str = f"{int(seg['end'])//60:02d}:{int(seg['end'])%60:02d}"
                transcript_text += f"[{start_str} - {end_str}] {seg['text']}\n"
                
            if progress: progress(60, "⚡ Gemini မှ မြန်မာစာတန်းထိုးအဖြစ် ဘာသာပြန်နေပါသည်...")
            
            prompt = (
                f"You are an expert subtitle translator. Translate the following timestamps and text into highly natural EVERYDAY SPOKEN Myanmar ({tone} tone).\n"
                "RULES:\n"
                "1. KEEP EXACT TIMESTAMPS: Do not change the [MM:SS - MM:SS] format.\n"
                "2. EVERYDAY SPOKEN MYANMAR (စကားပြောဟန်): Translate strictly into natural spoken Burmese.\n"
                "3. Your first line MUST be 'TITLE: Movie Subtitle'.\n"
                "4. Your second line MUST be 'COVER: NO'.\n"
                "5. Only output the lines in the requested format, nothing else.\n\n"
                f"Transcription:\n{transcript_text[:10000]}"
            )
            
            parts = [{"text": prompt}]
            chat_res = _generate_content(api_key, parts, config={"temperature": 0.3}, timeout=180)
            text = "\n".join(str(p.get("text") or "") for c in chat_res.get("candidates", []) for p in c.get("content", {}).get("parts", [])).strip()
            if not text: raise RuntimeError("Gemini မှ Script မပြန်လာပါ။")
            return text.strip()
            
        # ==========================================
        # GEMINI ROUTING FOR OTHER TYPES
        # ==========================================
        if recap_type in ["Movie Recap", "Movie Dub"] and has_audio:
            if progress: progress(25, f"⚡ [{recap_type}] အသံဖိုင် ခွဲထုတ်နေပါသည်...")
            temp_audio_path = Path(tempfile.mktemp(suffix=".mp3"))
            _run(["ffmpeg", "-y", "-i", str(video_path), "-q:a", "0", "-map", "a", str(temp_audio_path)], timeout=120)
            
            if recap_type == "Movie Dub":
                if progress: progress(30, "🎙️ Movie Dub အတွက် လူအသံ (Vocals) သီးသန့် ခွဲထုတ်နေပါသည်...")
                # အကယ်၍ VPS မှ ခွဲထုတ်ခြင်း မအောင်မြင်ပါက ဤနေရာတွင် RuntimeError ဖြင့် ချက်ချင်းရပ်သွားပါမည်
                vocals_audio_path = get_vocals_only(temp_audio_path, Path(tempfile.gettempdir()))
                if vocals_audio_path:
                    gemini_file = upload_media_to_gemini(vocals_audio_path, api_key, "audio/wav", progress)
                else:
                    raise RuntimeError("[DEMUCS_ERROR] လူသံခွဲထုတ်ခြင်းမှ ဖိုင်မရရှိပါ။")
            else:
                gemini_file = upload_media_to_gemini(temp_audio_path, api_key, "audio/mp3", progress)
        else:
            if progress: progress(25, f"🎬 [{recap_type}] ဗီဒီယိုကို တိုက်ရိုက် လေ့လာနေပါသည်...")
            gemini_file = upload_media_to_gemini(video_path, api_key, "video/mp4", progress)

        parts = [{"fileData": {"fileUri": gemini_file["fileUri"], "mimeType": gemini_file["mimeType"]}}]
        if progress: progress(55, "📝 AI မှ ဇာတ်ညွှန်း (Script) ရေးသားနေပါသည်...")

        hook_instruction = "CRITICAL RULE 1: Your VERY FIRST LINE must be 'TITLE: ' followed by a 3 to 4 word engaging Burmese hook for the video.\n"
        
        if recap_type == "Movie Recap":
            hook_instruction += "CRITICAL RULE 2: Your SECOND LINE must be 'COVER: NO' because you are analyzing an audio file.\n"
        else:
            hook_instruction += "CRITICAL RULE 2: Check the first 1 to 3 seconds of the video. If it has a custom thumbnail, intro text, or cover image covering the video, your SECOND LINE must be 'COVER: YES'. If it starts immediately with the actual movie/scene without any cover, your SECOND LINE must be 'COVER: NO'.\n"

        # ၁ မိနစ်လျှင် မြန်မာစာလုံးရေ ၁၅၀ မှ ၂၀၀ နှုန်းထားဖြင့် အလိုအလျောက် တွက်ချက်ခြင်း
        min_words = max(150, int((duration_seconds / 60) * 150))
        max_words = max(200, int((duration_seconds / 60) * 200))

        if recap_type == "Movie Dub" and has_audio:
            instruction = hook_instruction + (
                f"You are a Master Dubbing Director. Listen to the audio carefully.\n"
                f"Translate the exact meaning into HIGHLY NATURAL, EVERYDAY SPOKEN {language} ({tone} tone).\n"
                "CRITICAL RULES:\n"
                "1. TRANSLATE EVERYONE: Translate ALL dialogues. Do not skip anyone.\n"
                "2. SEQUENTIAL TIMESTAMPS: You MUST provide the exact start and end timestamps from the original audio for every single dialogue line (e.g., [00:12 - 00:15] Adult: Hello).\n"
                "3. DO NOT SUMMARIZE: Do not write explanations. Only write what is being spoken.\n"
                "4. Use everyday conversational language (စကားပြောဟန်)."
            )
        elif recap_type == "Movie Recap" and has_audio:
            instruction = hook_instruction + (
                f"You are an expert Translator and Dubbing Director. Listen to the provided audio file carefully.\n"
                f"Provide a 100% COMPLETE translation in HIGHLY NATURAL, EVERYDAY SPOKEN {language} ({tone} tone).\n"
                "CRITICAL RULES:\n"
                "1. DO NOT SHORTEN OR SUMMARIZE: Translate every single word, explanation, and dialogue. Leave absolutely nothing out.\n"
                "2. 100% NATURAL SPOKEN TONE (စကားပြောဟန်): Write exactly how a local Myanmar person naturally speaks in real life. STRICTLY AVOID formal, bookish, or robotic vocabulary.\n"
                "3. SMOOTH NATURAL PACING: The sentences must flow smoothly and naturally so that when spoken, they match the exact length and emotion of the original audio perfectly.\n"
                "4. Return ONLY the spoken narrative as a continuous text."
            )
        elif recap_type == "Silent Recap" or not has_audio:
            instruction = hook_instruction + (
                f"You are an expert storyteller. This video has NO dialogue. Watch the actions closely.\n"
                f"Create a detailed, highly engaging narrative story in EVERYDAY SPOKEN {language} ({tone} tone).\n"
                "CRITICAL RULES:\n"
                f"1. You MUST write between {min_words} and {max_words} Myanmar words.\n"
                "2. Tell the story naturally like you are explaining a movie to a friend (စကားပြောဟန်).\n"
                "3. Return ONLY the spoken narrative."
            )
        elif recap_type in ["Raw to Recap", "Raw to reacp"]:
            instruction = hook_instruction + (
                f"You are an energetic Movie Recapper (ဇာတ်လမ်းပြန်လည်ပြောပြသူ). Watch the visual frames AND listen to the audio carefully.\n"
                f"Provide a 100% COMPLETE, highly detailed recap in HIGHLY NATURAL, EVERYDAY SPOKEN {language} ({tone} tone).\n"
                "CRITICAL RULES:\n"
                "1. NO TIMESTAMPS OR TAGS: DO NOT write any timestamps, numbers, or tags like [ACTION] or [DIALOGUE]. Just write the pure spoken narrative as a continuous story.\n"
                "2. 100% EXACT COVERAGE (CRITICAL): You MUST describe every single detailed action AND translate EVERY SINGLE spoken dialogue. DO NOT SUMMARIZE. If a character speaks in the audio, you MUST narrate exactly what they said.\n"
                "3. RECAPPER PERSONA: You are an OUTSIDE NARRATOR (အပြင်လူ). DO NOT roleplay as the characters. When characters speak, weave their exact words into your third-person narration (e.g., 'မင်းသားက မင်းသမီးကို သူချစ်တယ်လို့ ပြောလိုက်ပါတယ်').\n"
                "4. EXCITING STORYTELLING: Describe the detailed actions and exact dialogues naturally like you are narrating an exciting movie scene-by-scene to a friend.\n"
                "5. 100% SPOKEN TONE: STRICTLY AVOID formal or bookish words. Use completely natural everyday Myanmar conversational tone (စကားပြောဟန်)."
            )


        parts.append({"text": instruction})
        
        # Raw to Recap အတွက် ၁ မိနစ် (၆၀ စက္ကန့်) သာ အချိန်ပေးမည်။ အခြားအမျိုးအစားများအတွက် ၃ မိနစ် (၁၈၀ စက္ကန့်) ပေးမည်။
        ai_timeout = 60 if recap_type in ["Raw to Recap", "Raw to reacp"] else 180
        
        res = _generate_content(api_key, parts, config={"temperature": 0.3}, timeout=ai_timeout)
        text = "\n".join(str(p.get("text") or "") for c in res.get("candidates", []) for p in c.get("content", {}).get("parts", [])).strip()
        if not text: raise RuntimeError("Script မပြန်လာပါ။")
        return text.strip()

    finally:
        if gemini_file and gemini_file.get("name"):
            delete_gemini_file(gemini_file["name"], api_key)
        if temp_audio_path and temp_audio_path.exists():
            try: temp_audio_path.unlink(missing_ok=True)
            except: pass
        if 'vocals_audio_path' in locals() and vocals_audio_path and vocals_audio_path.exists():
            try: vocals_audio_path.unlink(missing_ok=True)
            except: pass

def create_title_image(text: str, width: int, output_path: Path):
    import textwrap
    font_path = str(FONT_FILES.get("Pyidaungsu Bold", FONT_FILES.get("Noto Sans Myanmar")))
    font_size = max(45, int(width * 0.08))
    try: font = ImageFont.truetype(font_path, size=font_size)
    except: font = ImageFont.load_default()
    
    lines = textwrap.wrap(text, width=18)
    if len(lines) > 2: lines = lines[:2]
    
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    
    line_heights, line_widths = [], []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
        
    tw = max(line_widths) if line_widths else 0
    line_spacing = int(font_size * 0.25)
    th = sum(line_heights) + (len(lines) - 1) * line_spacing
    
    pad_x, pad_y = int(width * 0.1), int(font_size * 0.8)
    img_w, img_h = int(tw + pad_x * 2), int(th + pad_y * 2)
    
    bg_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_layer)
    bg_draw.rounded_rectangle([(pad_x//2, pad_y//2), (img_w - pad_x//2, img_h - pad_y//2)], radius=20, fill=(5, 10, 20, 230))
    bg_layer = bg_layer.filter(ImageFilter.GaussianBlur(radius=6))
    
    text_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    
    colors = ["#00FFFF", "#FFE600"] 
    current_y = pad_y
    stroke_w = max(2, int(font_size * 0.04))
    
    for i, line in enumerate(lines):
        lw = line_widths[i]
        lx = (img_w - lw) // 2
        color = colors[i % 2]
        
        for dx in range(-stroke_w, stroke_w+1):
            for dy in range(-stroke_w, stroke_w+1):
                text_draw.text((lx+dx, current_y+dy), line, font=font, fill="black")
                
        text_draw.text((lx+4, current_y+4), line, font=font, fill=(0,0,0,180))
        text_draw.text((lx, current_y), line, font=font, fill=color)
        
        current_y += line_heights[i] + line_spacing
        
    final_img = Image.alpha_composite(bg_layer, text_layer)
    
    target_width = width * 0.95
    if final_img.width > target_width:
        scale = target_width / final_img.width
        final_img = final_img.resize((int(final_img.width * scale), int(final_img.height * scale)), Image.Resampling.LANCZOS)
        
    final_img.save(output_path, "PNG")
    return output_path

def generate_thumbnail(video_path: Path, script: str, output_path: Path) -> Path | None:
    try:
        hook_text = ""
        lines = [line.strip() for line in script.strip().split('\n') if line.strip()]
        for line in lines[:5]:
            if line.startswith("TITLE:"):
                hook_text = line.replace("TITLE:", "").strip().strip("*\"'")
                break
        
        if not hook_text: return None
            
        extracted_frame = Path(tempfile.mktemp(suffix=".jpg"))
        cmd = ["ffmpeg", "-y", "-ss", "00:00:02", "-i", str(video_path), "-vframes", "1", "-q:v", "2", str(extracted_frame)]
        subprocess.run(cmd, capture_output=True, timeout=30)
        
        if not extracted_frame.exists(): return None
            
        img = Image.open(extracted_frame).convert("RGBA")
        iw, ih = img.size
        
        target_ratio = 3 / 4
        current_ratio = iw / ih
        if current_ratio > target_ratio:
            new_w = int(ih * target_ratio)
            left = (iw - new_w) // 2
            img = img.crop((left, 0, left + new_w, ih))
        else:
            new_h = int(iw / target_ratio)
            top = 0
            img = img.crop((0, top, iw, top + new_h))
            
        grad_layer = Image.new("RGBA", img.size, (0,0,0,0))
        grad_draw = ImageDraw.Draw(grad_layer)
        grad_h = int(img.height * 0.4)
        for y in range(grad_h):
            alpha = int(200 * (1 - (y / grad_h)**1.5))
            grad_draw.line([(0, y), (img.width, y)], fill=(5, 10, 20, alpha))
        img.alpha_composite(grad_layer, (0, 0))
        
        title_img_path = Path(tempfile.mktemp(suffix=".png"))
        create_title_image(hook_text, img.width, title_img_path)
        
        title_img = Image.open(title_img_path).convert("RGBA")
        paste_x = (img.width - title_img.width) // 2
        paste_y = int(img.height * 0.04) 
        img.alpha_composite(title_img, (paste_x, paste_y))
        
        img.convert("RGB").save(output_path, "JPEG", quality=95)
        extracted_frame.unlink(missing_ok=True)
        title_img_path.unlink(missing_ok=True)
        
        return output_path
    except Exception as e:
        print(f"Thumbnail Extraction Failed: {e}")
        return None

def clean_script_text(text: str) -> str:
    cleaned = re.sub(r'\(.*?\)|\[.*?\]|\{.*?\}', '', text)
    cleaned = re.sub(r'^[^:\-]{1,40}[:\-]\s*', '', cleaned)
    cleaned = re.sub(r'[\*\"\'_]+', '', cleaned)
    cleaned = re.sub(r'^[\-\s]+', '', cleaned)
    return cleaned.strip()

def generate_microsoft_tts(script: str, voice: str, speed_factor: float = 1.2) -> bytes:
    key = os.getenv("AZURE_SPEECH_KEY", "").strip()
    region = os.getenv("AZURE_SPEECH_REGION", "southeastasia").strip()
    if not key: raise RuntimeError("Microsoft Voice setting မသတ်မှတ်ရသေးပါ။")
    
    selected_voice = MICROSOFT_VOICES.get(voice, DEFAULT_MICROSOFT_VOICE)
    try: import azure.cognitiveservices.speech as speechsdk
    except ImportError: raise RuntimeError("azure-cognitiveservices-speech package ကို requirements.txt တွင် မတွေ့ပါ။")

    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)
    
    rate_pct = int((speed_factor - 1.0) * 100)
    rate_str = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"
    
    # အချိန်မှတ်ဖတ်သည့်စနစ် အသစ် (ပိုမိုတိကျစေရန်)
    pattern = r'\[\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:-\s*(\d{1,2}:\d{2}(?::\d{2})?))?\s*\]\s*(.*)'
    segments = []
    
    def parse_sec(t_str):
        parts = t_str.split(':')
        if len(parts) >= 3: return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
        if len(parts) == 2: return int(parts[0])*60 + int(parts[1])
        return 0

    for line in script.splitlines():
        line = line.strip()
        if not line or line.startswith("TITLE:") or line.startswith("COVER:"): continue
        m = re.match(pattern, line)
        if m:
            start_str, end_str, raw_text = m.groups()
            start_sec = parse_sec(start_str)
            
            if end_str:
                end_sec = parse_sec(end_str)
                target_dur_ms = max(100, (end_sec - start_sec) * 1000)
            else:
                target_dur_ms = 9999999 
                
            clean_text = clean_script_text(raw_text)
            if clean_text: segments.append((start_sec, target_dur_ms, clean_text))
            
    break_words = ["။", "၊", "သောအခါ", "အောင်", "၍", "ပြီး", "လို့", "သောကြောင့်", "မို့လို့", "သည်", "ပါ", "တယ်", "မယ်"]
    def chunk_words(boundaries):
        chunks, current_chunk = [], []
        for w in boundaries:
            current_chunk.append(w)
            text_so_far = " ".join([cw["word"] for cw in current_chunk])
            char_count = len(text_so_far.replace(" ", ""))
            if (char_count >= 10 and any(text_so_far.strip().endswith(bw) for bw in break_words)) or char_count > 18:
                chunks.append(current_chunk)
                current_chunk = []
        if current_chunk: chunks.append(current_chunk)
        return chunks
        
    def clock(ms: float) -> str:
        ms = int(max(0, ms))
        return f"{ms // 3_600_000:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"
        
    srt_lines = []
    CACHE_DIR = Path(tempfile.gettempdir()) / "srt_cache"
    CACHE_DIR.mkdir(exist_ok=True)
    h = hashlib.md5(script.encode('utf-8')).hexdigest()

    if not segments:
        clean_script = re.sub(r'^TITLE:.*$', '', script, flags=re.MULTILINE)
        clean_script = re.sub(r'^COVER:.*$', '', clean_script, flags=re.MULTILINE)
        clean_script = re.sub(r'\[\d{2}:\d{2}.*?\]', '', clean_script)
        clean_script = clean_script_text(clean_script)
        
        ssml = f'<speak version="1.0" xml:lang="my-MM" xmlns="http://www.w3.org/2001/10/synthesis"><voice name="{selected_voice}"><prosody rate="{rate_str}">{xml_escape(clean_script)}</prosody></voice></speak>'
        
        stream = speechsdk.audio.PullAudioOutputStream()
        audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        
        word_boundaries = []
        def word_boundary_cb(evt):
            dur_ms = evt.duration.total_seconds() * 1000 if hasattr(evt.duration, 'total_seconds') else float(evt.duration) / 10000
            word_boundaries.append({"word": evt.text, "offset_ms": evt.audio_offset / 10000, "duration_ms": dur_ms})
            
        synthesizer.synthesis_word_boundary.connect(word_boundary_cb)
        result = synthesizer.speak_ssml_async(ssml).get()
        
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            chunks = chunk_words(word_boundaries)
            for idx, chunk in enumerate(chunks):
                start_ms = chunk[0]["offset_ms"]
                end_ms = chunk[-1]["offset_ms"] + chunk[-1]["duration_ms"]
                text = " ".join([w["word"] for w in chunk])
                adjusted_end = end_ms - 50 if end_ms - start_ms > 200 else end_ms
                srt_lines.append(f"{idx + 1}\n{clock(start_ms)} --> {clock(adjusted_end)}\n{text.strip()}")
            (CACHE_DIR / f"{h}.srt").write_text("\n\n".join(srt_lines), encoding="utf-8")
            return result.audio_data
        else: raise RuntimeError(f"Microsoft Voice ဆာဗာ ချို့ယွင်းနေပါသည်။: {result.reason}")

    temp_dir = Path(tempfile.mkdtemp(prefix="tts_timeline_"))
    try:
        inputs = []
        filter_chains = []
        srt_index = 1
        current_v_time = 0.0
        current_a_time = 0.0
        timing_plan = []
        
        for idx, (start_sec, target_dur_ms, text) in enumerate(segments):
            ssml = f'<speak version="1.0" xml:lang="my-MM" xmlns="http://www.w3.org/2001/10/synthesis"><voice name="{selected_voice}"><prosody rate="{rate_str}">{xml_escape(text)}</prosody></voice></speak>'
            stream = speechsdk.audio.PullAudioOutputStream()
            audio_config = speechsdk.audio.AudioOutputConfig(stream=stream)
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
            
            word_boundaries = []
            def word_boundary_cb(evt, wb_list=word_boundaries):
                dur_ms = evt.duration.total_seconds() * 1000 if hasattr(evt.duration, 'total_seconds') else float(evt.duration) / 10000
                wb_list.append({"word": evt.text, "offset_ms": evt.audio_offset / 10000, "duration_ms": dur_ms})
                
            synthesizer.synthesis_word_boundary.connect(word_boundary_cb)
            result = synthesizer.speak_ssml_async(ssml).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_path = temp_dir / f"seg_{idx}.mp3"
                audio_path.write_bytes(result.audio_data)
                inputs.extend(["-i", str(audio_path)])
                
                requested_delay_ms = int(start_sec * 1000)
                total_dur_ms = word_boundaries[-1]["offset_ms"] + word_boundaries[-1]["duration_ms"] if word_boundaries else 0
                
                v_start = max(float(start_sec), current_v_time)
                v_end = max(start_sec + (target_dur_ms / 1000.0) if target_dur_ms != 9999999 else start_sec + 2.0, v_start + 0.5)
                gap_dur = v_start - current_v_time
                actual_delay_ms = int((current_a_time + gap_dur) * 1000)
                
                tempo = 1.0
                tempo_filter = ""
                orig_chunk_dur = v_end - v_start
                if total_dur_ms > (orig_chunk_dur * 1000):
                    tempo = min(1.5, total_dur_ms / (orig_chunk_dur * 1000))
                    tempo_filter = f"atempo={tempo:.4f},"
                    
                filter_chains.append(f"[{idx}:a]{tempo_filter}adelay={actual_delay_ms}:all=1[a{idx}]")
                
                actual_a_dur_ms = total_dur_ms / tempo
                out_chunk_dur = max(orig_chunk_dur, actual_a_dur_ms / 1000.0)
                
                timing_plan.append({"v_start": v_start, "v_end": v_end, "stretch_dur": out_chunk_dur})
                current_v_time = v_end
                current_a_time = (actual_delay_ms / 1000.0) + out_chunk_dur
                
                for chunk in chunk_words(word_boundaries):
                    start_ms = (chunk[0]["offset_ms"] / tempo) + actual_delay_ms
                    end_ms = ((chunk[-1]["offset_ms"] + chunk[-1]["duration_ms"]) / tempo) + actual_delay_ms
                    c_text = " ".join([w["word"] for w in chunk])
                    adjusted_end = end_ms - (50 / tempo) if end_ms - start_ms > (200 / tempo) else end_ms
                    srt_lines.append(f"{srt_index}\n{clock(start_ms)} --> {clock(adjusted_end)}\n{c_text.strip()}")
                    srt_index += 1
                    
        if not inputs: raise RuntimeError("Audio ဖန်တီး၍မရပါ။")
        (CACHE_DIR / f"{h}.srt").write_text("\n\n".join(srt_lines), encoding="utf-8")
        (CACHE_DIR / f"{h}_plan.json").write_text(json.dumps(timing_plan))
        
        out_path = temp_dir / "final_timeline.mp3"
        if len(filter_chains) == 1:
            filter_complex = f"{filter_chains[0]}"
            map_target = "[a0]"
        else:
            mix_count = len(filter_chains)
            filter_complex = ";".join(filter_chains) + ";" + "".join([f"[a{i}]" for i in range(mix_count)]) + f"amix=inputs={mix_count}:duration=longest:dropout_transition=0[amixed_out];[amixed_out]volume={mix_count}[out]"
            map_target = "[out]"
            
        cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, "-map", map_target, str(out_path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0: raise RuntimeError(f"Audio Merge Failed: {res.stderr}")
        
        return out_path.read_bytes()
        
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def generate_gemini_tts(script: str, voice: str, api_key: str) -> bytes:
    if not api_key: raise RuntimeError("Gemini API Key မရှိပါ။")
    clean_script = re.sub(r'^TITLE:.*$', '', script, flags=re.MULTILINE).strip()
    clean_script = re.sub(r'^COVER:.*$', '', clean_script, flags=re.MULTILINE).strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
    payload = {"contents": [{"role": "user", "parts": [{"text": clean_script}]}], "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    response = requests.post(url, json=payload, timeout=(10, 120))
    if not response.ok:
        try:
            error_obj = response.json().get("error", {})
            detail = _gemini_error_myanmar(
                response.status_code,
                error_obj.get("status", ""),
                error_obj.get("message", response.text[:400]),
            )
        except Exception:
            detail = _gemini_error_myanmar(response.status_code)
        raise RuntimeError(f"အသံထုတ်လုပ်မှု မအောင်မြင်ပါ။ {detail}")
    try:
        parts = response.json()["candidates"][0]["content"]["parts"]
        audio_part = next(part for part in parts if "inlineData" in part)
        return base64.b64decode(audio_part["inlineData"]["data"])
    except Exception as e:
        raise RuntimeError("Gemini မှ အသံဖိုင်ပြန်မလာပါ။") from e

def make_srt(script: str, duration: float) -> str:
    clean_script = re.sub(r'^TITLE:.*$', '', script, flags=re.MULTILINE)
    clean_script = re.sub(r'^COVER:.*$', '', clean_script, flags=re.MULTILINE)
    clean_script = re.sub(r'\[\d{2}:\d{2}.*?\]', '', clean_script)
    clean_script = clean_script_text(clean_script)
    cleaned = clean_script.strip()
    if not cleaned: return ""

    h = hashlib.md5(script.encode('utf-8')).hexdigest()
    cache_file = Path(tempfile.gettempdir()) / "srt_cache" / f"{h}.srt"
    if cache_file.exists(): return cache_file.read_text(encoding="utf-8")
    
    words = cleaned.replace('\n', ' ').split(' ')
    chunks, current_chunk = [], ""
    break_words = ["။", "၊", "သောအခါ", "အောင်", "၍", "ပြီး", "လို့", "သောကြောင့်", "မို့လို့", "သည်", "ပါ", "တယ်", "မယ်"]
    
    for word in words:
        if not word.strip(): continue
        current_chunk += word + " "
        char_count = len(current_chunk.replace(" ", ""))
        if (char_count >= 10 and any(current_chunk.strip().endswith(bw) for bw in break_words)) or char_count > 18:
            chunks.append(current_chunk.strip())
            current_chunk = ""
    if current_chunk.strip(): chunks.append(current_chunk.strip())

    total_weight = sum([len(c.replace(" ", "")) * 1.5 + (20 if c.endswith("။") else 10 if c.endswith("၊") else 0) for c in chunks])
    if total_weight == 0: return ""

    def clock(value: float) -> str:
        ms = int(max(0, value) * 1000)
        return f"{ms // 3_600_000:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"

    srt_output, current_time = [], 0.0
    for index, (line, weight) in enumerate(zip(chunks, [len(c.replace(" ", "")) * 1.5 + (20 if c.endswith("။") else 10 if c.endswith("၊") else 0) for c in chunks])):
        piece_duration = (weight / total_weight) * duration
        end_time = current_time + piece_duration
        adjusted_end = end_time - 0.05 if end_time - current_time > 0.2 else end_time
        srt_output.append(f"{index + 1}\n{clock(current_time)} --> {clock(adjusted_end)}\n{line.strip()}")
        current_time = end_time
    return "\n\n".join(srt_output)

def ass_color(value: str, alpha: int = 0) -> str:
    color = re.sub(r"[^0-9a-fA-F]", "", value or "")[-6:].rjust(6, "F")
    return f"&H{max(0, min(255, alpha)):02X}{color[4:6]}{color[2:4]}{color[:2]}"

def fitted_source_rect(source: dict[str, float | int], out_width: int, out_height: int) -> dict[str, int]:
    scale = min(out_width / max(1, int(source["width"])), out_height / max(1, int(source["height"])))
    w, h = max(2, round(max(1, int(source["width"])) * scale)), max(2, round(max(1, int(source["height"])) * scale))
    return {"x": (out_width - w) // 2, "y": (out_height - h) // 2, "width": w, "height": h}

def srt_to_ass(srt: str, settings: dict[str, Any], width: int, height: int, source_rect: dict[str, int] | None = None, speed_factor: float = 1.0) -> str:
    scale_factor = (height / 720.0)
    font_size = max(20, min(80, round(float(settings.get("subtitle_size", 20)) * 1.8 * scale_factor)))
    visible = source_rect or {"x": 0, "y": 0, "width": width, "height": height}
    x = int(visible["x"]) + math.floor(int(visible["width"]) * max(0, min(100, int(settings.get("subtitle_x", 50)))) / 100 + 0.5)
    y = int(visible["y"]) + math.floor(int(visible["height"]) * max(0, min(100, int(settings.get("subtitle_y", 82)))) / 100 + 0.5)
    
    solid_bg = str(settings.get("subtitle_background", "solid")).strip().lower() in {"solid", "solid background"}
    back_alpha = 255 - round(max(0, min(100, int(settings.get("subtitle_opacity", 70)))) * 255 / 100)
    font = FONT_FAMILIES.get(str(settings.get("subtitle_font", "Noto Sans Myanmar")), "Noto Sans Myanmar")
    
    outline_c, primary_c, bg_c = ass_color(str(settings.get('subtitle_outline', '#000000'))), ass_color(str(settings.get('subtitle_color', '#FFD166'))), ass_color(str(settings.get('subtitle_background_color', '#000000')), back_alpha)
    
    header = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "", "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding"]
    
    if solid_bg:
        header.append(f"Style: BoxStyle,{font},{font_size},&HFF000000,&HFF000000,{bg_c},{bg_c},-1,0,0,0,100,100,0,0,3,3,0,2,20,20,20,1")
    header.append(f"Style: TextStyle,{font},{font_size},{primary_c},{primary_c},{outline_c},&H00000000,-1,0,0,0,100,100,0,0,1,3,2,2,20,20,20,1")
    header.extend(["", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"])
    
    events = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3 or "-->" not in lines[1]: continue
        start, end = (p.strip() for p in lines[1].split("-->", 1))
        try:
            def convert(value: str) -> str:
                h, m, s_ms = value.split(":")
                s, ms = s_ms.split(",")
                new_ms = int((int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)) / speed_factor)
                return f"{new_ms // 3600000}:{(new_ms // 60000) % 60:02d}:{(new_ms // 1000) % 60:02d}.{(new_ms % 1000) // 10:02d}"
            caption = " ".join(lines[2:]).replace("{", "\\{").replace("}", "\\}")
            if solid_bg: events.append(f"Dialogue: 0,{convert(start)},{convert(end)},BoxStyle,,0,0,0,,{{\\an5\\pos({x},{y})}}{caption}")
            events.append(f"Dialogue: 1,{convert(start)},{convert(end)},TextStyle,,0,0,0,,{{\\an5\\pos({x},{y})}}{caption}")
        except ValueError: continue
    return "\n".join(header + events) + "\n"

def create_text_image(text: str, font_path: Path, output_path: Path, font_size: int):
    if not text or not str(text).strip():
        text = "Logo"
        
    try: 
        font = ImageFont.truetype(str(font_path), font_size)
    except Exception as e: 
        # ဖောင့်မရှိပါက ရိုးရိုးဖောင့်မသုံးဘဲ Error ချက်ချင်းပြမည်
        raise RuntimeError(f"❌ Error: ဆာဗာပေါ်တွင် ဖောင့်ဖိုင် ({font_path.name}) ကို ရှာမတွေ့ပါ။ static/fonts ဖိုင်တွဲကို စစ်ဆေးပါ။")
        
    dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw_dummy = ImageDraw.Draw(dummy)
    
    try:
        bbox = draw_dummy.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw_dummy.textsize(text, font=font)
        
    pad_x, pad_y = 10, 10
    img_w = int(tw + pad_x * 2)
    img_h = int(th + pad_y * 2)
    
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(1, int(font_size * 0.05))
    
    for dx in range(-stroke, stroke+1):
        for dy in range(-stroke, stroke+1):
            draw.text((pad_x+dx, pad_y+dy), text, font=font, fill="black")
            
    draw.text((pad_x, pad_y), text, font=font, fill="white")
    img.save(output_path, "PNG")

def apply_voice_clone(base_audio_bytes: bytes, target_audio_path: str) -> bytes:
    """Colab ဆာဗာသို့ အသံပို့၍ ပွားသည့်စနစ်"""
    from pathlib import Path
    import requests
    import os

    if not target_audio_path or not Path(target_audio_path).exists():
        return base_audio_bytes
        
    # ဒီနေရာမှာ ခုနက Colab က ရလာတဲ့ ngrok လင့်ခ်ကို ထည့်ပါ
    api_url = "https://undergrad-dealt-compress.ngrok-free.dev/clone"
    
    try:
        with open(target_audio_path, 'rb') as target_file:
            files = {
                'base_audio': ('base.mp3', base_audio_bytes, 'audio/mpeg'),
                'target_audio': ('target.mp3', target_file, 'audio/mpeg')
            }
            # Colab ဆီသို့ လှမ်းပို့ခြင်း
            response = requests.post(api_url, files=files, timeout=180)
            
        if response.ok:
            return response.content
        else:
            return base_audio_bytes
    except:
        return base_audio_bytes

def render_true_movie_dub(video_path: Path, tts_wav: bytes, settings: dict[str, Any], output_path: Path) -> Path:
    import tempfile, shutil
    source = probe_video(video_path)
    aspect = str(settings.get("aspect", "9:16"))
    scale = 854
    out_w, out_h = (round(scale * 16 / 9), scale) if aspect == "16:9" else (scale, scale) if aspect == "1:1" else (scale, round(scale * 16 / 9))
    out_w -= out_w % 2; out_h -= out_h % 2
    visible_rect = fitted_source_rect(source, out_w, out_h)
    
    temp_dir = Path(tempfile.mkdtemp(prefix="true_dub_"))
    raw_tts_path = temp_dir / "full_tts.mp3"
    raw_tts_path.write_bytes(tts_wav)
    
    inputs = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(raw_tts_path)]
    
    try:
        # --- Visual Filters (ရုပ်ထွက်ပိုင်းအတွက် Blur, Logo, Subtitles သီးသန့်ပေါင်းထည့်ခြင်း) ---
        v_filters = [f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"]
        graph = f"[0:v]{','.join(v_filters)}[v0]"
        current_v = "v0"
        
        # 1. Blur စနစ် ထည့်သွင်းခြင်း
        if settings.get("blur_enabled"):
            raw_b = int(settings.get("blur_strength", 10))
            luma_r, chroma_r = max(1, min(20, raw_b)), max(1, min(14, raw_b // 2 if raw_b > 2 else 1))
            for idx, mask in enumerate(settings.get("blur_masks", [])):
                vx, vy, vw, vh = visible_rect["x"], visible_rect["y"], visible_rect["width"], visible_rect["height"]
                bx, by = max(1, vx + int(float(mask["x"]) * vw)), max(1, vy + int(float(mask["y"]) * vh))
                bw, bh = max(1, min(int(float(mask["width"]) * vw), out_w - bx - 1)), max(1, min(int(float(mask["height"]) * vh), out_h - by - 1))
                next_v = f"blur{idx}"
                graph += f";[{current_v}]split=2[bg{idx}][toblur{idx}];[toblur{idx}]crop={bw}:{bh}:{bx}:{by},boxblur={luma_r}:{chroma_r}[blurred{idx}];[bg{idx}][blurred{idx}]overlay={bx}:{by}[{next_v}]"
                current_v = next_v

        if settings.get("logo_type") == "moving":
            import time
            user_font = str(settings.get("logo_font", "Beloved Bunny"))
            target_font_path = FONT_FILES.get(user_font)
            
            # ဖောင့်ဖိုင် အမှန်တကယ် ရှိမရှိ စစ်ဆေးမည် (မရှိပါက Error ချက်ချင်းပြမည်)
            if not target_font_path or not target_font_path.exists():
                raise RuntimeError(f"❌ Error: ဆာဗာပေါ်တွင် ဖောင့်ဖိုင် ({user_font}) ကို ရှာမတွေ့ပါ။ fonts ဖိုင်တွဲကို စစ်ဆေးပါ။")
                
            try: safe_font = temp_dir / f"safe_font_{int(time.time() * 1000)}.ttf"
            except NameError: safe_font = Path(tempfile.gettempdir()) / f"safe_font_{int(time.time() * 1000)}.ttf"
            
            shutil.copy2(target_font_path, safe_font)
            font_path = str(safe_font).replace('\\', '/').replace(':', r'\:')
            
            run_text = str(settings.get("logo_text", "")).strip()
            if not run_text: run_text = "Test Logo"
            safe_text = run_text.replace("'", "\u2019").replace(":", r"\:")

            next_v = "vtestlogo"
            graph += f";[{current_v}]drawtext=fontfile='{font_path}':text='{safe_text}':x='abs(w-tw-abs(mod(t*150,max(1,(w-tw)*2))-(w-tw)))':y='abs(h-th-abs(mod(t*100,max(1,(h-th)*2))-(h-th)))':fontsize=h*0.05:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2[{next_v}]"
            current_v = next_v

        elif settings.get("logo_type", "static") == "static":
            logo_str = settings.get("logo_path") or settings.get("watermark_path")
            logo_text = str(settings.get("logo_text", "")).strip()
            lx_pct, ly_pct = max(0, min(100, int(settings.get("logo_x", 84)))) / 100.0, max(0, min(100, int(settings.get("logo_y", 86)))) / 100.0
            l_size = max(10, min(100, int(settings.get("logo_size", 22)))) / 100.0
            target_w = int(out_w * l_size)

            if logo_str and Path(str(logo_str)).exists():
                inputs.extend(["-i", str(logo_str)])
                next_v = "vlogo"
                graph += f";[{inputs.count('-i') - 1}:v]scale={target_w}:-1[wm];[{current_v}][wm]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                current_v = next_v
            elif logo_text:
                try: text_img_path = temp_dir / "logo.png"
                except NameError: 
                    text_img_path = Path(tempfile.mktemp(suffix=".png"))
                    try: temp_paths.append(text_img_path)
                    except NameError: pass
                    
                user_font = str(settings.get("logo_font", "Beloved Bunny"))
                font_path_to_use = FONT_FILES.get(user_font)
                
                # ဖောင့်ဖိုင် အမှန်တကယ် ရှိမရှိ စစ်ဆေးမည် (မရှိပါက Error ချက်ချင်းပြမည်)
                if not font_path_to_use or not font_path_to_use.exists():
                    raise RuntimeError(f"❌ Error: ဆာဗာပေါ်တွင် ဖောင့်ဖိုင် ({user_font}) ကို ရှာမတွေ့ပါ။ fonts ဖိုင်တွဲကို စစ်ဆေးပါ။")
                    
                create_text_image(logo_text, font_path_to_use, text_img_path, int(out_h * l_size * 0.3))
                
                inputs.extend(["-i", str(text_img_path)])
                next_v = "vlogo"
                graph += f";[{current_v}][{inputs.count('-i') - 1}:v]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                current_v = next_v
                
        # 3. စာတန်းထိုး (Subtitles) စနစ် ထည့်သွင်းခြင်း
        if settings.get("subtitles", True):
            raw_tts_dur = get_audio_duration(raw_tts_path)
            srt = str(settings.get("subtitle_srt") or make_srt(str(settings.get("script") or ""), raw_tts_dur))
            ass_path = temp_dir / "sub.ass"
            ass_path.write_text(srt_to_ass(srt, settings, out_w, out_h, visible_rect, 1.0), encoding="utf-8", newline="\n")
            font_dir = temp_dir / "fonts"
            font_dir.mkdir(exist_ok=True)
            shutil.copy2(FONT_FILES.get(str(settings.get("subtitle_font")), FONT_FILES["Noto Sans Myanmar"]), font_dir)
            next_v = "vsub"
            graph += f";[{current_v}]ass=filename='{str(ass_path).replace(':', r'\:')}':fontsdir='{str(font_dir).replace(':', r'\:')}'[{next_v}]"
            current_v = next_v

        # --- Audio (သင်ရေးထားသော မူလအဟောင်းအတိုင်း လုံးဝမထိပါ) ---
        sfx_only_path = None
        enable_sfx = settings.get("enable_sfx", True)
        if enable_sfx:
            try:
                sfx_only_path = get_background_audio_only(video_path, temp_dir)
            except Exception as e:
                print(f"VPS Demucs SFX Error: {e}")
        
        if sfx_only_path and sfx_only_path.exists():
            inputs.extend(["-i", str(sfx_only_path)])
            user_sfx_vol = float(settings.get("sfx_volume", 85)) / 100.0
            sfx_idx = inputs.count("-i") - 1
            graph += (
                f";[1:a]volume=1.0[tts];"
                f"[{sfx_idx}:a]volume={user_sfx_vol}[sfx];"
                f"[tts][sfx]amix=inputs=2:duration=longest:dropout_transition=2[amixed];"
                f"[amixed]volume=2.0[a]"
            )
        else:
            graph += f";[1:a]volume=1.0[a]"
        
        cmd = inputs + ["-filter_complex", graph, "-map", f"[{current_v}]", "-map", "[a]", "-c:v", "libx264", "-preset", "faster", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path)]
        _run(cmd, timeout=2400)
        return output_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def render_raw_to_recap(video_path: Path, tts_wav: bytes, settings: dict[str, Any], output_path: Path) -> Path:
    import tempfile, shutil
    source = probe_video(video_path)
    aspect = str(settings.get("aspect", "9:16"))
    scale = 854
    out_w, out_h = (round(scale * 16 / 9), scale) if aspect == "16:9" else (scale, scale) if aspect == "1:1" else (scale, round(scale * 16 / 9))
    out_w -= out_w % 2; out_h -= out_h % 2
    visible_rect = fitted_source_rect(source, out_w, out_h)
    
    temp_dir = Path(tempfile.mkdtemp(prefix="raw_recap_"))
    raw_tts_path = temp_dir / "full_tts.mp3"
    raw_tts_path.write_bytes(tts_wav)
    
    inputs = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(raw_tts_path)]
    
    try:
        script_text = str(settings.get("script", ""))
        if not script_text.strip():
            raise RuntimeError("❌ Error: ဇာတ်ညွှန်း (Script) မတွေ့ရှိပါ။")

        vid_dur = float(source["duration"])
        raw_tts_dur = get_audio_duration(raw_tts_path)
        
        # =======================================================
        # အဆင့် ၁: ရုပ်နှင့် အသံကို Normal Speed ဖြင့် တပြေးညီ ချိန်ညှိခြင်း (Base Sync)
        # =======================================================
        base_dur = max(vid_dur, raw_tts_dur)
        v_sync_factor = base_dur / vid_dur if vid_dur > 0 else 1.0

        # --- Visual Filters ---
        aesthetic_filters = [f"setpts={v_sync_factor:.5f}*PTS-STARTPTS", "fps=30"]
        if settings.get("mirror"): aesthetic_filters.append("hflip")
        if settings.get("zoom_enabled"): aesthetic_filters.append("crop=iw*0.96:ih*0.96")
        v_filter = settings.get("video_filter", "None")
        if v_filter == "HD Cam 2": aesthetic_filters.append("eq=brightness=0.02:saturation=1.1:contrast=1.15:gamma=0.95")
        elif v_filter == "Glow": aesthetic_filters.append("eq=brightness=0.06:saturation=1.2:contrast=1.05")
        aesthetic_filters.append("unsharp=5:5:0.8:3:3:0.4")
        
        if settings.get("video_bg_blur"):
            aesthetic_filters.append(f"split=2[bg][fg];[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=20:10[bg_blurred];[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fg_scaled];[bg_blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2")
        else:
            aesthetic_filters.extend([f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"])
            
        graph = f"[0:v]{','.join(aesthetic_filters)}[v0]"
        current_v = "v0"

        # --- Blurs ---
        if settings.get("blur_enabled"):
            raw_b = int(settings.get("blur_strength", 10))
            luma_r, chroma_r = max(1, min(20, raw_b)), max(1, min(14, raw_b // 2 if raw_b > 2 else 1))
            for idx, mask in enumerate(settings.get("blur_masks", [])):
                vx, vy, vw, vh = visible_rect["x"], visible_rect["y"], visible_rect["width"], visible_rect["height"]
                bx, by = max(1, vx + int(float(mask["x"]) * vw)), max(1, vy + int(float(mask["y"]) * vh))
                bw, bh = max(1, min(int(float(mask["width"]) * vw), out_w - bx - 1)), max(1, min(int(float(mask["height"]) * vh), out_h - by - 1))
                next_v = f"blur{idx}"
                graph += f";[{current_v}]split=2[bg{idx}][toblur{idx}];[toblur{idx}]crop={bw}:{bh}:{bx}:{by},boxblur={luma_r}:{chroma_r}[blurred{idx}];[bg{idx}][blurred{idx}]overlay={bx}:{by}[{next_v}]"
                current_v = next_v

        # --- Logos ---
        if settings.get("logo_type") == "moving":
            import time
            user_font = str(settings.get("logo_font", "Beloved Bunny"))
            target_font_path = FONT_FILES.get(user_font)
            if target_font_path and target_font_path.exists():
                safe_font = temp_dir / f"safe_font_{int(time.time() * 1000)}.ttf"
                shutil.copy2(target_font_path, safe_font)
                font_path = str(safe_font).replace('\\', '/').replace(':', r'\:')
                run_text = str(settings.get("logo_text", "Test Logo")).strip()
                safe_text = run_text.replace("'", "\u2019").replace(":", r"\:")
                next_v = "vtestlogo"
                graph += f";[{current_v}]drawtext=fontfile='{font_path}':text='{safe_text}':x='abs(w-tw-abs(mod(t*150,max(1,(w-tw)*2))-(w-tw)))':y='abs(h-th-abs(mod(t*100,max(1,(h-th)*2))-(h-th)))':fontsize=h*0.05:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2[{next_v}]"
                current_v = next_v

        elif settings.get("logo_type", "static") == "static":
            logo_str = settings.get("logo_path") or settings.get("watermark_path")
            logo_text = str(settings.get("logo_text", "")).strip()
            lx_pct, ly_pct = max(0, min(100, int(settings.get("logo_x", 84)))) / 100.0, max(0, min(100, int(settings.get("logo_y", 86)))) / 100.0
            l_size = max(10, min(100, int(settings.get("logo_size", 22)))) / 100.0
            target_w = int(out_w * l_size)

            if logo_str and Path(str(logo_str)).exists():
                inputs.extend(["-i", str(logo_str)])
                next_v = "vlogo"
                graph += f";[{inputs.count('-i') - 1}:v]scale={target_w}:-1[wm];[{current_v}][wm]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                current_v = next_v
            elif logo_text:
                text_img_path = temp_dir / "logo.png"
                user_font = str(settings.get("logo_font", "Beloved Bunny"))
                font_path_to_use = FONT_FILES.get(user_font)
                if font_path_to_use and font_path_to_use.exists():
                    create_text_image(logo_text, font_path_to_use, text_img_path, int(out_h * l_size * 0.3))
                    inputs.extend(["-i", str(text_img_path)])
                    next_v = "vlogo"
                    graph += f";[{current_v}][{inputs.count('-i') - 1}:v]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                    current_v = next_v

        # --- Subtitles (SRT generated for Base Duration) ---
        if settings.get("subtitles", True):
            srt = str(settings.get("subtitle_srt") or make_srt(script_text, base_dur))
            ass_path = temp_dir / "sub.ass"
            ass_path.write_text(srt_to_ass(srt, settings, out_w, out_h, visible_rect, 1.0), encoding="utf-8", newline="\n")
            font_dir = temp_dir / "fonts"
            font_dir.mkdir(exist_ok=True)
            shutil.copy2(FONT_FILES.get(str(settings.get("subtitle_font")), FONT_FILES["Noto Sans Myanmar"]), font_dir)
            next_v = "vsub"
            graph += f";[{current_v}]ass=filename='{str(ass_path).replace(':', r'\:')}':fontsdir='{str(font_dir).replace(':', r'\:')}'[{next_v}]"
            current_v = next_v

        # --- Audio (Base Normal Speed Mix) ---
        a_filters = ["[1:a]aresample=44100,aformat=channel_layouts=stereo,atempo=1.0[tts]"]
        current_a_mix = "tts"
        
        bgm_path_str = settings.get("music_path")
        if bool(bgm_path_str) and Path(str(bgm_path_str)).exists():
            inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path_str)])
            a_filters.append(f"[{inputs.count('-i') - 1}:a]aresample=44100,aformat=channel_layouts=stereo,volume={float(settings.get('music_volume', 24)) / 100.0},atempo=1.0[bgm_a]")
            a_filters.append(f"[{current_a_mix}][bgm_a]amix=inputs=2:duration=longest:dropout_transition=2[amixed];[amixed]volume=2.0[a_base]")
            current_a = "a_base"
        else:
            a_filters.append(f"[{current_a_mix}]volume=1.0[a_base]")
            current_a = "a_base"

        # =======================================================
        # 📌 အဆင့် ၂: CapCut စတိုင် (Global Speed Compression)
        # UI ကိုကျော်ပြီး 1.5x (တစ်ဆခွဲ) ဖြင့် အလိုအလျောက် အသေထား၍ တင်မည်
        # =======================================================
        speed_float = 1.5
        
        # 1. Video Speed ဖိချုံ့ခြင်း (Subtitles များပါ ကပ်ပြီးသားဖြစ်၍ အတူတူမြန်သွားမည်)
        v_final_pts = 1.0 / speed_float
        next_v = "v_final"
        a_filters.append(f"[{current_v}]setpts={v_final_pts:.5f}*PTS[{next_v}]")
        current_v = next_v
        
        # 2. Audio Speed ဖိချုံ့ခြင်း (atempo)
        t_rem = speed_float
        t_filters = []
        while t_rem > 2.0:
            t_filters.append("atempo=2.0")
            t_rem /= 2.0
        while t_rem < 0.5:
            t_filters.append("atempo=0.5")
            t_rem /= 0.5
        t_filters.append(f"atempo={t_rem:.5f}")
        
        next_a = "a_final"
        a_filters.append(f"[{current_a}]{','.join(t_filters)}[{next_a}]")
        current_a = next_a
        
        final_video_duration = base_dur / speed_float

        graph += ";" + ";".join(a_filters)

        # --- Final Render ---
        cmd = inputs + [
            "-filter_complex", graph, 
            "-map", f"[{current_v}]", "-map", f"[{current_a}]", 
            "-c:v", "libx264", "-preset", "faster", "-crf", "26", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-t", f"{final_video_duration:.3f}", "-movflags", "+faststart", str(output_path)
        ]
        
        _run(cmd, timeout=2400)
        return output_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
def render_movie_subtitle(video_path: Path, script_text: str, settings: dict[str, Any], output_path: Path) -> Path:
    import tempfile, shutil
    source = probe_video(video_path)
    aspect = str(settings.get("aspect", "9:16"))
    scale = 854
    out_w, out_h = (round(scale * 16 / 9), scale) if aspect == "16:9" else (scale, scale) if aspect == "1:1" else (scale, round(scale * 16 / 9))
    out_w -= out_w % 2; out_h -= out_h % 2
    visible_rect = fitted_source_rect(source, out_w, out_h)
    
    temp_dir = Path(tempfile.mkdtemp(prefix="movie_sub_"))
    inputs = ["ffmpeg", "-y", "-i", str(video_path)]
    
    try:
        v_filters = [f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"]
        graph = f"[0:v]{','.join(v_filters)}[v0]"
        current_v = "v0"
        
        # 1. Blur
        if settings.get("blur_enabled"):
            raw_b = int(settings.get("blur_strength", 10))
            luma_r, chroma_r = max(1, min(20, raw_b)), max(1, min(14, raw_b // 2 if raw_b > 2 else 1))
            for idx, mask in enumerate(settings.get("blur_masks", [])):
                vx, vy, vw, vh = visible_rect["x"], visible_rect["y"], visible_rect["width"], visible_rect["height"]
                bx, by = max(1, vx + int(float(mask["x"]) * vw)), max(1, vy + int(float(mask["y"]) * vh))
                bw, bh = max(1, min(int(float(mask["width"]) * vw), out_w - bx - 1)), max(1, min(int(float(mask["height"]) * vh), out_h - by - 1))
                next_v = f"blur{idx}"
                graph += f";[{current_v}]split=2[bg{idx}][toblur{idx}];[toblur{idx}]crop={bw}:{bh}:{bx}:{by},boxblur={luma_r}:{chroma_r}[blurred{idx}];[bg{idx}][blurred{idx}]overlay={bx}:{by}[{next_v}]"
                current_v = next_v
                
        if settings.get("logo_type") == "moving":
            import time
            user_font = str(settings.get("logo_font", "Beloved Bunny"))
            target_font_path = FONT_FILES.get(user_font)
            
            # ဖောင့်ဖိုင် အမှန်တကယ် ရှိမရှိ စစ်ဆေးမည် (မရှိပါက Error ချက်ချင်းပြမည်)
            if not target_font_path or not target_font_path.exists():
                raise RuntimeError(f"❌ Error: ဆာဗာပေါ်တွင် ဖောင့်ဖိုင် ({user_font}) ကို ရှာမတွေ့ပါ။ fonts ဖိုင်တွဲကို စစ်ဆေးပါ။")
                
            try: safe_font = temp_dir / f"safe_font_{int(time.time() * 1000)}.ttf"
            except NameError: safe_font = Path(tempfile.gettempdir()) / f"safe_font_{int(time.time() * 1000)}.ttf"
            
            shutil.copy2(target_font_path, safe_font)
            font_path = str(safe_font).replace('\\', '/').replace(':', r'\:')
            
            run_text = str(settings.get("logo_text", "")).strip()
            if not run_text: run_text = "Test Logo"
            safe_text = run_text.replace("'", "\u2019").replace(":", r"\:")

            next_v = "vtestlogo"
            graph += f";[{current_v}]drawtext=fontfile='{font_path}':text='{safe_text}':x='abs(w-tw-abs(mod(t*150,max(1,(w-tw)*2))-(w-tw)))':y='abs(h-th-abs(mod(t*100,max(1,(h-th)*2))-(h-th)))':fontsize=h*0.05:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2[{next_v}]"
            current_v = next_v

        elif settings.get("logo_type", "static") == "static":
            logo_str = settings.get("logo_path") or settings.get("watermark_path")
            logo_text = str(settings.get("logo_text", "")).strip()
            lx_pct, ly_pct = max(0, min(100, int(settings.get("logo_x", 84)))) / 100.0, max(0, min(100, int(settings.get("logo_y", 86)))) / 100.0
            l_size = max(10, min(100, int(settings.get("logo_size", 22)))) / 100.0
            target_w = int(out_w * l_size)

            if logo_str and Path(str(logo_str)).exists():
                inputs.extend(["-i", str(logo_str)])
                next_v = "vlogo"
                graph += f";[{inputs.count('-i') - 1}:v]scale={target_w}:-1[wm];[{current_v}][wm]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                current_v = next_v
            elif logo_text:
                try: text_img_path = temp_dir / "logo.png"
                except NameError: 
                    text_img_path = Path(tempfile.mktemp(suffix=".png"))
                    try: temp_paths.append(text_img_path)
                    except NameError: pass
                    
                user_font = str(settings.get("logo_font", "Beloved Bunny"))
                font_path_to_use = FONT_FILES.get(user_font)
                
                # ဖောင့်ဖိုင် အမှန်တကယ် ရှိမရှိ စစ်ဆေးမည် (မရှိပါက Error ချက်ချင်းပြမည်)
                if not font_path_to_use or not font_path_to_use.exists():
                    raise RuntimeError(f"❌ Error: ဆာဗာပေါ်တွင် ဖောင့်ဖိုင် ({user_font}) ကို ရှာမတွေ့ပါ။ fonts ဖိုင်တွဲကို စစ်ဆေးပါ။")
                    
                create_text_image(logo_text, font_path_to_use, text_img_path, int(out_h * l_size * 0.3))
                
                inputs.extend(["-i", str(text_img_path)])
                next_v = "vlogo"
                graph += f";[{current_v}][{inputs.count('-i') - 1}:v]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
                current_v = next_v
        
        # 3. Subtitle
        if settings.get("subtitles", True):
            def parse_script_to_srt(script: str, default_duration: float) -> str:
                pattern = r'\[(\d{2}):(\d{2})(?:\s*-\s*(\d{2}):(\d{2}))?\]\s*(.*)'
                srt_lines, srt_idx = [], 1
                for line in script.splitlines():
                    line = line.strip()
                    if not line or line.startswith("TITLE:") or line.startswith("COVER:"): continue
                    m = re.match(pattern, line)
                    if m:
                        sm, ss, em, es, text = m.groups()
                        start_ms = (int(sm) * 60 + int(ss)) * 1000
                        if em and es: end_ms = (int(em) * 60 + int(es)) * 1000
                        else: end_ms = start_ms + 2500
                        def clock(ms): return f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"
                        srt_lines.append(f"{srt_idx}\n{clock(start_ms)} --> {clock(end_ms)}\n{text.strip()}")
                        srt_idx += 1
                return "\n\n".join(srt_lines) if srt_lines else make_srt(script, default_duration)
                
            srt = parse_script_to_srt(script_text, source["duration"])
            ass_path = temp_dir / "sub.ass"
            ass_path.write_text(srt_to_ass(srt, settings, out_w, out_h, visible_rect, 1.0), encoding="utf-8", newline="\n")
            font_dir = temp_dir / "fonts"
            font_dir.mkdir(exist_ok=True)
            shutil.copy2(FONT_FILES.get(str(settings.get("subtitle_font")), FONT_FILES["Noto Sans Myanmar"]), font_dir)
            next_v = "vsub"
            graph += f";[{current_v}]ass=filename='{str(ass_path).replace(':', r'\:')}':fontsdir='{str(font_dir).replace(':', r'\:')}'[{next_v}]"
            current_v = next_v
            
        # 4. Final Render
        if source.get("has_audio"):
            cmd = inputs + ["-filter_complex", graph, "-map", f"[{current_v}]", "-map", "0:a", "-c:v", "libx264", "-preset", "faster", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path)]
        else:
            cmd = inputs + ["-filter_complex", graph, "-map", f"[{current_v}]", "-c:v", "libx264", "-preset", "faster", "-crf", "26", "-movflags", "+faststart", str(output_path)]
            
        _run(cmd, timeout=2400)
        return output_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
def render_final(video_path: Path, tts_wav: bytes, settings: dict[str, Any], output_path: Path) -> Path:
    recap_type = str(settings.get("recapType", settings.get("recap_type", "")))
    if recap_type == "Movie Dub":
        return render_true_movie_dub(video_path, tts_wav, settings, output_path)
    elif recap_type == "Movie Subtitle":
        # Movie Subtitle အတွက် သီးသန့် Isolated Function ဆီသို့ လွှဲပေးပါမည်
        return render_movie_subtitle(video_path, str(settings.get("script", "")), settings, output_path)
    elif recap_type in ["Raw to Recap", "Raw to reacp"]:
        return render_raw_to_recap(video_path, tts_wav, settings, output_path)
        
    source = probe_video(video_path)
    aspect = str(settings.get("aspect", "9:16"))
    scale = 854
    out_w, out_h = (round(scale * 16 / 9), scale) if aspect == "16:9" else (scale, scale) if aspect == "1:1" else (scale, round(scale * 16 / 9))
    out_w -= out_w % 2; out_h -= out_h % 2
    visible_rect = fitted_source_rect(source, out_w, out_h)
    
    temp_paths = []
    raw_audio_path = Path(tempfile.mktemp(suffix=".mp3"))
    raw_audio_path.write_bytes(tts_wav)
    temp_paths.append(raw_audio_path)
    
    speed_float = float(settings.get("speed") or settings.get("videoSpeed") or settings.get("voiceSpeed") or 1.0)
    recap_type = str(settings.get("recapType", settings.get("recap_type", "")))
    is_simple = recap_type in {"Movie Dub", "Movie Recap"}
    orig_audio = str(settings.get("originalAudio", ""))
    if not orig_audio:
        orig_audio = "Low" if is_simple else "Mute"
    audio_already_sped_up = False
    
    script_text = str(settings.get("script", ""))
    voice = str(settings.get("voice", "Nilar"))
    
    vid_duration = float(source["duration"]) # ဤနေရာတွင် vid_duration ကို ကြိုတင်သတ်မှတ်ပါ
    v_pts_factor = 1.0 
    target_vid_dur = vid_duration

    # Raw to Recap ကို Hybrid စနစ်သစ်ဖြင့် သွားမည်ဖြစ်၍ Global Stretch မှ ဖယ်ထုတ်ထားပါသည်
    if recap_type == "Movie Recap" and script_text:
        try:
            base_wav = generate_microsoft_tts(script_text, voice, speed_factor=1.0)
            temp_base_path = Path(tempfile.mktemp(suffix=".mp3"))
            temp_base_path.write_bytes(base_wav)
            base_audio_dur = get_audio_duration(temp_base_path)
            
            calculated_factor = vid_duration / base_audio_dur if base_audio_dur > 0 else 0.95
            video_slow_factor = max(0.95, calculated_factor) 
            target_vid_dur = vid_duration / video_slow_factor
            v_pts_factor = 1.0 / video_slow_factor
            
            if base_audio_dur > target_vid_dur:
                auto_speed = base_audio_dur / target_vid_dur
                final_wav = generate_microsoft_tts(script_text, voice, speed_factor=auto_speed)
                speed_float = auto_speed 
            else:
                final_wav = base_wav
                speed_float = 1.0
                
            if voice == "Voice_Clone":
                cloned_audio_path = str(settings.get("cloned_audio_path", ""))
                if not cloned_audio_path:
                    raise RuntimeError("⚠️ အသံပွားရန်အတွက် ကိုယ်ပိုင် MP3 ဖိုင်ကို တင်ပေးရန် လိုအပ်ပါသည်။")
                final_wav = apply_voice_clone(final_wav, cloned_audio_path)
                    
            raw_audio_path.write_bytes(final_wav)
            audio_already_sped_up = True
            temp_base_path.unlink(missing_ok=True)
        except Exception as e:
            if "⚠️" in str(e): raise e # Voice Clone Error တက်လျှင် Nilar ဖြင့် မဖုံးတော့ဘဲ ချက်ချင်းရပ်မည်
            print(f"Auto Audio Sync Failed: {e}")
            video_slow_factor = 0.95
            target_vid_dur = vid_duration / video_slow_factor
            v_pts_factor = 1.0 / video_slow_factor
    elif script_text:
        try:
            if voice == "Voice_Clone":
                native_wav = generate_microsoft_tts(script_text, "Nilar", speed_factor=speed_float)
                cloned_audio_path = str(settings.get("cloned_audio_path", ""))
                if not cloned_audio_path:
                    raise RuntimeError("⚠️ အသံပွားရန်အတွက် ကိုယ်ပိုင် MP3 ဖိုင်ကို တင်ပေးရန် လိုအပ်ပါသည်။")
                final_wav = apply_voice_clone(native_wav, cloned_audio_path)
                raw_audio_path.write_bytes(final_wav)
            else:
                native_wav = generate_microsoft_tts(script_text, voice, speed_factor=speed_float)
                raw_audio_path.write_bytes(native_wav)
                
            audio_already_sped_up = True
        except Exception as e:
            if "⚠️" in str(e): raise e # Voice Clone Error တက်လျှင် Nilar ဖြင့် မဖုံးတော့ဘဲ ချက်ချင်းရပ်မည်
            pass

    script_text = str(settings.get("script", ""))
    hook_text = ""
    has_cover = False
    
    lines = [line.strip() for line in script_text.strip().split('\n') if line.strip()]
    for line in lines[:5]:
        if line.startswith("TITLE:"):
            hook_text = line.replace("TITLE:", "").strip().strip("*\"'")
        if line.startswith("COVER:") and "YES" in line.upper():
            has_cover = True
    
    if settings.get("trim_cover"):
        trim_sec = 0.02
    else:
        trim_sec = float(settings.get("trim_start", 0))
        
    if trim_sec > 0:
        inputs = ["ffmpeg", "-y", "-ss", str(trim_sec), "-i", str(video_path), "-i", str(raw_audio_path)]
    else:
        inputs = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(raw_audio_path)]
    
    raw_tts_dur = get_audio_duration(raw_audio_path)
    vid_duration = float(source["duration"])
    if raw_tts_dur <= 0: raw_tts_dur = vid_duration
    
    if audio_already_sped_up:
        raw_target = raw_tts_dur
    else:
        raw_target = raw_tts_dur / speed_float if speed_float > 0 else raw_tts_dur
        
    if recap_type == "Movie Dub":
        target_dur = max(vid_duration, raw_target)
        v_pts_factor = 1.0
    elif recap_type in ["Movie Recap", "Raw to reacp"]:
        # အသံ Speed တင်ပြီးသည့်တိုင်အောင် ပိုရှည်နေသေးပါက အသံမပြတ်သွားစေရန် 0.90 အထိ ထပ်နှေးခွင့် (ကြားညှိ) ပေးမည်
        if raw_target > target_vid_dur:
            final_v_factor = max(0.90, vid_duration / raw_target)
            target_dur = vid_duration / final_v_factor
            v_pts_factor = 1.0 / final_v_factor
        else:
            target_dur = target_vid_dur
            v_pts_factor = 1.0 / video_slow_factor
    else:
        target_dur = raw_target
        v_pts_factor = target_dur / vid_duration if vid_duration > 0 else 1.0

    video_filters = [f"setpts={v_pts_factor:.4f}*PTS-STARTPTS", "fps=30"]
    if settings.get("mirror"): video_filters.append("hflip")
    if settings.get("zoom_enabled"): video_filters.append("crop=iw*0.96:ih*0.96")
    v_filter = settings.get("video_filter", "None")
    if v_filter == "HD Cam 2":
        video_filters.append("eq=brightness=0.02:saturation=1.1:contrast=1.15:gamma=0.95")
    elif v_filter == "Glow":
        video_filters.append("eq=brightness=0.06:saturation=1.2:contrast=1.05")
    video_filters.append("unsharp=5:5:0.8:3:3:0.4")
    if settings.get("video_bg_blur"):
        video_filters.append(f"split=2[bg][fg];[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h},boxblur=20:10[bg_blurred];[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fg_scaled];[bg_blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2")
    else:
        video_filters.extend([f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"])
    
    graph = f"[0:v]{','.join(video_filters)}[v0]"
    current_v = "v0"
    
    if hook_text:
        if has_cover:
            # AI မှ သူများကာဘာ တွေ့ရှိပါက ၁ စက္ကန့်တိတိ အပြည့်ဖုံးအုပ်မည့် Cinematic Mask (စာသားမပါဘဲ ဖုံးရုံသက်သက်)
            title_img_path = Path(tempfile.mktemp(suffix=".png"))
            temp_paths.append(title_img_path)
            cover_img = Image.new("RGBA", (out_w, out_h), (15, 18, 25, 255))
            cover_img.convert("RGB").save(title_img_path, "PNG")
            inputs.extend(["-i", str(title_img_path)])
            
            next_v = "vtitle"
            title_input_idx = inputs.count("-i") - 1
            graph += f";[{current_v}][{title_input_idx}:v]overlay=x=0:y=0:enable='between(t,0,1)'[{next_v}]"
            current_v = next_v
    
    if settings.get("blur_enabled"):
        raw_b = int(settings.get("blur_strength", 10))
        luma_r, chroma_r = max(1, min(20, raw_b)), max(1, min(14, raw_b // 2 if raw_b > 2 else 1))
        for idx, mask in enumerate(settings.get("blur_masks", [])):
            vx, vy, vw, vh = visible_rect["x"], visible_rect["y"], visible_rect["width"], visible_rect["height"]
            bx, by = max(1, vx + int(float(mask["x"]) * vw)), max(1, vy + int(float(mask["y"]) * vh))
            bw, bh = max(1, min(int(float(mask["width"]) * vw), out_w - bx - 1)), max(1, min(int(float(mask["height"]) * vh), out_h - by - 1))
            next_v = f"blur{idx}"
            graph += f";[{current_v}]split=2[bg{idx}][toblur{idx}];[toblur{idx}]crop={bw}:{bh}:{bx}:{by},boxblur={luma_r}:{chroma_r}[blurred{idx}];[bg{idx}][blurred{idx}]overlay={bx}:{by}[{next_v}]"
            current_v = next_v

    
        
    # Test Logo (အပြေး Logo) ကို ဖွင့်ထားပါက ရပ်နေသော Logo ကို ဖျောက်ပြီး မျက်နှာပြင်အနှံ့ ကန်ထွက်ပြေးစေမည့်စနစ်
    if settings.get("logo_type") == "moving":
        # ပြင်ဆင်ချက် - logo_font ကို ဖတ်ရန်
        user_font = str(settings.get("logo_font", "Beloved Bunny"))
        safe_font = Path(tempfile.gettempdir()) / "safe_font.ttf"
        shutil.copy2(FONT_FILES.get(user_font, FONT_FILES["Noto Sans Myanmar"]), safe_font)
        font_path = str(safe_font).replace('\\', '/').replace(':', r'\:')
        
        run_text = str(settings.get("logo_text", "")).strip()
        if not run_text:
            run_text = "Test Logo"
        safe_text = run_text.replace("'", "\u2019").replace(":", r"\:")

        next_v = "vtestlogo"
        graph += f";[{current_v}]drawtext=fontfile='{font_path}':text='{safe_text}':x='abs(w-tw-abs(mod(t*150,max(1,(w-tw)*2))-(w-tw)))':y='abs(h-th-abs(mod(t*100,max(1,(h-th)*2))-(h-th)))':fontsize=h*0.05:fontcolor=white:shadowcolor=black:shadowx=2:shadowy=2[{next_v}]"
        current_v = next_v

    # Test Logo ပိတ်ထားမှသာ ရိုးရိုး (ရပ်နေသော) Logo ပေါ်မည်
    elif settings.get("logo_type", "static") == "static":
        logo_str, logo_text = settings.get("logo_path") or settings.get("watermark_path"), str(settings.get("logo_text", "")).strip()
        lx_pct, ly_pct, l_size = max(0, min(100, int(settings.get("logo_x", 84)))) / 100.0, max(0, min(100, int(settings.get("logo_y", 86)))) / 100.0, max(10, min(100, int(settings.get("logo_size", 22)))) / 100.0
        target_w = int(out_w * l_size)

        if logo_str and Path(str(logo_str)).exists():
            inputs.extend(["-i", str(logo_str)])
            next_v = "vlogo"
            graph += f";[{inputs.count('-i') - 1}:v]scale={target_w}:-1[wm];[{current_v}][wm]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
            current_v = next_v
        elif logo_text:
            text_img_path = Path(tempfile.mktemp(suffix=".png"))
            # ပြင်ဆင်ချက် - logo_font ကို ဖတ်ရန်
            font_path_to_use = FONT_FILES.get(str(settings.get("logo_font", "Beloved Bunny")), FONT_FILES["Noto Sans Myanmar"])
            create_text_image(logo_text, font_path_to_use, text_img_path, int(out_h * l_size * 0.3))
            temp_paths.append(text_img_path)
            inputs.extend(["-i", str(text_img_path)])
            next_v = "vlogo"
            graph += f";[{current_v}][{inputs.count('-i') - 1}:v]overlay=W*{lx_pct}-w/2:H*{ly_pct}-h/2[{next_v}]"
            current_v = next_v

    if settings.get("subtitles", True):
        ass_speed = 1.0 if audio_already_sped_up else speed_float
        srt = str(settings.get("subtitle_srt") or make_srt(str(settings.get("script") or ""), raw_tts_dur))
        ass_path = Path(tempfile.mktemp(suffix=".ass"))
        ass_path.write_text(srt_to_ass(srt, settings, out_w, out_h, visible_rect, ass_speed), encoding="utf-8", newline="\n")
        temp_paths.append(ass_path)
        font_dir = Path(tempfile.mkdtemp(prefix="fonts-"))
        shutil.copy2(FONT_FILES.get(str(settings.get("subtitle_font")), FONT_FILES["Noto Sans Myanmar"]), font_dir)
        temp_paths.append(font_dir)
        next_v = "vsub"
        graph += f";[{current_v}]ass=filename='{str(ass_path).replace(':', r'\:')}':fontsdir='{str(font_dir).replace(':', r'\:')}'[{next_v}]"
        current_v = next_v
    
    current_a_mix = "1:a" 
    a_filters = []
    
    a_filters.append(f"[{current_a_mix}]aresample=44100,aformat=channel_layouts=stereo[tts_base]")
    current_a_mix = "tts_base"
    
    if speed_float != 1.0 and not audio_already_sped_up:
        t_rem = speed_float
        t_filters = []
        while t_rem > 2.0:
            t_filters.append("atempo=2.0")
            t_rem /= 2.0
        while t_rem < 0.5:
            t_filters.append("atempo=0.5")
            t_rem /= 0.5
        t_filters.append(f"atempo={t_rem:.4f}")
        
        a_filters.append(f"[{current_a_mix}]{','.join(t_filters)}[tts_speed]")
        current_a_mix = "tts_speed"

    if recap_type in ["Movie Recap", "Silent Recap", "Raw to reacp"]:
        orig_audio = "Mute"

    has_orig = source.get("has_audio") and orig_audio != "Mute"
    
    # Movie Dub အသံ Mute လုပ်သည့် Toggle ကို စစ်ဆေးခြင်း
    enable_sfx = settings.get("enable_sfx", True)
    if recap_type == "Movie Dub" and not enable_sfx:
        has_orig = False
        
    if has_orig:
        if recap_type == "Movie Dub":
            sfx_only_path = get_background_audio_only(video_path, Path(tempfile.gettempdir()))
            # AI က အသံမခွဲပေးနိုင်ရင် တရုတ်သံကြီး ဝင်မလာအောင် မူရင်းအသံကို လုံးဝ Mute ပစ်ပါမည်
            if not sfx_only_path:
                has_orig = False
        else:
            sfx_only_path = None

    if has_orig:
        user_sfx_vol = float(settings.get("sfx_volume", 85)) / 100.0
        vol = f"{user_sfx_vol:.2f}"
        t, tempo_filters = vid_duration / target_dur if target_dur > 0 else 1.0, []
        while t < 0.5: tempo_filters.append("atempo=0.5"); t /= 0.5
        while t > 2.0: tempo_filters.append("atempo=2.0"); t /= 2.0
        tempo_filters.append(f"atempo={t:.4f}")
        
        if sfx_only_path:
            inputs.extend(["-i", str(sfx_only_path)])
            sfx_index = inputs.count("-i") - 1
            a_filters.append(f"[{sfx_index}:a]aresample=44100,aformat=channel_layouts=stereo,volume={vol},{','.join(tempo_filters)}[orig_a]")
        else:
            a_filters.append(f"[0:a]aresample=44100,aformat=channel_layouts=stereo,volume={vol},{','.join(tempo_filters)}[orig_a]")
            
        in_a = current_a_mix if ":" in current_a_mix else f"[{current_a_mix}]"
        
        a_filters.append(f"{in_a}asplit=2[tts_for_sc][tts_for_mix]")
        a_filters.append(f"[orig_a][tts_for_sc]sidechaincompress=threshold=0.06:ratio=20:attack=1:release=20[ducked_orig]")
        a_filters.append(f"[tts_for_mix][ducked_orig]amix=inputs=2:duration=longest:dropout_transition=2[amixed_1];[amixed_1]volume=2.0[mixed_1]")
        current_a_mix = "mixed_1"
        
    bgm_path_str = settings.get("music_path")
    if bool(bgm_path_str) and Path(str(bgm_path_str)).exists() and orig_audio != "Normal":
        inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path_str)])
        a_filters.append(f"[{inputs.count('-i') - 1}:a]aresample=44100,aformat=channel_layouts=stereo,volume={float(settings.get('music_volume', 24)) / 100.0}[bgm_a]")
        in_a = current_a_mix if ":" in current_a_mix else f"[{current_a_mix}]"
        a_filters.append(f"{in_a}[bgm_a]amix=inputs=2:duration=longest:dropout_transition=2[amixed_2];[amixed_2]volume=2.0[mixed_2]")
        current_a_mix = "mixed_2"

    if a_filters: graph += ";" + ";".join(a_filters)

    command = inputs + [
        "-filter_complex", graph,
        "-map", f"[{current_v}]", "-map", current_a_mix if ":" in current_a_mix else f"[{current_a_mix}]",
        "-c:v", "libx264", "-preset", "faster", "-crf", "26", "-pix_fmt", "yuv420p", "-r", "30", 
        "-c:a", "aac", "-b:a", "128k", "-t", f"{target_dur:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    
    try:
        _run(command, timeout=2400)
        if not output_path.exists() or output_path.stat().st_size < 1024: raise RuntimeError("Final MP4 မဖန်တီးနိုင်ပါ။")
        return output_path
    finally:
        for p in temp_paths: (shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True))