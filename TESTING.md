# Verification Record

## Local checks completed on 2026-08-27

| Check | Result |
|---|---|
| Python syntax | `main.py`, `media_engine.py`, `membership.py` and FFmpeg smoke script compile. |
| Policy and browser contracts | Tests cover the seven-day Simple Trial expiry, confirmed Start/Creator/Studio prices and limits, Simple own-key session storage, no browser plan field, verified membership gate, credit math, Gemini TTS REST contract, tabs, automatic-voice-only controls, overlay coordinate logic and cancel/double-export guards. |
| Final rendering | Deterministic FFmpeg smoke test passed with an H.264/AAC `720 × 1280` MP4. It exercises Burmese subtitles, mixed Myanmar/English text logo, blur, mirror, zoom, colour, background blur, pitch, audio mixing and visible-source coordinate mapping. |
| Browser interaction | The supplied `1000130858.mp4` sample uploaded at `4.8 MB · 209 sec`. Native video playback remained loaded while subtitle controls changed. Bundled `Pyidaungsu Bold` loaded successfully. Blur and Logo controls appeared directly over the video, while unrelated editor overlays remained hidden during feature positioning. A 16:9 letterbox geometry check matched the visible source rect. |

## Intentionally not represented as passed

No real Gemini Script/TTS or end-to-end Simple Trial export was performed because no customer Gemini key, production Supabase account, or user consent for provider usage was supplied. In particular, a Gemini 3.1 Flash TTS `429` quota response remains a provider-account condition; the code reports that condition and never switches a Simple Trial user to the VIP/owner key.

Run the commands in `README.md` again after configuring the real Supabase secrets and before production deployment.
