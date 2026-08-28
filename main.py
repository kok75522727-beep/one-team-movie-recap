"""One Team Movie Recap server.

The browser owns editor interactions. This server only receives files, starts
slow AI/render work once, and returns observable job progress.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Cookie, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from nicegui import app, ui
from pydantic import BaseModel, Field

from media_engine import GEMINI_VOICES, MICROSOFT_VOICES, generate_gemini_tts, generate_microsoft_tts, generate_script, probe_video, render_final
from membership import Entitlement, MembershipError, SupabaseMembership, evaluate_export


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
ASSETS = DATA / "assets"
EXPORTS = DATA / "exports"
for directory in (UPLOADS, ASSETS, EXPORTS):
    directory.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_BYTES = 200 * 1024 * 1024
VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska", "video/x-msvideo", "application/octet-stream"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
AUDIO_TYPES_PREFIX = "audio/"


@dataclass
class ProjectMedia:
    video_path: Path | None = None
    video_mime: str = "video/mp4"
    video_info: dict[str, float | int] = field(default_factory=dict)
    logo_path: Path | None = None
    music_path: Path | None = None


@dataclass
class ExportJob:
    id: str
    project_id: str
    status: str = "queued"
    step: str = "upload"
    progress: int = 4
    message: str = "Export စတင်ရန်ပြင်နေပါတယ်"
    error: str = ""
    output_path: Path | None = None
    cancelled: bool = False
    member: dict[str, Any] | None = None
    entitlement: Entitlement | None = None

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output_path", None)
        data.pop("member", None)
        data.pop("entitlement", None)
        if self.output_path and self.status == "done":
            data["download_url"] = f"/media/exports/{self.output_path.name}"
        return data


PROJECTS: dict[str, ProjectMedia] = {}
JOBS: dict[str, ExportJob] = {}
LOCK = threading.Lock()
MEMBERSHIP = SupabaseMembership()


class JobRequest(BaseModel):
    project_id: str = Field(min_length=8, max_length=100)
    settings: dict[str, Any]
    simple_api_key: str = Field(default="", max_length=300)


class PaymentReviewRequest(BaseModel):
    note: str = Field(default="", max_length=240)


class QuotaOverrideRequest(BaseModel):
    google_subject: str = Field(min_length=1, max_length=200)
    extra_videos: int = Field(ge=0, le=1000)
    days: int = Field(default=1, ge=1, le=365)


def clean_project_id(value: str) -> str:
    allowed = "".join(char for char in value if char.isalnum() or char in "-_")[:72]
    if len(allowed) < 8:
        raise HTTPException(status_code=400, detail="Project ID မမှန်ပါ")
    return allowed


def safe_name(filename: str, fallback: str) -> str:
    stem = "".join(char for char in Path(filename or fallback).stem if char.isalnum() or char in "-_")[:60] or fallback
    suffix = Path(filename or fallback).suffix.lower()[:10]
    return f"{stem}{suffix}"


async def store_upload(upload: UploadFile, folder: Path, max_bytes: int = MAX_VIDEO_BYTES) -> Path:
    filename = safe_name(upload.filename or "upload.bin", "upload")
    target = folder / f"{uuid.uuid4().hex}-{filename}"
    total = 0
    try:
        with target.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="200MB ထက်ကျော်နေပါတယ်")
                destination.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if total < 1024:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="ဖိုင်မပြည့်စုံပါ")
    return target


def update(job: ExportJob, *, step: str, progress: int, message: str) -> None:
    with LOCK:
        job.step, job.progress, job.message = step, progress, message


def cancelled(job: ExportJob) -> bool:
    if job.cancelled:
        job.status = "cancelled"
        job.message = "Export ကိုရပ်လိုက်ပါတယ်"
        return True
    return False


def resolved_api_key(request: JobRequest, entitlement: Entitlement) -> str:
    """Use the owner's server key only for Owner; customers must submit their own key."""
    if entitlement.route == "owner":
        key = os.getenv("ONE_TEAM_OWNER_GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Owner Gemini server key မသတ်မှတ်ရသေးပါ။ Admin ကိုဆက်သွယ်ပါ")
        return key
    key = request.simple_api_key.strip()
    if not key:
        raise RuntimeError("Gemini အသံ/Script အတွက် ကိုယ့် Gemini API Key ထည့်ပါ")
    return key


