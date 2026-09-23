"""Thin Sarvam AI client: chat (sarvam-105b), text-to-speech (bulbul:v3), speech-to-text (saarika:v2.5).
All calls are synchronous with explicit timeouts; callers decide how to degrade."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
import uuid

from .config import settings

BASE = "https://api.sarvam.ai"
TTS_VOICE = settings.sarvam_voice
TTS_MODEL = "bulbul:v3"
CHAT_MODEL = "sarvam-105b-conversations"   # non-reasoning: ~0.4 s; sarvam-105b spends its whole budget reasoning
STT_MODEL = "saarika:v2.5"


def _lang(language: str) -> str:
    return "mr-IN" if language == "mr" else "hi-IN"


def _headers(extra: dict | None = None) -> dict:
    h = {"api-subscription-key": settings.sarvam_api_key}
    h.update(extra or {})
    return h


def available() -> bool:
    return bool(settings.sarvam_api_key)


def chat(system: str, user: str, timeout: float = 15.0, max_tokens: int = 600) -> str:
    body = json.dumps({"model": CHAT_MODEL, "temperature": 0.2, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(f"{BASE}/v1/chat/completions", data=body, headers=_headers({"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    content = d["choices"][0]["message"].get("content")
    if not content:
        raise RuntimeError(f"empty chat content (finish={d['choices'][0].get('finish_reason')})")
    return content


def tts(text: str, language: str, pace: float = 1.0, voice: str | None = None, timeout: float = 20.0,
        temperature: float | None = None) -> bytes:
    """Return WAV bytes (24000 Hz mono 16-bit, the model's native rate)."""
    body = json.dumps({"text": text, "language_code": _lang(language), "speaker": voice or settings.sarvam_voice,
                       "model": TTS_MODEL, "speech_sample_rate": 24000, "pace": pace,
                       "temperature": settings.sarvam_temperature if temperature is None else temperature}).encode()
    req = urllib.request.Request(f"{BASE}/text-to-speech", data=body, headers=_headers({"Content-Type": "application/json"}))
    for attempt in range(7):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode())
            return base64.b64decode(d["audios"][0])
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 6:
                time.sleep(1.5 * (2 ** attempt))       # 1.5, 3, 6, 12, 24, 48 s: Sarvam's TTS quota is per minute
                continue
            raise
    raise RuntimeError("tts retries exhausted")


def stt(wav_bytes: bytes, language: str, timeout: float = 20.0) -> str:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("model", STT_MODEL), ("language_code", _lang(language))):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode() + wav_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE}/speech-to-text", data=body, headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("transcript", "")
