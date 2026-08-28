# GitHub Source Manifest

Commit this folder as-is, preserving its directories.

| Path | Required |
|---|---|
| `main.py` | Yes — FastAPI/NiceGUI application and API routes. |
| `membership.py` | Yes — authenticated Simple/VIP/credit checks. |
| `media_engine.py` | Yes — Gemini and FFmpeg final MP4 renderer. |
| `static/editor.html` | Yes — editor markup. |
| `static/editor.css` | Yes — One Team UI and browser font rules. |
| `static/editor.js` | Yes — direct touch overlays and export client. |
| `fonts/*` | Yes — Myanmar and Latin subtitle/logo fonts. The filenames are English-only for Phone ZIP compatibility. |
| `requirements.txt` | Yes — Python runtime and test packages. |
| `Procfile` | Recommended — generic Python hosting start command. |
| `.gitignore` | Recommended — prevents media, credentials and test cache commits. |
| `README.md` | Recommended — environment variables, migration and test instructions. |
| `tests/*` | Recommended — source rules and deterministic FFmpeg smoke test. |

Do not commit `.env`, `data/`, generated MP4s, uploaded media, API keys, Supabase service keys, or test-cache files.