def process_job(job: ExportJob, request: JobRequest) -> None:
    try:
        with LOCK:
            media = PROJECTS.get(job.project_id)
        if not media or not media.video_path or not media.video_path.is_file():
            raise RuntimeError("Video ဖိုင်မတွေ့ပါ။ ပြန်တင်ပါ")
        if not job.member or not job.entitlement:
            raise RuntimeError("Account plan ကိုပြန်စစ်ပါ")
        api_key = resolved_api_key(request, job.entitlement)
        if cancelled(job):
            return
        update(job, step="upload", progress=12, message="Video information စစ်နေပါတယ်")
        settings = dict(request.settings)
        settings["logo_path"] = str(media.logo_path or "")
        settings["music_path"] = str(media.music_path or "")
        info = media.video_info or probe_video(media.video_path)
        if cancelled(job):
            return
        update(job, step="script", progress=30, message="Gemini Script ရေးနေပါတယ်")
        script = str(settings.get("script") or "").strip()
        if not script:
            script = generate_script(
                api_key, media.video_path, media.video_mime,
                str(settings.get("language") or "Myanmar"), int(settings.get("duration") or 60), str(settings.get("tone") or "Cinematic"),
            )
        settings["script"] = script
        if cancelled(job):
            return
        selected_voice = str(settings.get("voice") or "Nilar")
        if selected_voice in MICROSOFT_VOICES and job.entitlement.tier != "vip" and selected_voice not in {"Nilar", "Thiha"}:
            raise RuntimeError("ဒီ Microsoft Voice ကို Pro Plan မှာသာသုံးလို့ရပါတယ်")
        if selected_voice in GEMINI_VOICES:
            update(job, step="voice", progress=62, message="Gemini Voice ထုတ်နေပါတယ်")
            narration = generate_gemini_tts(script, selected_voice, api_key)
        else:
            update(job, step="voice", progress=62, message="Microsoft Voice ထုတ်နေပါတယ်")
            narration = generate_microsoft_tts(script, selected_voice)
        if cancelled(job):
            return
        update(job, step="render", progress=80, message="Blur၊ Subtitle နဲ့ Final MP4 ပေါင်းနေပါတယ်")
        output = EXPORTS / f"one-team-{job.id}.mp4"
        render_final(media.video_path, narration, settings, output)
        if cancelled(job):
            output.unlink(missing_ok=True)
            return
        MEMBERSHIP.record_success(job.member, job.entitlement, float(info["duration"]))
        with LOCK:
            job.output_path, job.status = output, "done"
            job.progress, job.message = 100, "Final MP4 အဆင်သင့်ပါပြီ"
    except Exception as exc:
        if job.cancelled:
            with LOCK:
                job.status, job.message = "cancelled", "Export ကိုရပ်လိုက်ပါတယ်"
            return
        if job.member:
            MEMBERSHIP.record_failure(job.member, float((media.video_info if 'media' in locals() else {}).get("duration") or 0), job.entitlement)
        with LOCK:
            job.status, job.error = "error", str(exc)
            job.message = "Video export မအောင်မြင်ပါ"


app.add_static_files("/static", STATIC)
app.add_static_files("/fonts", ROOT / "fonts")
app.add_static_files("/media", DATA)


def verified_browser_member(session_token: str | None, *, owner_only: bool = False) -> dict[str, Any] | None:
    """Validate an HTTP-only browser session before sending protected page shells."""
    try:
        member = MEMBERSHIP.member_from_token(str(session_token or ""))
        if owner_only and not bool(member.get("is_admin")):
            return None
        return member
    except MembershipError:
        return None


