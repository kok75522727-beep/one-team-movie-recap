"""Server-enforced One Team memberships and manual-payment workflow.

The browser only presents offers. It never decides the effective plan, payment
amount, account role, export quota, or which Gemini key may be used.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import requests


SIMPLE_MAX_SECONDS = 0  # No customer-facing minute cap.
SIMPLE_TRIAL_DAILY_LIMIT = 1
SIMPLE_TRIAL_DAYS = 7
PAID_MAX_SECONDS = 0  # No customer-facing minute cap.
CREDIT_MAX_SECONDS = 0  # Legacy compatibility; credit packs are disabled.
CREDIT_BASE_SECONDS = 0
CREDITS_FOR_BASE_VIDEO = 0
CREDITS_PER_EXTRA_MINUTE = 0

# Only these server-side records are valid purchase offers. Changing a value in
# a browser request cannot alter an amount, quota, or API-key route.
VIP_TIERS = {
    "simple_vip": {
        "label": "Simple VIP",
        "price": 15000,
        "daily_limit": 3,
        "days": 30,
        "key_mode": "user",
        "description": "User Gemini API Key · Gemini Voice 10 မျိုး · Microsoft Voice 2 မျိုး · Video စိတ်ကြိုက်ထုတ် · တစ်နေ့ 3 ပုဒ်",
        "gemini_voice_count": 10,
        "microsoft_voice_count": 2,
    },
    "vip": {
        "label": "VIP",
        "price": 30000,
        "daily_limit": 3,
        "days": 30,
        "key_mode": "user",
        "description": "User Gemini API Key · Gemini Voice 10 မျိုး · Microsoft Voice 12 မျိုး · Video စိတ်ကြိုက်ထုတ် · တစ်နေ့ 3 ပုဒ်",
        "gemini_voice_count": 10,
        "microsoft_voice_count": 12,
    },
}

# Credit packs are disabled. Legacy columns/RPCs remain in Supabase so old
# history is preserved, but current exports never request or deduct credits.
CREDIT_PACKS: dict[str, dict[str, int]] = {}

DEFAULT_PAYMENT_DESTINATIONS = {
    "KBZPay": {"phone": "09670132806", "account_name": "Nay Lin Aung"},
    "WavePay": {"phone": "90670132806", "account_name": "Nay Lin Aung"},
}
PAYMENT_RECEIPT_BUCKET = "payment-receipts"
MAX_PAYMENT_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class MembershipError(RuntimeError):
    """A customer-facing access, plan, or membership configuration error."""


@dataclass(frozen=True)
class Entitlement:
    route: str  # trial, user_key, server_key, credits, owner
    plan: str  # simple or pro for usage rows
    tier: str
    key_mode: str  # user or server
    credits_required: int = 0


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def myanmar_export_day(now: datetime | None = None) -> str:
    """Asia/Yangon has no daylight-saving changes and remains UTC+06:30."""
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
    expiry = _parse_time(member.get("plan_expires_at"))
    if expiry and expiry <= (now or datetime.now(timezone.utc)):
        return "none"
    return plan


def simple_trial_expired(member: dict[str, Any], now: datetime | None = None) -> bool:
    if str(member.get("plan") or "").lower() != "simple":
        return False
    expiry = _parse_time(member.get("plan_expires_at"))
    return bool(expiry and expiry <= (now or datetime.now(timezone.utc)))


def simple_trial_days_remaining(member: dict[str, Any], now: datetime | None = None) -> int:
    if effective_plan(member, now) != "simple":
        return 0
    expiry = _parse_time(member.get("plan_expires_at"))
    if not expiry:
        return 0
    seconds = (expiry - (now or datetime.now(timezone.utc))).total_seconds()
    return max(0, math.ceil(seconds / 86400))


def credit_balance(member: dict[str, Any], now: datetime | None = None) -> int:
    if str(member.get("status") or "") != "active":
        return 0
    expiry = _parse_time(member.get("credit_expires_at"))
    if expiry and expiry <= (now or datetime.now(timezone.utc)):
        return 0
    try:
        return max(0, int(member.get("credit_balance") or 0))
    except (TypeError, ValueError):
        return 0


def credits_for_duration(duration_seconds: float) -> int:
    """Current plans do not use credit packs."""
    return 0


def tier_for(member: dict[str, Any]) -> str:
    tier = str(member.get("subscription_tier") or "").lower()
    # Existing old Start/Creator/Studio records are intentionally routed to the
    # stricter server-key VIP offer until the owner reviews their migration.
    return tier if tier in VIP_TIERS else "vip"


def is_owner(member: dict[str, Any]) -> bool:
    configured_email = _env("ONE_TEAM_ADMIN_EMAIL", "MEMBERSHIP_ADMIN_EMAIL").lower()
    email = str(member.get("email") or "").lower()
    return bool(configured_email and email and email == configured_email and str(member.get("role") or "") == "admin")


def daily_quota_bonus(member: dict[str, Any], now: datetime | None = None) -> int:
    expiry = _parse_time(member.get("quota_bonus_expires_at"))
    current = now or datetime.now(timezone.utc)
    if expiry and expiry <= current:
        return 0
    try:
        return max(0, int(member.get("daily_quota_bonus") or 0))
    except (TypeError, ValueError):
        return 0


def key_mode_for(member: dict[str, Any]) -> str:
    if effective_plan(member) in {"simple", "pro"}:
        return "user"
    return ""


def evaluate_export(member: dict[str, Any], duration_seconds: float, successful_today: int) -> Entitlement:
    """Enforce plan limits before Gemini or FFmpeg work starts."""
    seconds = max(0.0, float(duration_seconds or 0))
    if seconds <= 0:
        raise MembershipError("Video duration မမှန်ပါ")
    if is_owner(member):
        return Entitlement("owner", "pro", "owner", "server")
    plan = effective_plan(member)
    if plan == "none":
        if simple_trial_expired(member):
            raise MembershipError("Simple Trial 7 ရက်ကုန်သွားပါပြီ။ Plan ဝယ်ပြီးမှ Video Export လုပ်ပါ")
        raise MembershipError("Plan သက်တမ်းကုန်သွားပါပြီ။ Renew လုပ်ပါ")
    if plan == "simple":
        if successful_today >= SIMPLE_TRIAL_DAILY_LIMIT + daily_quota_bonus(member):
            raise MembershipError("လူသုံးများနေပါသည်။ Chat မှာပြောပါ။")
        return Entitlement("trial", "simple", "trial", "user")
    if plan == "pro":
        tier = tier_for(member)
        offer = VIP_TIERS[tier]
        if successful_today >= int(offer["daily_limit"]) + daily_quota_bonus(member):
            raise MembershipError("လူသုံးများနေပါသည်။ Chat မှာပြောပါ။")
        # Gemini Script and Gemini voices use the customer's key. Microsoft
        # voices are rendered by the server-side Azure key in media_engine.
        return Entitlement("user_key", "pro", tier, "user")
    raise MembershipError("Account plan မမှန်ပါ")


class SupabaseMembership:
    """REST adapter for the legacy One Team Supabase membership/payment schema."""

    def __init__(self) -> None:
        self.url = _env("MEMBERSHIP_SUPABASE_URL", "SUPABASE_URL").rstrip("/")
        self.anon_key = _env("MEMBERSHIP_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY")
        self.service_key = _env(
            "MEMBERSHIP_SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
        )

    @property
    def ready(self) -> bool:
        return bool(self.url and self.anon_key and self.service_key)

    def public_config(self) -> dict[str, str]:
        if not self.url or not self.anon_key:
            return {"enabled": "false"}
        return {"enabled": "true", "url": self.url, "anon_key": self.anon_key}

    def public_payment_info(self) -> dict[str, Any]:
        return {
            "plans": [
                {"key": key, "label": str(value["label"]), "price": int(value["price"]), "description": str(value["description"])}
                for key, value in VIP_TIERS.items()
            ],
            "destinations": self.payment_destinations(),
        }

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
        if not self.ready:
            raise MembershipError("Account Database setting မပြည့်သေးပါ။ Admin ကိုဆက်သွယ်ပါ")
        headers = {"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}", "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, json: Any = None, timeout: int = 20) -> Any:
        try:
            response = requests.request(method, f"{self.url}{path}", headers=headers, json=json, timeout=(8, timeout))
        except requests.RequestException as exc:
            raise MembershipError("Account Database ကိုခဏမဆက်သွယ်နိုင်ပါ") from exc
        if not response.ok:
            raise MembershipError("Account Database စစ်ဆေးမရသေးပါ။ ခဏနောက်ပြန်စမ်းပါ")
        try:
            return response.json()
        except ValueError:
            return None

    def _raw_request(self, method: str, path: str, *, headers: dict[str, str], data: bytes, timeout: int = 25) -> Any:
        try:
            response = requests.request(method, f"{self.url}{path}", headers=headers, data=data, timeout=(8, timeout))
        except requests.RequestException as exc:
            raise MembershipError("Receipt ပုံကိုခဏမသိမ်းနိုင်ပါ") from exc
        if not response.ok:
            raise MembershipError("Receipt ပုံကိုမသိမ်းမရသေးပါ။ ခဏနောက်ပြန်တင်ပါ")
        try:
            return response.json()
        except ValueError:
            return None

    def _make_owner_if_configured(self, member: dict[str, Any]) -> dict[str, Any]:
        configured_email = _env("ONE_TEAM_ADMIN_EMAIL", "MEMBERSHIP_ADMIN_EMAIL").lower()
        if not configured_email or str(member.get("email") or "").lower() != configured_email:
            return member
        if str(member.get("role") or "") == "admin":
            return member
        subject = quote(str(member.get("google_subject") or ""), safe="")
        if not subject:
            return member
        updated = self._request(
            "PATCH", f"/rest/v1/members?google_subject=eq.{subject}",
            headers=self._service_headers("return=representation"), json={"role": "admin", "status": "active"},
        ) or []
        return dict(updated[0]) if updated else member

    def member_from_token(self, access_token: str) -> dict[str, Any]:
        if not self.ready:
            raise MembershipError("Account Database setting မပြည့်သေးပါ။ Admin ကိုဆက်သွယ်ပါ")
        token = str(access_token or "").strip()
        if not token:
            raise MembershipError("Account ဝင်ပြီးမှ Video Export လုပ်ပါ")
        identity = self._request("GET", "/auth/v1/user", headers={"apikey": self.anon_key, "Authorization": f"Bearer {token}"})
        if not isinstance(identity, dict) or not identity.get("email"):
            raise MembershipError("Account session မမှန်ပါ။ ပြန်ဝင်ပါ")
        email = str(identity["email"]).strip().lower()
        subject = str(identity.get("id") or "")
        found = self._request("GET", f"/rest/v1/members?select=*&email=eq.{quote(email, safe='')}&limit=1", headers=self._service_headers()) or []
        member = dict(found[0]) if found else None
        if member is None and subject:
            found = self._request("GET", f"/rest/v1/members?select=*&google_subject=eq.{quote(subject, safe='')}&limit=1", headers=self._service_headers()) or []
            member = dict(found[0]) if found else None
        if member is None:
            trial_expires_at = (datetime.now(timezone.utc) + timedelta(days=SIMPLE_TRIAL_DAYS)).isoformat()
            owner_email = _env("ONE_TEAM_ADMIN_EMAIL", "MEMBERSHIP_ADMIN_EMAIL").lower()
            created = self._request(
                "POST", "/rest/v1/members", headers=self._service_headers("return=representation"),
                json={
                    "google_subject": subject,
                    "email": email,
                    "display_name": str((identity.get("user_metadata") or {}).get("display_name") or email.split("@", 1)[0]),
                    "status": "active",
                    "plan": "simple",
                    "subscription_tier": "trial",
                    "plan_expires_at": trial_expires_at,
                    "role": "admin" if email == owner_email else "member",
                },
            ) or []
            member = dict(created[0]) if created else None
        if member is None:
            raise MembershipError("Account ဖွင့်မရသေးပါ။ ခဏနောက်ပြန်ဝင်ပါ")
        member = self._make_owner_if_configured(member)
        member["effective_plan"] = "pro" if is_owner(member) else effective_plan(member)
        member["is_admin"] = is_owner(member)
        member["trial_days_remaining"] = simple_trial_days_remaining(member)
        member["trial_expired"] = simple_trial_expired(member)
        member["key_mode"] = "server" if is_owner(member) else key_mode_for(member)
        return member

    def successful_exports_today(self, member: dict[str, Any], plan: str) -> int:
        subject = quote(str(member.get("google_subject") or ""), safe="")
        if not subject:
            raise MembershipError("Account identity မမှန်ပါ။ ပြန်ဝင်ပါ")
        day = myanmar_export_day()
        rows = self._request(
            "GET", f"/rest/v1/export_usage?select=id&google_subject=eq.{subject}&plan=eq.{quote(plan, safe='')}&outcome=eq.success&export_day=eq.{day}",
            headers=self._service_headers(),
        ) or []
        return len(rows)

    def record_success(self, member: dict[str, Any], entitlement: Entitlement, duration_seconds: float) -> None:
        subject = str(member.get("google_subject") or "")
        if not subject:
            raise MembershipError("Account identity မမှန်ပါ။ ပြန်ဝင်ပါ")
        if entitlement.route == "credits":
            result = self._request(
                "POST", "/rest/v1/rpc/consume_member_credits", headers=self._service_headers(),
                json={"p_google_subject": subject, "p_credits": entitlement.credits_required, "p_note": f"Successful {round(duration_seconds)} second export"},
            )
            if not result:
                raise MembershipError("Credits ဖြတ်မရသေးပါ။ Final Video ကိုမသိမ်းပါ")
        if entitlement.route == "owner":
            return
        self._request(
            "POST", "/rest/v1/export_usage", headers=self._service_headers(),
            json={
                "google_subject": subject, "plan": entitlement.plan, "subscription_tier": entitlement.tier,
                "export_day": myanmar_export_day(), "source_duration_seconds": round(float(duration_seconds), 2), "outcome": "success",
            },
        )

    def record_failure(self, member: dict[str, Any], duration_seconds: float, entitlement: Entitlement | None = None) -> None:
        if not self.ready or not member.get("google_subject"):
            return
        try:
            self._request(
                "POST", "/rest/v1/export_usage", headers=self._service_headers(),
                json={
                    "google_subject": str(member["google_subject"]), "plan": entitlement.plan if entitlement else effective_plan(member),
                    "subscription_tier": entitlement.tier if entitlement else tier_for(member), "export_day": myanmar_export_day(),
                    "source_duration_seconds": round(float(duration_seconds), 2), "outcome": "failed",
                },
            )
        except MembershipError:
            pass

    def _payment_row_for_member(self, subject: str, status: str = "submitted") -> dict[str, Any] | None:
        rows = self._request(
            "GET", f"/rest/v1/payment_requests?select=*&google_subject=eq.{quote(subject, safe='')}&status=eq.{quote(status, safe='')}&order=submitted_at.desc&limit=1",
            headers=self._service_headers(),
        ) or []
        return dict(rows[0]) if rows else None

    def payment_status_for_member(self, member: dict[str, Any]) -> dict[str, Any] | None:
        subject = str(member.get("google_subject") or "")
        return self._payment_row_for_member(subject) if subject else None

    def _upload_receipt(self, subject: str, content: bytes, mime_type: str) -> str:
        if not content:
            return ""
        if len(content) > MAX_PAYMENT_RECEIPT_BYTES:
            raise MembershipError("Receipt ပုံက 5 MB အောက်ဖြစ်ရမယ်")
        extension = ALLOWED_RECEIPT_TYPES.get(mime_type.lower())
        if not extension:
            raise MembershipError("Receipt ကို JPG, PNG, WEBP ပုံသာတင်ပါ")
        safe_subject = re.sub(r"[^a-zA-Z0-9_-]", "", subject)[:80]
        if not safe_subject:
            raise MembershipError("Account identity မမှန်ပါ")
        key = f"{safe_subject}/{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:10]}.{extension}"
        headers = self._service_headers()
        headers["Content-Type"] = mime_type
        headers["x-upsert"] = "false"
        self._raw_request("POST", f"/storage/v1/object/{PAYMENT_RECEIPT_BUCKET}/{quote(key, safe='/')}", headers=headers, data=content)
        return key

    def create_payment_request(
        self, member: dict[str, Any], *, kind: str, offer_key: str, payment_method: str, transaction_id: str,
        receipt: bytes = b"", receipt_mime: str = "",
    ) -> dict[str, Any]:
        if kind != "plan":
            raise MembershipError("လက်ရှိ Plan မှာ Credit Pack မရှိပါ")
        offer = VIP_TIERS.get(offer_key)
        if offer is None:
            raise MembershipError("ရွေးထားတဲ့ Plan မမှန်ပါ")
        if payment_method not in self.payment_destinations():
            raise MembershipError("Payment Method ကိုရွေးပါ")
        subject = str(member.get("google_subject") or "")
        if not subject:
            raise MembershipError("Account identity မမှန်ပါ။ ပြန်ဝင်ပါ")
        transaction = re.sub(r"\s+", "", transaction_id or "")[:100]
        if len(transaction) < 4:
            raise MembershipError("Transaction ID ကိုမှန်မှန်ထည့်ပါ")
        if self._payment_row_for_member(subject):
            raise MembershipError("ငွေလွှဲအချက်အလက် စစ်ဆေးရန်ပို့ထားပြီးပါပြီ။ Admin အတည်ပြုမှုကိုစောင့်ပါ")
        receipt_key = self._upload_receipt(subject, receipt, receipt_mime) if receipt else ""
        row = {
            "google_subject": subject,
            "plan": "pro" if kind == "plan" else "credits",
            "request_kind": "plan",
            "requested_tier": offer_key,
            "requested_credits": 0,
            "amount_mmk": int(offer["price"]),
            "payment_method": payment_method,
            "transaction_id": transaction,
            "receipt_key": receipt_key,
            "status": "submitted",
        }
        created = self._request("POST", "/rest/v1/payment_requests", headers=self._service_headers("return=representation"), json=row) or []
        if not created:
            raise MembershipError("ငွေလွှဲအချက်အလက်မသိမ်းမရသေးပါ။ SQL setting ကိုစစ်ပါ")
        return dict(created[0])

    def _receipt_url(self, receipt_key: str) -> str:
        if not receipt_key:
            return ""
        response = self._request(
            "POST", f"/storage/v1/object/sign/{PAYMENT_RECEIPT_BUCKET}/{quote(str(PurePosixPath(receipt_key)), safe='/')}",
            headers=self._service_headers(), json={"expiresIn": 600},
        ) or {}
        signed = str(response.get("signedURL") or response.get("signedUrl") or "")
        return f"{self.url}/storage/v1{signed}" if signed.startswith("/") else signed

    def set_daily_quota_bonus(self, google_subject: str, extra_videos: int, days: int = 1) -> bool:
        bonus = max(0, min(1000, int(extra_videos)))
        duration = max(1, min(365, int(days)))
        updated = self._request(
            "PATCH", f"/rest/v1/members?google_subject=eq.{quote(str(google_subject), safe='')}",
            headers=self._service_headers("return=representation"),
            json={"daily_quota_bonus": bonus, "quota_bonus_expires_at": (datetime.now(timezone.utc) + timedelta(days=duration)).isoformat()},
        ) or []
        return bool(updated)

    def owner_overview(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        members = self._request("GET", "/rest/v1/members?select=email,google_subject,plan,subscription_tier,plan_expires_at,status,role,daily_quota_bonus,quota_bonus_expires_at", headers=self._service_headers()) or []
        active_paid = [row for row in members if effective_plan(dict(row), now) == "pro"]
        active_simple = [row for row in members if effective_plan(dict(row), now) == "simple"]
        usage_rows = self._request("GET", "/rest/v1/export_usage?select=plan,outcome&limit=10000", headers=self._service_headers()) or []
        usage_by_plan = {"simple": {"success": 0, "failed": 0}, "pro": {"success": 0, "failed": 0}}
        for row in usage_rows:
            plan_key = str(row.get("plan") or "").lower()
            outcome = str(row.get("outcome") or "").lower()
            if plan_key in usage_by_plan and outcome in usage_by_plan[plan_key]:
                usage_by_plan[plan_key][outcome] += 1
        pending = self._request("GET", "/rest/v1/payment_requests?select=*&status=eq.submitted&order=submitted_at.asc", headers=self._service_headers()) or []
        visible_pending = []
        for row in pending:
            item = dict(row)
            item["receipt_url"] = self._receipt_url(str(item.get("receipt_key") or ""))
            visible_pending.append(item)
        by_tier = {key: sum(1 for row in active_paid if tier_for(dict(row)) == key) for key in VIP_TIERS}
        return {
            "active_vip_count": len(active_paid),
            "active_simple_count": len(active_simple),
            "pending_payment_count": len(visible_pending),
            "active_by_tier": by_tier,
            "usage_by_plan": usage_by_plan,
            "members": [{"email": str(row.get("email") or ""), "google_subject": str(row.get("google_subject") or ""), "plan": str(row.get("plan") or ""), "tier": str(row.get("subscription_tier") or ""), "daily_quota_bonus": daily_quota_bonus(dict(row), now)} for row in members if effective_plan(dict(row), now) in {"simple", "pro"}],
            "payments": visible_pending,
        }

    def _member_by_subject(self, subject: str) -> dict[str, Any] | None:
        rows = self._request(
            "GET", f"/rest/v1/members?select=*&google_subject=eq.{quote(subject, safe='')}&limit=1", headers=self._service_headers(),
        ) or []
        return dict(rows[0]) if rows else None

    def _payment_by_id(self, request_id: int) -> dict[str, Any] | None:
        rows = self._request(
            "GET", f"/rest/v1/payment_requests?select=*&id=eq.{int(request_id)}&limit=1", headers=self._service_headers(),
        ) or []
        return dict(rows[0]) if rows else None

    def approve_payment(self, request_id: int, actor: str, note: str = "") -> bool:
        payment = self._payment_by_id(request_id)
        if payment is None or str(payment.get("status") or "") != "submitted":
            raise MembershipError("စစ်ဆေးရန် Payment Request မတွေ့ပါ")
        subject = str(payment.get("google_subject") or "")
        kind = str(payment.get("request_kind") or "plan")
        now = datetime.now(timezone.utc)
        if kind != "plan":
            raise MembershipError("လက်ရှိ Plan မှာ Credit Pack မရှိပါ")
        else:
            tier = str(payment.get("requested_tier") or "")
            offer = VIP_TIERS.get(tier)
            if offer is None:
                raise MembershipError("Plan မမှန်ပါ")
            member = self._member_by_subject(subject)
            current_expiry = _parse_time((member or {}).get("plan_expires_at"))
            start = current_expiry if current_expiry and current_expiry > now else now
            self._request(
                "PATCH", f"/rest/v1/members?google_subject=eq.{quote(subject, safe='')}",
                headers=self._service_headers(),
                json={
                    "status": "active", "plan": "pro", "subscription_tier": tier,
                    "plan_expires_at": (start + timedelta(days=int(offer["days"]))).isoformat(),
                    "approved_at": now.isoformat(), "admin_note": note.strip() or str(offer["label"]),
                },
            )
        updated = self._request(
            "PATCH", f"/rest/v1/payment_requests?id=eq.{int(request_id)}&status=eq.submitted",
            headers=self._service_headers("return=representation"),
            json={"status": "approved", "reviewed_at": now.isoformat(), "reviewed_by": actor, "admin_note": note.strip()},
        ) or []
        if not updated:
            raise MembershipError("Payment status ပြောင်းမရသေးပါ")
        return True

    def reject_payment(self, request_id: int, actor: str, note: str = "") -> bool:
        updated = self._request(
            "PATCH", f"/rest/v1/payment_requests?id=eq.{int(request_id)}&status=eq.submitted",
            headers=self._service_headers("return=representation"),
            json={
                "status": "rejected", "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_by": actor, "admin_note": note.strip() or "Rejected by admin",
            },
        ) or []
        if not updated:
            raise MembershipError("Payment status ပြောင်းမရသေးပါ")
        return True
