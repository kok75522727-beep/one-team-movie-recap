# External API Notes

## Gemini Text-to-Speech

Source: https://ai.google.dev/gemini-api/docs/speech-generation

The official Gemini TTS REST endpoint is `POST https://generativelanguage.googleapis.com/v1beta/interactions` with the `x-goog-api-key` header. The request uses model `gemini-3.1-flash-tts-preview`, `input` text, `response_format: {"type":"audio"}`, and `generation_config.speech_config` containing a prebuilt voice such as `Kore`. The response audio is returned in `output_audio.data` as base64 PCM; it must be wrapped as 24 kHz, mono, 16-bit WAV before FFmpeg rendering.

Source: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview

The model page lists text input and audio output, with the model code `gemini-3.1-flash-tts-preview`, and identifies the model as a preview audio-generation model.

## Azure Speech

Source: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech

Microsoft Speech TTS is performed server-side with an SSML request to the regional `cognitiveservices/v1` endpoint, authenticated with `Ocp-Apim-Subscription-Key` and an output format header. The key must remain server-only.