@app.post("/api/browser-session")
async def establish_browser_session(response: Response, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    """Exchange a browser-held Supabase token for a same-site HTTP-only gate cookie."""
    try:
        MEMBERSHIP.member_from_token(bearer_token(authorization))
    except MembershipError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.set_cookie(
        "one_team_session", bearer_token(authorization), max_age=3600, httponly=True,
        secure=not bool(os.getenv("ONE_TEAM_INSECURE_COOKIES")), samesite="lax", path="/",
    )
    return {"ok": True}


@app.delete("/api/browser-session")
async def clear_browser_session(response: Response) -> dict[str, bool]:
    response.delete_cookie("one_team_session", path="/")
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def editor_page(one_team_session: str | None = Cookie(default=None)) -> Response:
    if verified_browser_member(one_team_session) is None:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "editor.html", media_type="text/html")


@app.get("/login", include_in_schema=False)
async def login_page(one_team_session: str | None = Cookie(default=None)) -> Response:
    if verified_browser_member(one_team_session) is not None:
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html", media_type="text/html")


@app.get("/owner", include_in_schema=False)
async def owner_page(one_team_session: str | None = Cookie(default=None)) -> Response:
    if verified_browser_member(one_team_session, owner_only=True) is None:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "owner.html", media_type="text/html")


def bearer_token(authorization: str | None) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        return ""
    return authorization[len(prefix):].strip()


@app.get("/api/auth-config")
async def auth_config() -> dict[str, str]:
    """Return only the Supabase public configuration; the service key never leaves this server."""
    return MEMBERSHIP.public_config()


