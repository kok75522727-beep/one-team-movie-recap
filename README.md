# One Team Movie Recap Studio

This is the NiceGUI + FastAPI replacement source bundle for the old Streamlit editor. The browser keeps the original video locally while the customer positions blur boxes, subtitles, and logo. Only one final export request sends the selected settings to the server for Gemini Script, Microsoft narration, and FFmpeg MP4 rendering.

## Confirmed One Team rules

| Area | Current rule |
|---|---|
| Login | A separate `/login` page supports Email/Password and Google sign-in through Supabase. The editor redirects there until a valid session exists. |
| Simple Trial | A new account gets **7 days**, **1 successful Final Video per day**. Gemini Script/voices use the customer's own Gemini key. |
| Simple VIP | **15,000 MMK / 30 days**, **3 successful Final Videos per day**, Video ကို စိတ်ကြိုက်ထုတ်လို့ရ. Gemini has 10 voices from the customer's key; Microsoft has 2 voices from the owner's Azure key. |
| VIP | **30,000 MMK / 30 days**, **3 successful Final Videos per day**, Video ကို စိတ်ကြိုက်ထုတ်လို့ရ. Gemini has 10 voices from the customer's key; Microsoft has 12 configured voices from the owner's Azure key. |
| Narration | Movie Recap Script is automatic. Gemini Script/Gemini voices use the submitted customer key; Microsoft voices use the server-only Azure key. There is no manual text-to-speech page. |
| Credits and duration | Credit packs are disabled. Customer-facing video-minute cap is not imposed; the server still enforces the daily export limit. Failed exports do not consume a successful-export slot. |
| Owner | The email in `ONE_TEAM_ADMIN_EMAIL` becomes Owner after login. Owner can review payments, see plan-separated member/export/failure counts, and grant a chosen extra daily-video allowance to one account. |
| Payment | Existing manual **KBZPay/WavePay** transfer flow: customer submits the chosen plan, transaction ID, and optional receipt image; Owner verifies and approves on `/owner`. |

The browser cannot select a paid plan, change a price, or approve payment. Voice selection is validated server-side: Gemini voices require the submitted customer key, while Microsoft voices use only the protected Azure server secret. The server reads a validated Supabase membership record for every export.

## Files

| Path | Purpose |
|---|---|
| `main.py` | FastAPI/NiceGUI routes, authenticated export job API, payments, and protected owner APIs. |
| `membership.py` | Trial/paid rules, customer-key routing, Supabase membership lookup, payment creation, owner approval, quota overrides, and plan-separated usage counting. |
| `media_engine.py` | Gemini 3.7 Flash Script generation, Microsoft Azure Speech narration, and FFmpeg final MP4 rendering. |
| `static/login.html` | Separate Email/Password and Google login page. |
| `static/editor.html` | Mobile video editor, plan-payment sheet, and direct overlay controls. |
| `static/owner.html` | Owner-only member/export/failure report, quota override control, and pending payment approval queue. |
| `supabase_one_team_plan_payment_migration.sql` | Safe Supabase migration for the legacy manual payment/credits flow. |

## Required Railway variables

Set all secret values in Railway **Variables**. Do not put them in GitHub, code, browser input, or chat.

```bash
# Supabase: public URL/key plus server-only role key
SUPABASE_URL="https://YOUR-PROJECT.supabase.co"
SUPABASE_ANON_KEY="..."
SUPABASE_SERVICE_ROLE_KEY="..."

# Owner-only identity and server-side Gemini key
ONE_TEAM_ADMIN_EMAIL="owner@example.com"
ONE_TEAM_OWNER_GEMINI_API_KEY="..."

# Microsoft Azure Speech narration for every plan
AZURE_SPEECH_KEY="..."
AZURE_SPEECH_REGION="southeastasia"

# Optional: replace the default stored KBZPay/WavePay recipient without editing code
ONE_TEAM_KBZPAY_PHONE="..."
ONE_TEAM_KBZPAY_ACCOUNT_NAME="..."
ONE_TEAM_WAVEPAY_PHONE="..."
ONE_TEAM_WAVEPAY_ACCOUNT_NAME="..."
```

`SUPABASE_SERVICE_KEY` and `MEMBERSHIP_SUPABASE_SERVICE_KEY` still work as aliases, but `SUPABASE_SERVICE_ROLE_KEY` is now accepted too. The service-role key must never be exposed to the browser.

## Supabase setup

1. In **Supabase → SQL Editor**, run `supabase_one_team_plan_payment_migration.sql` once, after the legacy `members` and `export_usage` schema already exists. It preserves existing payment data and adds the required fields safely.
2. In **Authentication → Providers**, enable Email and Google. Put the Google client ID and secret only in Supabase.
3. In **Authentication → URL Configuration**, add this exact redirect URL: `https://YOUR-RAILWAY-DOMAIN/login`. Replace the domain with the real Railway public domain. Add it as both Site URL/redirect URL as required by the Supabase screen.
4. Login with the email written in `ONE_TEAM_ADMIN_EMAIL`. That account can open `/owner`, view the number of currently active VIP members, view payment receipt links, then Approve or Reject each pending payment. Owner exports use `ONE_TEAM_OWNER_GEMINI_API_KEY` server-side and do not ask the owner to enter a Gemini key in the browser; Simple/VIP customer exports still require each customer's own Gemini key.

Google OAuth cannot work until the Google provider and the exact Railway `/login` redirect URL are configured in Supabase.

## Azure Speech setup

The app calls Azure Speech's server-side Text to Speech REST endpoint with SSML and MP3 output. Use an Azure Speech resource in the same region as `AZURE_SPEECH_REGION`; the code does not send `AZURE_SPEECH_KEY` to the user. The editor offers Myanmar Microsoft neural voice choices `Nilar` and `Thiha`. Check current voice availability in the selected Azure region before using live traffic.[1] [2]

## Test locally

Install FFmpeg/FFprobe first, then run:

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest -q
PYTHONPATH=. python3 tests/verify_media_render.py
```

The test suite deliberately does not call real Gemini, Supabase, Google, KBZPay/WavePay, or Azure providers. A real end-to-end export must be tested only after valid server variables, Supabase setup, and a test user are ready.

## Deployment limitation

Railway trial storage and in-memory jobs are temporary. They can serve the app for a test, but an app restart/redeploy can remove uploads, running jobs, and completed MP4 files. Membership/payment records survive only because they are in Supabase. Before charging regular customers, move uploads/exports to persistent object storage and replace the in-memory job map with shared persistent job state.

## References

[1]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech "Microsoft Azure Speech: Text to Speech REST API"
[2]: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support "Microsoft Azure Speech: Language and Voice Support"
