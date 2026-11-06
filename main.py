"""FastAPI server for One Team Movie Recap Studio."""

from __future__ import annotations
import os, uuid, shutil, subprocess, requests, json, re, time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Body
from fastapi.responses import FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import yt_dlp
import media_engine
import membership

app = FastAPI(title="One Team Movie Recap Studio")

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
FONTS_DIR = ROOT / "fonts"

for folder in [STATIC_DIR, UPLOAD_DIR, OUTPUT_DIR, FONTS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# ----------------- Telegram Webhook Auto Setup -----------------
@app.on_event("startup")
async def setup_telegram_webhook():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    base_url = os.getenv("BASE_URL", "").strip()
    if bot_token and base_url:
        webhook_url = f"{base_url}/api/telegram/webhook"
        try:
            requests.get(f"https://api.telegram.org/bot{bot_token}/setWebhook?url={webhook_url}", timeout=10)
            print("Telegram Webhook Auto-Set Successfully!")
        except: pass

# Static files များ ချိတ်ဆက်ခြင်း
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/fonts", StaticFiles(directory=str(FONTS_DIR)), name="fonts")

# GCS မသုံးတော့ဘဲ VPS Local Storage (100GB) ကိုသာ အသုံးပြုရန်
BUCKET_NAME = ""

def get_bucket():
    return None

def upload_to_gcs(local_path: Path | str, blob_name: str, content_type: str = "application/octet-stream") -> str:
    return ""

def download_from_gcs(blob_name: str, local_path: Path | str) -> bool:
    return False

def save_json_to_gcs(blob_name: str, data: dict):
    pass

def load_json_from_gcs(blob_name: str) -> dict | None:
    return None

class JobSettings(BaseModel):
    project_id: str
    settings: dict[str, Any]
    simple_api_key: Optional[str] = ""

def extract_token(auth_header: Optional[str]) -> str:
    if not auth_header: return ""
    if auth_header.startswith("Bearer "): return auth_header[7:]
    return auth_header

def save_data(type_str: str, id_str: str, data: dict):
    path = UPLOAD_DIR / f"{type_str}_{id_str}.json"
    tmp_path = UPLOAD_DIR / f"{type_str}_{id_str}.tmp"
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try: save_json_to_gcs(f"data/{type_str}_{id_str}.json", data)
    except: pass

def load_data(type_str: str, id_str: str) -> dict | None:
    path = UPLOAD_DIR / f"{type_str}_{id_str}.json"
    if path.exists() and path.stat().st_size > 0:
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content: return json.loads(content)
        except: pass
    gcs_data = load_json_from_gcs(f"data/{type_str}_{id_str}.json")
    if gcs_data:
        try: path.write_text(json.dumps(gcs_data, ensure_ascii=False), encoding="utf-8")
        except: pass
        return gcs_data
    return None

def update_job(job_id: str, updates: dict):
    job = load_data("job", job_id)
    if job:
        job.update(updates)
        save_data("job", job_id, job)

def send_telegram_alert(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
    except: pass

@app.get("/")
async def serve_index(): return FileResponse(STATIC_DIR / "editor.html")
@app.get("/login")
async def serve_login(): return FileResponse(STATIC_DIR / "login.html")
@app.get("/owner")
async def serve_owner(): return FileResponse(STATIC_DIR / "owner.html")

@app.get("/api/auth-config")
async def get_auth_config():
    return membership.SupabaseMembership().public_config()

@app.post("/api/browser-session")
async def bridge_session(): return {"status": "ok"}
@app.delete("/api/browser-session")
async def clear_session(): return {"status": "cleared"}

@app.get("/api/membership")
async def get_membership(authorization: Optional[str] = Header(None)):
    try: return membership.SupabaseMembership().member_from_token(extract_token(authorization))
    except Exception as e: raise HTTPException(status_code=401, detail=str(e))

@app.get("/api/plans")
async def get_plans(): return membership.SupabaseMembership().public_payment_info()

@app.post("/api/payments")
async def submit_payment(
    authorization: Optional[str] = Header(None), kind: str = Form(...), offer_key: str = Form(...),
    payment_method: str = Form(...), transaction_id: str = Form(...), receipt: Optional[UploadFile] = File(None)
):
    db = membership.SupabaseMembership()
    try:
        member = db.member_from_token(extract_token(authorization))
        receipt_bytes = await receipt.read() if receipt else b""
        db.create_payment_request(member, kind=kind, offer_key=offer_key, payment_method=payment_method, transaction_id=transaction_id, receipt=receipt_bytes, receipt_mime=receipt.content_type if receipt else "")
        return {"status": "ok", "message": "ငွေလွှဲအချက်အလက် ပို့ပြီးပါပြီ။ Admin အတည်ပြုမှုကို စောင့်ပါ။"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/telegram/webhook")
async def telegram_webhook(payload: dict):
    try:
        import os
        import urllib.request
        import json
        
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        message = payload.get("message", {})
        text = message.get("text", "")
        chat_id = str(message.get("chat", {}).get("id", ""))
        
        if bot_token and chat_id and text:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            if text.startswith("/start "):
                # Telegram မှ လက်ခံရရှိသော စာသားကို Email အမှန်ဖြစ်အောင် ပြန်ပြောင်းမည်
                raw_payload = text.split(" ")[1].strip()
                user_key = raw_payload.replace("_at_", "@").replace("_dot_", ".").lower()
                save_data("tg_bind", user_key, {"chat_id": chat_id})
                reply_text = f"✅ One Team Movie Recap နှင့် အောင်မြင်စွာ ချိတ်ဆက်ပြီးပါပြီ။\n(Account: {user_key})"
            else:
                reply_text = f"✅ ဆာဗာ အလုပ်လုပ်ပါသည်။ (ရရှိသောစာ: {text})"
            
            # စာပြန်ပို့မည့် အပိုင်း (Error လုံးဝမတက်စေရန်)
            data = json.dumps({"chat_id": chat_id, "text": reply_text}).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req)
            
    except Exception as e:
        print("Telegram Webhook Error:", e)
        
    return {"status": "ok"}

@app.post("/api/upload-video-local")
async def upload_video_local(project_id: str = Form(...), file: UploadFile = File(...)):
    try:
        import shutil
        filename = file.filename
        file_id = f"{project_id}_{filename.replace(' ', '_')}"
        local_path = UPLOAD_DIR / file_id
        
        with open(local_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
            
        info = media_engine.probe_video(local_path)
        save_data("proj", project_id, {
            "video_path": str(local_path), 
            "filename": filename, 
            "file_id": file_id,
            "info": info
        })
        return info
    except Exception as exc:
        if 'local_path' in locals() and local_path.exists(): local_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Video ဖိုင် မှားယွင်းနေပါသည်")

@app.post("/api/assets")
async def upload_asset(file: UploadFile = File(...), kind: str = Form(...), project_id: str = Form(...)):
    try:
        filename = f"{kind}_{uuid.uuid4().hex}_{file.filename}"
        path = UPLOAD_DIR / filename
        with open(path, "wb") as f:
            f.write(await file.read())
        return {"status": "ok", "url": f"/api/play/{filename}", "path": str(path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/play/{filename}")
async def play_video(filename: str):
    file_path = UPLOAD_DIR / filename
    if not file_path.exists():
        if not download_from_gcs(f"uploads/{filename}", file_path): raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="video/mp4")

@app.post("/api/link-preview")
async def get_link_preview(payload: dict = Body(...)):
    url = payload.get("url", "").strip()
    if not url: raise HTTPException(status_code=400, detail="Link မပါပါ")
    project_id = str(uuid.uuid4())
    job_id = f"dl_{project_id}"
    save_data("job", job_id, {"status": "running", "progress": 0, "message": "Link ချိတ်ဆက်နေပါသည်...", "project_id": project_id})
    
    import threading
    def dl_worker():
        filename = f"link_{project_id}.mp4"
        dl_path = UPLOAD_DIR / filename
        dl_dir = UPLOAD_DIR
        try:
            update_job(job_id, {"progress": 15, "message": "ဗီဒီယို ရှာဖွေနေပါသည်..."})
            downloaded = False
            
            is_youtube = "youtube.com" in url or "youtu.be" in url
            
            if not is_youtube:
                try:
                    cobalt_payload = {"url": url, "vCodec": "h264", "vQuality": "720"}
                    api_res = requests.post("https://api.cobalt.tools/api/json", json=cobalt_payload, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=20)
                    if api_res.ok:
                        res_json = api_res.json()
                        video_url = res_json.get("url") or (res_json.get("picker")[0].get("url") if res_json.get("picker") else None)
                        if video_url:
                            update_job(job_id, {"progress": 40, "message": "ဒေါင်းလုဒ် ဆွဲယူနေပါသည်..."})
                            with requests.get(video_url, stream=True, timeout=60) as r:
                                r.raise_for_status()
                                with open(dl_path, 'wb') as f:
                                    for chunk in r.iter_content(chunk_size=1048576): 
                                        if chunk: f.write(chunk)
                            downloaded = True
                except: pass 
                
            if not downloaded or not dl_path.exists() or dl_path.stat().st_size == 0:
                update_job(job_id, {"progress": 30, "message": "VPS ဖြင့် ချိတ်ဆက်နေပါသည်..."})
                
                vps_api = "http://169.58.181.87:8001/api/download" 
                response = requests.post(vps_api, json={"url": url}, stream=True, timeout=600)
                
                if response.status_code == 200:
                    import shutil
                    with open(dl_path, 'wb') as f:
                        shutil.copyfileobj(response.raw, f)
                    filename = dl_path.name
                    downloaded = True
                else:
                    raise RuntimeError(f"VPS မှ ဒေါင်းလုဒ်ဆွဲမရပါ: {response.text}")
                    
            if not downloaded or not dl_path.exists() or dl_path.stat().st_size == 0: 
                raise ValueError("Video ဖိုင်ကို အပြည့်အဝ ဒေါင်းလုဒ်ဆွဲ၍ မရပါ။ (Private/Age-restricted ဖြစ်နိုင်ပါသည်)")
                
            info = media_engine.probe_video(dl_path)
            upload_to_gcs(dl_path, f"uploads/{filename}", content_type="video/mp4")
            save_data("proj", project_id, {"video_path": str(dl_path), "filename": filename, "gcs_path": f"uploads/{filename}", "info": info})
            update_job(job_id, {"status": "done", "progress": 100, "message": "ပြီးဆုံးပါပြီ", "duration": info.get("duration", 0), "video_url": f"/api/play/{filename}"})
        except Exception as e:
            if dl_path.exists(): dl_path.unlink(missing_ok=True)
            update_job(job_id, {"status": "error", "error": str(e), "message": f"Link မှ ဒေါင်းလုဒ်ဆွဲ၍ မရပါ: {str(e)}"})
    threading.Thread(target=dl_worker, daemon=True).start()
    return {"status": "started", "job_id": job_id}

@app.post("/api/preview-ms-voice")
async def preview_ms_voice(payload: dict = Body(...)):
    voice = payload.get("voice", "Nilar")
    text = "မင်္ဂလာပါ၊ ဝမ် ထိမ်း မှ ကြိုဆိုပါတယ်။"
    try:
        if voice in media_engine.GEMINI_VOICES:
            api_key = os.getenv("GEMINI_API_KEY", "")
            audio_bytes = media_engine.generate_gemini_tts(text, voice, api_key)
        else:
            import azure.cognitiveservices.speech as speechsdk
            key = os.getenv("AZURE_SPEECH_KEY", "").strip()
            region = os.getenv("AZURE_SPEECH_REGION", "southeastasia").strip()
            
            selected_voice = media_engine.MICROSOFT_VOICES.get(voice, media_engine.DEFAULT_MICROSOFT_VOICE)
            ssml = f'<speak version="1.0" xml:lang="my-MM" xmlns="http://www.w3.org/2001/10/synthesis"><voice name="{selected_voice}"><prosody rate="+0%">{text}</prosody></voice></speak>'
            
            speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
            speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
            
            result = synthesizer.speak_ssml_async(ssml).get()
            if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                raise Exception("Microsoft Voice Error")
            audio_bytes = result.audio_data
            
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/jobs")
async def create_job(payload: JobSettings, authorization: Optional[str] = Header(None)):
    db = membership.SupabaseMembership()
    try: member = db.member_from_token(extract_token(authorization))
    except Exception: raise HTTPException(status_code=401, detail="Session expired")

    proj = load_data("proj", payload.project_id)
    if not proj: raise HTTPException(status_code=400, detail="Video ဖိုင် ရှာမတွေ့ပါ။")

    duration_seconds = float(proj["info"].get("duration", 0))
    plan = membership.effective_plan(member)
    
    owner_settings = load_data("settings", "owner") or {
        "free_limit_enabled": True, "free_daily_limit": 1,
        "free_max_seconds": 300, "free_allowed_type": "Movie Recap", "free_allow_voice_clone": False
    }
    
    recap_type = payload.settings.get("recapType", payload.settings.get("recap_type", "Movie Recap"))
    voice = payload.settings.get("voice", "Nilar")
    
    if plan == "simple" and owner_settings.get("free_limit_enabled", True):
        allowed_type = owner_settings.get("free_allowed_type", "Movie Recap")
        if recap_type != allowed_type:
            raise HTTPException(status_code=403, detail=f"အခမဲ့အသုံးပြုသူများသည် '{allowed_type}' အမျိုးအစား တစ်ခုတည်းကိုသာ အသုံးပြုခွင့်ရှိပါသည်။")
        if not owner_settings.get("free_allow_voice_clone", False) and voice == "Voice_Clone":
            raise HTTPException(status_code=403, detail="Voice Clone ကို အခမဲ့အသုံးပြုခွင့် မရှိပါ။ VIP ဝယ်ယူပါ။")

    total_succ = db.successful_exports_today(member, plan)
    daily_succ = db.successful_exports_today_date(member, membership.myanmar_export_day())
    
    try: 
        entitlement = membership.evaluate_export(member, duration_seconds, total_succ, daily_succ, owner_settings)
    except Exception as e: 
        raise HTTPException(status_code=403, detail=str(e))

    job_id = str(uuid.uuid4())
    job_data = {
        "id": job_id, "status": "running", "progress": 5, "step": "upload", 
        "message": "အချက်အလက်များကို စစ်ဆေးနေပါသည်", "download_url": "", "thumb_url": "",
        "user_email": member.get("email", ""), "filename": proj.get("filename", "Video"),
        "duration": duration_seconds, "created_at": time.time(),
        "recap_type": payload.settings.get("recapType", payload.settings.get("recap_type", "Movie Recap"))
    }
    save_data("job", job_id, job_data)

    db._request("POST", "/rest/v1/jobs", headers=db._service_headers(), json={
        "job_id": job_id,
        "google_subject": member.get("google_subject", ""),
        "filename": proj.get("filename", "Video"),
        "status": "running",
        "progress": 5,
        "message": "အချက်အလက်များကို စစ်ဆေးနေပါသည်",
        "duration": duration_seconds
    })

    import threading
    def task_worker():
        try:
            settings = payload.settings
            
            # ပြင်ဆင်ချက်: Speed မှန်ကန်စေရန် voiceSpeed ကို speed အဖြစ် ပြောင်းလဲသတ်မှတ်ခြင်း
            settings["speed"] = settings.get("voiceSpeed", settings.get("speed", 1.0))
            
            api_key = payload.simple_api_key or os.getenv("GEMINI_API_KEY", "")
            video_path = Path(proj["video_path"])
            if not video_path.exists(): download_from_gcs(proj.get("gcs_path") or f"uploads/{proj.get('file_id', video_path.name)}", video_path)

            voice_choice = settings.get("voice", "Nilar")
            def update_progress(percent: int, msg: str):
                update_job(job_id, {"progress": percent, "message": msg, "step": "render" if percent >= 80 else "voice" if percent >= 50 else "script" if percent >= 20 else "upload"})
                db._request("PATCH", f"/rest/v1/jobs?job_id=eq.{job_id}", headers=db._service_headers(), json={"progress": percent, "message": msg})

            script = media_engine.generate_script(api_key, video_path, "video/mp4", settings.get("language", "Myanmar"), int(duration_seconds), settings.get("tone", "Cinematic"), settings.get("recapType", "Movie Recap"), "Natural", update_progress, settings.get("groq_api_key", ""))
            settings["script"] = script

            # --- အသစ်ထည့်ရန်: Raw to Recap တွင် UI ကိုကျော်ပြီး Normal Speed (1.0) အတိုင်း အရင်ထားရန် ---
            if settings.get("recapType", "") in ["Raw to Recap", "Raw to reacp"]:
                settings["speed"] = 1.0
                settings["voiceSpeed"] = 1.0

            if settings.get("recapType", "") == "Movie Subtitle":
                update_progress(65, "စာတန်းထိုးအတွက်ဖြစ်၍ မြန်မာအသံ မထုတ်ပါ")
                tts_wav = b""
            elif settings.get("recapType", "") in ["Raw to Recap", "Raw to reacp"]:
                update_progress(65, "🎬 အသံများကို အပိုင်းလိုက်ဖြတ်၍ တိကျစွာ ထုတ်လုပ်နေပါသည် (API ကြောင့် အနည်းငယ် ကြာနိုင်ပါသည်)")
                import re, time
                
                # 📌 AI ရေးပုံ ပြောင်းလဲမှုများကို အမှားခံနိုင်သော Flexible Regex သစ်
                pattern = r'\[.*?(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?))?.*?\]\s*(.*)'
                script_text = settings.get("script", "")
                audio_chunks = []
                
                for line in script_text.splitlines():
                    line = line.strip()
                    # AI မှ ရှေ့ဆုံးတွင် ** သို့မဟုတ် - များ ထည့်ရေးထားပါက အရင်ရှင်းလင်းမည်
                    line = re.sub(r'^[\*\-\s]+', '', line)
                    
                    # re.match အစား နေရာမရွေးရှာနိုင်သော re.search ကို သုံးမည်
                    m = re.search(pattern, line, re.IGNORECASE)
                    if m:
                        s_str, e_str, raw_text = m.groups()
                        clean_text = media_engine.clean_script_text(raw_text)
                        if not clean_text: continue
                        
                        def parse_sec(t_str):
                            if not t_str: return 0.0
                            p = t_str.split(':')
                            if len(p) >= 3: return int(p[0])*3600 + int(p[1])*60 + int(p[2])
                            return int(p[0])*60 + int(p[1])
                            
                        st = parse_sec(s_str)
                        time.sleep(0.5) # API Rate Limit မဖြစ်အောင် 0.5 စက္ကန့် စောင့်ပေးမည်
                        
                        try:
                            if voice_choice in media_engine.GEMINI_VOICES:
                                chunk_wav = media_engine.generate_gemini_tts(clean_text, voice_choice, api_key)
                            else:
                                chunk_wav = media_engine.generate_microsoft_tts(clean_text, voice_choice, speed_factor=1.0)
                            audio_chunks.append({"start": st, "text": clean_text, "audio_bytes": chunk_wav})
                        except Exception as e:
                            print(f"Chunk TTS Error: {e}")
                            pass
                            
                settings["audio_chunks"] = audio_chunks
                tts_wav = b"" # Raw to Recap အတွက် အသံအရှည်ကြီး မသုံးတော့ပါ
            else:
                update_progress(65, "အသံ ဖိုင် ထုတ်ယူနေပါသည်")
                tts_wav = media_engine.generate_gemini_tts(script, voice_choice, api_key) if voice_choice in media_engine.GEMINI_VOICES else media_engine.generate_microsoft_tts(script, voice_choice, speed_factor=float(settings.get("speed", 1.0)))

            update_progress(80, "Final Video ပေါင်းစပ်နေပါသည်")
            out_file = OUTPUT_DIR / f"final_{job_id}.mp4"
            media_engine.render_final(video_path, tts_wav, settings, out_file)
            gcs_url = upload_to_gcs(out_file, f"outputs/{out_file.name}", content_type="video/mp4")

            # ကာဘာပုံကို မူရင်းဗီဒီယိုမှ 3:4 Size ဖြင့် Screenshot ရိုက်ယူခြင်း
            thumb_file = OUTPUT_DIR / f"thumb_{job_id}.jpg"
            thumb_res = media_engine.generate_thumbnail(video_path, script, thumb_file)
            if thumb_res:
                thumb_gcs = upload_to_gcs(thumb_file, f"outputs/{thumb_file.name}", content_type="image/jpeg")
                thumb_download_url = f"/api/download/{thumb_file.name}"
            else:
                thumb_download_url = ""

            user_name = member.get("user_metadata", {}).get("full_name") or member.get("email", "").split("@")[0]
            v_title = proj.get("filename", "video.mp4")
            
            out_size_mb = round(out_file.stat().st_size / (1024 * 1024), 2) if out_file.exists() else 0.0
            speed_val = settings.get("speed", 1.0)
            recap_type_val = settings.get("recapType", settings.get("recap_type", "Movie Recap"))
            
            # ထွက်လာသည့် Final Video ၏ ကြာချိန်အမှန်ကို တိုက်ရိုက်တိုင်းတာခြင်း
            try:
                final_dur = int(float(media_engine.probe_video(out_file).get("duration", 0)))
            except:
                final_dur = int(duration_seconds / float(speed_val)) if float(speed_val) > 0 else int(duration_seconds)

            db.record_success(member, entitlement, duration_seconds)
            
            update_job(job_id, {"status": "done", "progress": 100, "message": "Final MP4 အဆင်သင့်ပါပြီ", "download_url": f"/api/download/{out_file.name}", "thumb_url": thumb_download_url, "gcs_url": gcs_url, "duration": final_dur})
            
            db._request("PATCH", f"/rest/v1/jobs?job_id=eq.{job_id}", headers=db._service_headers(), json={
                "status": "done", 
                "progress": 100, 
                "message": "Final MP4 အဆင်သင့်ပါပြီ", 
                "download_url": f"/api/download/{out_file.name}", 
                "thumb_url": thumb_download_url,
                "duration": final_dur
            })

            mins, secs = divmod(final_dur, 60)
            
            # အသံအမည်ကို အင်္ဂလိပ်လိုမပို့ဘဲ မြန်မာလို ပြောင်းလဲပေးခြင်း
            VOICE_NAMES_MM = {
                'Nilar': 'နီလာ (အမျိုးသမီး)', 'Thiha': 'သီဟ (အမျိုးသား)',
                'Ava': 'အေးမြတ်', 'Andrew': 'အောင်ဒင်',
                'Emma': 'အိအိမွန်', 'Brian': 'ဘုန်းမြတ်',
                'Vivienne': 'ဝေဝေခိုင်', 'Remy': 'ရဲရင့်အောင်',
                'Seraphina': 'စန္ဒီ', 'Florian': 'ဖြိုးမင်း',
                'Xiaoxiao': 'ဆုဆုလှိုင်', 'Giuseppe': 'ဂျော်နီ'
            }
            display_voice = VOICE_NAMES_MM.get(voice_choice, voice_choice)
            
            msg_body = (
                f"🎬 <b>Video Recap ပြီးစီးပါပြီ!</b>\n\n"
                f"👤 <b>အသုံးပြုသူ:</b> {user_name}\n"
                f"📁 <b>ဖိုင်အမည်:</b> {v_title}\n"
                f"🎞 <b>အမျိုးအစား:</b> {recap_type_val}\n"
                f"⏱ <b>ကြာချိန်:</b> {mins} မိနစ် {secs} စက္ကန့်\n"
                f"⚡️ <b>Speed:</b> {speed_val}x\n"
                f"🗣 <b>အသံ:</b> {display_voice}\n"
                f"💾 <b>ဖိုင်အရွယ်အစား:</b> {out_size_mb} MB"
            )
            
            send_telegram_alert(msg_body)

            # Bot ထဲသို့ ဒေါင်းလုဒ်လင့်ခ်နှင့် ခလုတ် မပျက်မကွက် ရောက်စေရန် ပို့ခြင်း
            user_email = member.get("email", "").strip().lower()
            bind_info = load_data("tg_bind", user_email) or load_json_from_gcs(f"data/tg_bind_{user_email}.json")
            user_chat_id = bind_info.get("chat_id", "") if bind_info else ""
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            base_url = os.getenv("BASE_URL", "https://oneteamrecap.site").strip()
            
            if user_chat_id and bot_token:
                try:
                    direct_dl_url = f"{base_url}/api/download/{out_file.name}"
                    full_msg = f"{msg_body}\n\n📥 <b>ဒေါင်းလုဒ်လင့်ခ်များ:</b>\n{direct_dl_url}"
                    
                    # ဗီဒီယို ဒေါင်းရန် ခလုတ်
                    buttons = [
                        [{"text": "📥 Download Video (100% Quality)", "url": direct_dl_url}]
                    ]
                    
                    # ကာဘာပုံ ရှိပါက ကာဘာပုံ ဒေါင်းရန် ခလုတ်ပါ ထပ်ထည့်မည်
                    if thumb_download_url:
                        full_thumb_url = f"{base_url}{thumb_download_url}"
                        buttons.append([{"text": "🖼 ကာဘာပုံ (Thumbnail) ဒေါင်းရန်", "url": full_thumb_url}])
                        
                    keyboard = {
                        "inline_keyboard": buttons
                    }
                    
                    payload_data = {
                        "chat_id": user_chat_id,
                        "text": full_msg,
                        "parse_mode": "HTML",
                        "reply_markup": keyboard
                    }
                    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload_data, timeout=20)
                except Exception as tg_err:
                    print(f"Telegram Bot Send Error: {tg_err}")

        except Exception as exc:
            db.record_failure(member, duration_seconds, entitlement)
            update_job(job_id, {"status": "error", "error": str(exc), "message": "Video ထုတ်လုပ်မှု မအောင်မြင်ပါ"})
            db._request("PATCH", f"/rest/v1/jobs?job_id=eq.{job_id}", headers=db._service_headers(), json={
            "status": "error", "message": "Video ထုတ်လုပ်မှု မအောင်မြင်ပါ"
            })
        
            user_name = member.get("user_metadata", {}).get("full_name") or member.get("email", "").split("@")[0]
            
            # Admin Group သို့ Error ပို့ရန်
            send_telegram_alert(f"⚠️ <b>Video Recap ထုတ်လုပ်မှု မအောင်မြင်ပါ!</b>\n\n👤 <b>အသုံးပြုသူ:</b> {user_name}\n📁 <b>ဖိုင်အမည်:</b> {proj.get('filename', 'video.mp4')}\n❌ <b>Error:</b> <code>{str(exc)[-250:]}</code>")

            # User Chat ဆီသို့ Error တိုက်ရိုက်ပို့ရန်
            user_email = member.get("email", "").strip().lower()
            bind_info = load_data("tg_bind", user_email) or load_json_from_gcs(f"data/tg_bind_{user_email}.json")
            user_chat_id = bind_info.get("chat_id", "") if bind_info else ""
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            
            if user_chat_id and bot_token:
                try:
                    # User နားလည်လွယ်မည့် စာသားဖြင့် ပြင်ဆင်ပို့ပေးခြင်း
                    user_error_msg = f"⚠️ <b>လူကြီးမင်း၏ Video Recap ထုတ်လုပ်မှု မအောင်မြင်ပါ။</b>\n\n📁 <b>ဖိုင်အမည်:</b> {proj.get('filename', 'video.mp4')}\n❌ <b>အကြောင်းရင်း:</b> {str(exc)}\n\n<i>ခေတ္တစောင့်ဆိုင်းပြီးနောက် ထပ်မံကြိုးစားကြည့်ပါခင်ဗျာ။</i>"
                    
                    requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={
                        "chat_id": user_chat_id,
                        "text": user_error_msg,
                        "parse_mode": "HTML"
                    }, timeout=20)
                except Exception as tg_err:
                    print(f"Telegram Bot Send Error to User: {tg_err}")

    threading.Thread(target=task_worker, daemon=True).start()
    return load_data("job", job_id)

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    db = membership.SupabaseMembership()
    records = db._request("GET", f"/rest/v1/jobs?job_id=eq.{job_id}&limit=1", headers=db._service_headers())
    if records:
        r = records[0]
        return {
            "id": r.get("job_id"),
            "status": r.get("status"),
            "progress": r.get("progress"),
            "message": r.get("message"),
            "step": "render" if r.get("progress", 0) > 80 else "voice",
            "download_url": r.get("download_url"),
            "thumb_url": r.get("thumb_url"),
            "error": r.get("message") if r.get("status") == "error" else None
        }
    job = load_data("job", job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    update_job(job_id, {"status": "cancelled", "message": "လုပ်ငန်းစဉ်ကို ပယ်ဖျက်လိုက်ပါသည်"})
    return {"status": "ok"}

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    try:
        bucket = get_bucket()
        blob = bucket.blob(f"outputs/{filename}")
        if blob.exists():
            blob.content_disposition = f'attachment; filename="{filename}"'
            blob.content_type = 'application/octet-stream'
            blob.patch()
            try: blob.make_public()
            except: pass
            return RedirectResponse(url=blob.public_url)
    except: pass
    local_path = OUTPUT_DIR / filename
    if local_path.exists() and local_path.stat().st_size > 0:
        return FileResponse(str(local_path), media_type="application/octet-stream", filename=filename, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    raise HTTPException(status_code=404, detail="ဖိုင် ရှာမတွေ့ပါ။")

@app.get("/api/history")
async def get_history(authorization: Optional[str] = Header(None)):
    db = membership.SupabaseMembership()
    try: member = db.member_from_token(extract_token(authorization))
    except Exception: raise HTTPException(status_code=401, detail="Session expired")
    
    subject = member.get("google_subject", "")
    records = db._request("GET", f"/rest/v1/jobs?google_subject=eq.{subject}&order=created_at.desc", headers=db._service_headers()) or []
    
    from datetime import datetime
    now_utc = datetime.utcnow()
    history_list = []
    
    for r in records:
        status = r.get("status")
        
        try:
            created_str = str(r.get("created_at", ""))
            clean_str = created_str.replace("Z", "").split("+")[0].split(".")[0]
            created_dt = datetime.fromisoformat(clean_str) 
            age_hours = (now_utc - created_dt).total_seconds() / 3600
            
            # အောင်မြင်တာရော၊ မအောင်မြင်တာပါမကျန် အားလုံးကို (၂၄) နာရီကျော်ရင် အပြီးဖျက်မည်
            if age_hours >= 24:
                db._request("DELETE", f"/rest/v1/jobs?job_id=eq.{r.get('job_id')}", headers=db._service_headers())
                continue # ဖျက်ပြီးသားဖြစ်၍ UI သို့ ပြန်မပို့တော့ပါ
        except Exception:
            pass

        history_list.append({
            "job_id": r.get("job_id"),
            "filename": r.get("filename", "Video Recap"),
            "status": "success" if status == "done" else ("fail" if status in ["error", "cancelled"] else "running"),
            "duration": r.get("duration", 0),
            "date": str(r.get("created_at", ""))[:10],
            "download_url": r.get("download_url", ""),
            "thumb_url": r.get("thumb_url", ""),
            "progress": r.get("progress", 0),
            "message": r.get("message", "အမည်မသိ Error")
        })
    return history_list

# ---------------- Live Dashboard API ----------------
@app.get("/api/owner/jobs-history")
async def api_owner_jobs_history():
    jobs = []
    try:
        for f in UPLOAD_DIR.glob("job_*.json"):
            try:
                content = f.read_text(encoding="utf-8").strip()
                if content: jobs.append(json.loads(content))
            except: pass
        jobs = sorted(jobs, key=lambda x: x.get("created_at", 0), reverse=True)
        return jobs[:100]
    except Exception:
        return []

# ပြင်ဆင်ချက်: Dashboard သစ်အတွက် Database မှတ်တမ်းအပြည့်အစုံ ဆွဲထုတ်ပေးမည့် API
@app.get("/api/owner/full-history")
async def api_owner_full_history():
    try:
        db = membership.SupabaseMembership()
        records = db._request("GET", "/rest/v1/jobs?select=job_id,google_subject,filename,status,duration,created_at&order=created_at.desc&limit=300", headers=db._service_headers()) or []
        
        for r in records:
            job_data = load_data("job", r.get("job_id"))
            if job_data:
                # UUID အစား Email ကို ထည့်ပေးခြင်း
                user_email = job_data.get("user_email", "")
                if user_email:
                    r["google_subject"] = user_email
                dur = job_data.get("duration", r.get("duration", 0))
            else:
                dur = r.get("duration", 0)
                
            # ကြာချိန်ကို မိနစ်/စက္ကန့် ပုံစံချခြင်း
            dur = int(dur)
            mins, secs = divmod(dur, 60)
            if mins > 0:
                r["duration"] = f"{mins}m {secs}"
            else:
                r["duration"] = dur
                
        return records
    except Exception as e:
        return []
# ----------------------------------------------------

def file_cleanup_worker():
    while True:
        now = time.time()
        for folder in [UPLOAD_DIR, OUTPUT_DIR]:
            for f in folder.iterdir():
                try:
                    if f.is_file() and (now - f.stat().st_mtime > 14400): f.unlink(missing_ok=True)
                except: pass
        time.sleep(3600)
import threading
threading.Thread(target=file_cleanup_worker, daemon=True).start()

class AdminAction(BaseModel):
    request_id: int

class ExtendUserAction(BaseModel):
    email: str
    days: int

def get_admin_db():
    return membership.SupabaseMembership()

@app.get("/api/owner/metrics")
async def api_owner_metrics():
    try:
        return get_admin_db().owner_overview()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/owner/approve-payment")
async def api_approve_payment(action: AdminAction):
    try:
        get_admin_db().approve_payment(action.request_id, actor="owner")
        return {"message": "အတည်ပြုပြီးပါပြီ"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/owner/reject-payment")
async def api_reject_payment(action: AdminAction):
    try:
        get_admin_db().reject_payment(action.request_id, actor="owner")
        return {"message": "ပယ်ဖျက်ပြီးပါပြီ"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/owner/extend-user")
async def api_extend_user(action: ExtendUserAction):
    try:
        get_admin_db().extend_user_plan(action.email, action.days, actor="owner")
        return {"message": "ရက်တိုးပေးပြီးပါပြီ"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class OwnerSettingsModel(BaseModel):
    free_limit_enabled: bool = True
    free_daily_limit: int = 1
    free_max_seconds: int = 300
    free_allowed_type: str = "Movie Recap"
    free_allow_voice_clone: bool = False

@app.get("/api/owner/settings")
async def api_get_owner_settings():
    return load_data("settings", "owner") or {
        "free_limit_enabled": True, "free_daily_limit": 1,
        "free_max_seconds": 300, "free_allowed_type": "Movie Recap", "free_allow_voice_clone": False
    }

@app.post("/api/owner/settings")
async def api_save_owner_settings(settings: OwnerSettingsModel, authorization: Optional[str] = Header(None)):
    db = membership.SupabaseMembership()
    try:
        member = db.member_from_token(extract_token(authorization))
        if not member.get("is_admin"): raise HTTPException(status_code=403, detail="Unauthorized")
        save_data("settings", "owner", settings.dict())
        return {"message": "Settings saved"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)))