@app.get("/api/membership")
async def current_membership(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        member = MEMBERSHIP.member_from_token(bearer_token(authorization))
        pending = MEMBERSHIP.payment_status_for_member(member)
        return {
            "plan": str(member.get("effective_plan") or "none"),
            "tier": str(member.get("subscription_tier") or ""),
            "key_mode": str(member.get("key_mode") or ""),
            "trial_days_remaining": int(member.get("trial_days_remaining") or 0),
            "trial_expired": bool(member.get("trial_expired")),
            "is_admin": bool(member.get("is_admin")),
            "email": str(member.get("email") or ""),
            "credit_balance": int(member.get("credit_balance") or 0),
            "pending_payment": bool(pending),
        }
    except MembershipError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/api/plans")
async def plans() -> dict[str, Any]:
    """Public plan and payment destination details, without any secrets."""
    return MEMBERSHIP.public_payment_info()


@app.post("/api/payments")
async def submit_payment(
    kind: str = Form(...),
    offer_key: str = Form(...),
    payment_method: str = Form(...),
    transaction_id: str = Form(...),
    receipt: UploadFile | None = File(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        member = MEMBERSHIP.member_from_token(bearer_token(authorization))
        if bool(member.get("is_admin")):
            raise MembershipError("Owner account က payment request မတင်ရပါ")
        receipt_bytes = b""
        receipt_mime = ""
        if receipt is not None:
            receipt_mime = str(receipt.content_type or "").lower()
            receipt_bytes = await receipt.read()
            await receipt.close()
        payment = MEMBERSHIP.create_payment_request(
            member, kind=kind, offer_key=offer_key, payment_method=payment_method,
            transaction_id=transaction_id, receipt=receipt_bytes, receipt_mime=receipt_mime,
        )
        return {"id": payment.get("id"), "status": "submitted", "message": "ငွေလွှဲအချက်အလက်ပို့ပြီးပါပြီ။ Owner အတည်ပြုမှုကိုစောင့်ပါ"}
    except MembershipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def owner_member(authorization: str | None) -> dict[str, Any]:
    member = MEMBERSHIP.member_from_token(bearer_token(authorization))
    if not bool(member.get("is_admin")):
        raise MembershipError("Owner account ဖြင့်သာကြည့်လို့ရပါတယ်")
    return member


@app.get("/api/owner/overview")
async def owner_overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        owner_member(authorization)
        return MEMBERSHIP.owner_overview()
    except MembershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/owner/quota-override")
async def owner_quota_override(request: QuotaOverrideRequest, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    try:
        owner_member(authorization)
        if not MEMBERSHIP.set_daily_quota_bonus(request.google_subject, request.extra_videos, request.days):
            raise MembershipError("User account မတွေ့ပါ")
        return {"updated": True}
    except MembershipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/owner/payments/{request_id}/approve")
async def owner_approve_payment(request_id: int, review: PaymentReviewRequest, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    try:
        owner = owner_member(authorization)
        MEMBERSHIP.approve_payment(request_id, str(owner.get("email") or "Owner"), review.note)
        return {"approved": True}
    except MembershipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/owner/payments/{request_id}/reject")
async def owner_reject_payment(request_id: int, review: PaymentReviewRequest, authorization: str | None = Header(default=None)) -> dict[str, bool]:
    try:
        owner = owner_member(authorization)
        MEMBERSHIP.reject_payment(request_id, str(owner.get("email") or "Owner"), review.note)
        return {"rejected": True}
    except MembershipError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/video")
async def upload_video(project_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    project_id = clean_project_id(project_id)
    mime = (file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream").lower()
    if mime not in VIDEO_TYPES:
        raise HTTPException(status_code=415, detail="MP4, MOV, MKV, AVI, WEBM video ဖိုင်သာတင်ပါ")
    path = await store_upload(file, UPLOADS)
    try:
        info = probe_video(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    with LOCK:
        old = PROJECTS.get(project_id)
        if old and old.video_path:
            old.video_path.unlink(missing_ok=True)
        PROJECTS[project_id] = ProjectMedia(video_path=path, video_mime=mime, video_info=info)
    return info


@app.post("/api/assets")
async def upload_asset(project_id: str = Form(...), kind: str = Form(...), file: UploadFile = File(...)) -> dict[str, str]:
    project_id = clean_project_id(project_id)
    if kind not in {"logo", "music"}:
        raise HTTPException(status_code=400, detail="Asset type မမှန်ပါ")
    mime = (file.content_type or "").lower()
    if kind == "logo" and mime not in IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="PNG, JPG, WEBP logo ဖိုင်သာတင်ပါ")
    if kind == "music" and not mime.startswith(AUDIO_TYPES_PREFIX):
        raise HTTPException(status_code=415, detail="Audio ဖိုင်သာတင်ပါ")
    path = await store_upload(file, ASSETS, max_bytes=50 * 1024 * 1024)
    with LOCK:
        media = PROJECTS.setdefault(project_id, ProjectMedia())
        old = media.logo_path if kind == "logo" else media.music_path
        if old:
            old.unlink(missing_ok=True)
        if kind == "logo":
            media.logo_path = path
        else:
            media.music_path = path
    return {"path": str(path)}


@app.post("/api/jobs")
async def create_job(request: JobRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    project_id = clean_project_id(request.project_id)
    request.project_id = project_id
    with LOCK:
        media = PROJECTS.get(project_id)
    if not media or not media.video_path:
        raise HTTPException(status_code=400, detail="အရင် Video ဖိုင်တင်ပါ")
    try:
        member = MEMBERSHIP.member_from_token(bearer_token(authorization))
        used = MEMBERSHIP.successful_exports_today(member, "pro" if str(member.get("effective_plan")) == "pro" else "simple")
        entitlement = evaluate_export(member, float(media.video_info.get("duration") or 0), used)
        resolved_api_key(request, entitlement)
    except MembershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with LOCK:
        if any(existing.project_id == project_id and existing.status in {"queued", "running"} for existing in JOBS.values()):
            raise HTTPException(status_code=409, detail="ဒီ Video အတွက် Export လုပ်နေပြီးသားပါ။ ပြီးမှသာနောက်တစ်ကြိမ်နှိပ်ပါ")
    job = ExportJob(id=uuid.uuid4().hex, project_id=project_id, status="running", member=member, entitlement=entitlement)
    with LOCK:
        JOBS[job.id] = job
    threading.Thread(target=process_job, args=(job, request), daemon=True, name=f"export-{job.id[:8]}").start()
    return job.public()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Export job မတွေ့ပါ")
        return job.public()


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Export job မတွေ့ပါ")
        job.cancelled = True
        job.status, job.message = "cancelled", "Export ကိုရပ်လိုက်ပါတယ်"
        return job.public()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), title="One Team Movie Recap", reload=False, show=False)
