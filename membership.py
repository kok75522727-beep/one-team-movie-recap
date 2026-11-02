"""Server-enforced One Team memberships and manual-payment workflow."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

MAX_VIDEO_SECONDS = 600  # ၁၀ မိနစ် အကန့်အသတ်
SIMPLE_TRIAL_DAYS = 3    

# VIP Plan တစ်ခုတည်းသာ ထားရှိခြင်း
VIP_TIERS = {
    "vip_monthly": {
        "label": "VIP Monthly Plan",
        "price": 25000,
        "days": 30,
        "description": "၁၀ မိနစ်အထိ Video များ အကန့်အသတ်မရှိ (Unlimited) ထုတ်လုပ်နိုင်သည်",
    }
}

DEFAULT_PAYMENT_DESTINATIONS = {
    "KBZPay": {"phone": "09670132806", "account_name": "Nay Lin Aung"},
    "WavePay": {"phone": "09670132806", "account_name": "Nay Lin Aung"},
}

class MembershipError(RuntimeError):
    pass

@dataclass(frozen=True)
class Entitlement:
    route: str  
    plan: str  
    tier: str
    key_mode: str  
    credits_required: int = 0

def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""

def myanmar_export_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=6, minutes=30))).date().isoformat()

def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

def effective_plan(member: dict[str, Any], now: datetime | None = None) -> str:
    if str(member.get("status") or "") != "active":
        return "none"
    plan = str(member.get("plan") or "none").lower()
    if plan not in {"simple", "pro"}:
        return "none"
    
    # Pro သမားများကိုသာ သက်တမ်းကုန်/မကုန် စစ်ဆေးပါမည်
    if plan == "pro":
        expiry = _parse_time(member.get("plan_expires_at"))
        if expiry and expiry <= (now or datetime.now(timezone.utc)):
            return "none"
            
    # Simple (Free) သမားများသည် သက်တမ်း လုံးဝမကုန်ပါ
    return plan

def is_owner(member: dict[str, Any]) -> bool:
    configured_email = _env("ONE_TEAM_ADMIN_EMAIL", "MEMBERSHIP_ADMIN_EMAIL").lower()
    email = str(member.get("email") or "").lower()
    return bool(configured_email and email and email == configured_email)

def evaluate_export(member: dict[str, Any], duration_seconds: float, total_successful: int, daily_successful: int = 0, settings: dict[str, Any] = None) -> Entitlement:
    seconds = max(0.0, float(duration_seconds or 0))
    
    if seconds <= 0:
        raise MembershipError("Video duration မမှန်ပါ")
        
    if is_owner(member):
        return Entitlement("owner", "pro", "owner", "user")
        
    plan = effective_plan(member)

    if plan == "pro":
        if seconds > MAX_VIDEO_SECONDS:
            raise MembershipError("Video ကြာချိန်သည် ၁၀ မိနစ် ထက် မပိုရပါ။")
        tier = str(member.get("subscription_tier") or "vip_monthly").lower()
        return Entitlement("user_key", "pro", tier, "user")
        
    if plan == "simple":
        settings = settings or {}
        is_limited = settings.get("free_limit_enabled", True)
        
        if is_limited:
            max_sec = settings.get("free_max_seconds", 300)
            daily_limit = settings.get("free_daily_limit", 1)
            
            if seconds > max_sec:
                raise MembershipError(f"အခမဲ့အသုံးပြုသူများသည် {max_sec//60} မိနစ်အထိသာ ထုတ်လုပ်ခွင့်ရှိပါသည်။")
            if daily_successful >= daily_limit:
                raise MembershipError(f"ယနေ့အတွက် အခမဲ့ထုတ်လုပ်ခွင့် ({daily_limit}) ပုဒ် ပြည့်သွားပါပြီ။ မနက်ဖြန်မှ ထပ်စမ်းပါ (သို့) VIP ဝယ်ယူပါ။")
        else:
            if seconds > MAX_VIDEO_SECONDS:
                raise MembershipError("Video ကြာချိန်သည် ၁၀ မိနစ် ထက် မပိုရပါ။")
            if total_successful >= 2:
                raise MembershipError("လူကြီးမင်း၏ အခမဲ့ထုတ်လုပ်ခွင့် (၂) ပုဒ် ပြည့်သွားပါပြီ။ ဆက်လက်အသုံးပြုလိုပါက Plan ဝယ်ယူပါ။")
                
        return Entitlement("trial", "simple", "trial", "user")
        
    raise MembershipError("Plan သက်တမ်းကုန်သွားပါပြီ။ ဝယ်ယူအားပေးပါ။")

class SupabaseMembership:
    def __init__(self) -> None:
        self.url = _env("MEMBERSHIP_SUPABASE_URL", "SUPABASE_URL").rstrip("/")
        self.anon_key = _env("MEMBERSHIP_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY")
        self.service_key = _env("MEMBERSHIP_SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")

    @property
    def ready(self) -> bool:
        return bool(self.url and self.anon_key and self.service_key)

    def public_config(self) -> dict[str, str]:
        return {"enabled": "true", "url": self.url, "anon_key": self.anon_key} if self.url and self.anon_key else {"enabled": "false"}

    def public_payment_info(self) -> dict[str, Any]:
        plans = [{"key": k, "label": v["label"], "price": v["price"], "description": v["description"], "kind": "plan"} for k, v in VIP_TIERS.items()]
        return {"plans": plans, "credit_packs": [], "destinations": self.payment_destinations()}

    def payment_destinations(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for method, defaults in DEFAULT_PAYMENT_DESTINATIONS.items():
            prefix = "ONE_TEAM_KBZPAY" if method == "KBZPay" else "ONE_TEAM_WAVEPAY"
            result[method] = {
                "phone": _env(f"{prefix}_PHONE") or str(defaults["phone"]),
                "account_name": _env(f"{prefix}_ACCOUNT_NAME") or str(defaults["account_name"]),
            }
        return result

    def _service_headers(self, prefer: str = "") -> dict[str, str]:
        headers = {"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}", "Content-Type": "application/json"}
        if prefer: headers["Prefer"] = prefer
        return headers

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, json: Any = None) -> Any:
        try:
            res = requests.request(method, f"{self.url}{path}", headers=headers, json=json, timeout=(8, 20))
            return res.json() if res.ok else None
        except Exception:
            return None

    def member_from_token(self, access_token: str) -> dict[str, Any]:
        token = str(access_token or "").strip()
        identity = self._request("GET", "/auth/v1/user", headers={"apikey": self.anon_key, "Authorization": f"Bearer {token}"})
        if not isinstance(identity, dict) or not identity.get("email"):
            raise MembershipError("Account session မမှန်ပါ။ ပြန်ဝင်ပါ")
        email = str(identity["email"]).strip().lower()
        subject = str(identity.get("id") or "")
        found = self._request("GET", f"/rest/v1/members?select=*&email=eq.{quote(email, safe='')}&limit=1", headers=self._service_headers()) or []
        member = dict(found[0]) if found else None
        if member is None:
            trial_expires_at = (datetime.now(timezone.utc) + timedelta(days=SIMPLE_TRIAL_DAYS)).isoformat()
            created = self._request("POST", "/rest/v1/members", headers=self._service_headers("return=representation"), json={
                "google_subject": subject, "email": email, "display_name": email.split("@", 1)[0],
                "status": "active", "plan": "simple", "subscription_tier": "trial", "plan_expires_at": trial_expires_at, "role": "member"
            }) or []
            member = dict(created[0]) if created else None
        
        member["effective_plan"] = "pro" if is_owner(member) else effective_plan(member)
        member["is_admin"] = is_owner(member)
        member["credit_balance"] = 0
        expiry = _parse_time(member.get("plan_expires_at"))
        member["trial_days_remaining"] = max(0, math.ceil((expiry - datetime.now(timezone.utc)).total_seconds() / 86400)) if expiry else 0
        return member

    def successful_exports_today(self, member: dict[str, Any], plan: str) -> int:
        # ဒီနေရာမှာ Today မဟုတ်တော့ဘဲ အကောင့်တစ်ခုလုံးရဲ့ စုစုပေါင်း (Total) ကို ရေတွက်ပါမည်
        subject = str(member.get("google_subject") or "")
        exports = self._request("GET", f"/rest/v1/export_usage?google_subject=eq.{quote(subject, safe='')}&outcome=eq.success&select=id", headers=self._service_headers()) or []
        return len(exports)

    def successful_exports_today_date(self, member: dict[str, Any], export_day: str) -> int:
        subject = str(member.get("google_subject") or "")
        exports = self._request("GET", f"/rest/v1/export_usage?google_subject=eq.{quote(subject, safe='')}&outcome=eq.success&export_day=eq.{quote(export_day, safe='')}&select=id", headers=self._service_headers()) or []
        return len(exports)

    def record_success(self, member: dict[str, Any], entitlement: Entitlement, duration_seconds: float) -> None:
        subject = str(member.get("google_subject") or "")
        if entitlement.route != "owner":
            self._request("POST", "/rest/v1/export_usage", headers=self._service_headers(), json={
                "google_subject": subject, "plan": entitlement.plan, "subscription_tier": entitlement.tier,
                "export_day": myanmar_export_day(), "source_duration_seconds": round(float(duration_seconds), 2), "outcome": "success"
            })

    def record_failure(self, member: dict[str, Any], duration_seconds: float, entitlement: Entitlement | None = None) -> None:
        subject = str(member.get("google_subject") or "")
        if entitlement and entitlement.route != "owner":
            self._request("POST", "/rest/v1/export_usage", headers=self._service_headers(), json={
                "google_subject": subject, "plan": entitlement.plan, "subscription_tier": entitlement.tier,
                "export_day": myanmar_export_day(), "source_duration_seconds": round(float(duration_seconds), 2), "outcome": "failed"
            })

    def create_payment_request(self, member: dict[str, Any], *, kind: str, offer_key: str, payment_method: str, transaction_id: str, receipt: bytes = b"", receipt_mime: str = "") -> dict[str, Any]:
        subject = str(member.get("google_subject") or "")
        offer = VIP_TIERS.get(offer_key)
        if not offer: raise MembershipError("ရွေးထားသော Plan မမှန်ပါ")
        row = {
            "google_subject": subject, "plan": "pro", "request_kind": "plan",
            "requested_tier": offer_key, "requested_credits": 0, "amount_mmk": int(offer["price"]),
            "payment_method": payment_method, "transaction_id": transaction_id, "status": "submitted"
        }
        created = self._request("POST", "/rest/v1/payment_requests", headers=self._service_headers("return=representation"), json=row) or []
        return dict(created[0])

    def owner_overview(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        members = self._request("GET", "/rest/v1/members?select=*", headers=self._service_headers()) or []
        pending = self._request("GET", "/rest/v1/payment_requests?select=*&status=eq.submitted&order=submitted_at.asc", headers=self._service_headers()) or []
        exports = self._request("GET", "/rest/v1/export_usage?select=outcome,google_subject", headers=self._service_headers()) or []
        
        success_total = 0
        fail_total = 0
        user_stats = {}

        # User တစ်ဦးချင်းစီ၏ အောင်မြင်/ကျရှုံးမှု တွက်ချက်ခြင်း
        for e in exports:
            subj = e.get("google_subject")
            outcome = e.get("outcome")
            if outcome == "success":
                success_total += 1
            else:
                fail_total += 1
                
            if subj:
                if subj not in user_stats:
                    user_stats[subj] = {"success": 0, "fail": 0}
                if outcome == "success":
                    user_stats[subj]["success"] += 1
                else:
                    user_stats[subj]["fail"] += 1
                    
        # Admin Dashboard တွင်ပြရန် User အချက်အလက်များ ထည့်သွင်းခြင်း
        for m in members:
            subj = m.get("google_subject")
            stats = user_stats.get(subj, {"success": 0, "fail": 0})
            m["success_count"] = stats["success"]
            m["fail_count"] = stats["fail"]
        
        return {
            "active_vip_count": len([m for m in members if effective_plan(m, now) == "pro"]),
            "active_simple_count": len([m for m in members if effective_plan(m, now) == "simple"]),
            "pending_payment_count": len(pending),
            "success_exports": success_total,
            "fail_exports": fail_total,
            "members": members,
            "payments": pending,
        }

    def approve_payment(self, request_id: int, actor: str, note: str = "") -> bool:
        payment = self._request("GET", f"/rest/v1/payment_requests?select=*&id=eq.{int(request_id)}&limit=1", headers=self._service_headers()) or []
        if not payment: raise MembershipError("Payment မတွေ့ပါ")
        p = payment[0]
        subject = str(p["google_subject"])
        now = datetime.now(timezone.utc)
        
        tier = str(p.get("requested_tier", "vip_monthly"))
        offer = VIP_TIERS.get(tier, VIP_TIERS["vip_monthly"])
        self._request("PATCH", f"/rest/v1/members?google_subject=eq.{quote(subject, safe='')}", headers=self._service_headers(), json={
            "status": "active", "plan": "pro", "subscription_tier": tier,
            "plan_expires_at": (now + timedelta(days=int(offer["days"]))).isoformat(), "admin_note": note or offer["label"]
        })
        self._request("PATCH", f"/rest/v1/payment_requests?id=eq.{int(request_id)}", headers=self._service_headers(), json={"status": "approved", "reviewed_at": now.isoformat(), "reviewed_by": actor})
        return True

    def reject_payment(self, request_id: int, actor: str, note: str = "") -> bool:
        self._request("PATCH", f"/rest/v1/payment_requests?id=eq.{int(request_id)}", headers=self._service_headers(), json={"status": "rejected", "reviewed_at": datetime.now(timezone.utc).isoformat(), "reviewed_by": actor})
        return True

    def extend_user_plan(self, email: str, add_days: int, actor: str) -> bool:
        found = self._request("GET", f"/rest/v1/members?select=*&email=eq.{quote(email.strip().lower(), safe='')}&limit=1", headers=self._service_headers()) or []
        if not found:
            raise MembershipError("User email ရှာမတွေ့ပါ")
        member = found[0]
        subject = str(member["google_subject"])
        
        current_expiry = _parse_time(member.get("plan_expires_at"))
        now = datetime.now(timezone.utc)
        if not current_expiry or current_expiry < now:
            new_expiry = now + timedelta(days=add_days)
        else:
            new_expiry = current_expiry + timedelta(days=add_days)
            
        self._request("PATCH", f"/rest/v1/members?google_subject=eq.{quote(subject, safe='')}", headers=self._service_headers(), json={
            "plan_expires_at": new_expiry.isoformat(),
            "admin_note": f"Extended {add_days} days by {actor}"
        })
        return True

    def demote_user(self, email: str, actor: str) -> bool:
        found = self._request("GET", f"/rest/v1/members?select=*&email=eq.{quote(email.strip().lower(), safe='')}&limit=1", headers=self._service_headers()) or []
        if not found:
            raise MembershipError("User email ရှာမတွေ့ပါ")
            
        member = found[0]
        subject = str(member["google_subject"])
        
        trial_expires_at = (datetime.now(timezone.utc) + timedelta(days=SIMPLE_TRIAL_DAYS)).isoformat()
        
        self._request("PATCH", f"/rest/v1/members?google_subject=eq.{quote(subject, safe='')}", headers=self._service_headers(), json={
            "status": "active", 
            "plan": "simple", 
            "subscription_tier": "trial",
            "plan_expires_at": trial_expires_at, 
            "admin_note": f"Demoted from VIP by {actor}"
        })
        return True

    def grant_manual_credits(self, email: str, credits: int, actor: str, note: str = "") -> bool:
        return True

    def refund_daily_count(self, email: str, count: int, actor: str) -> bool:
        return True