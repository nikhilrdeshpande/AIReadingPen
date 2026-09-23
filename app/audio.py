"""Audio adapter. Priority: cached asset -> Sarvam -> OpenAI -> visible failure.
Playback is on the Mac via afplay so the laptop speaker is used regardless of the browser."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .config import settings
from .manifest import Lesson

PRON_DICT_VERSION = "1"


def cache_key(provider: str, model: str, voice: str, language: str, script: str) -> str:
    h = hashlib.sha256(f"{provider}|{model}|{voice}|{language}|{script}|{PRON_DICT_VERSION}".encode()).hexdigest()
    return h[:16]


class AudioPlayer:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.last_source = ""
        self.last_asset = ""
        self.playing = False

    def warm(self) -> None:
        """Open the output device once so the first real clip starts without a device spin-up delay."""
        silent = Path(settings.audio_cache_dir) / "_silence.wav"
        if not silent.exists():
            import wave
            silent.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(silent), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 1600)
        subprocess.run(["afplay", str(silent)], check=False, timeout=5)

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            self.playing = False

    def play_file(self, path: str | Path, on_done=None) -> None:
        self.stop()
        with self._lock:
            self._proc = subprocess.Popen(["afplay", str(path)])
            self.playing = True
            proc = self._proc

        def _wait():
            proc.wait()
            with self._lock:
                if self._proc is proc:
                    self.playing = False
            if on_done:
                on_done()
        threading.Thread(target=_wait, daemon=True).start()

    # ---- routing ----
    def resolve(self, lesson: Lesson) -> tuple[str, str] | None:
        """Return (source, path) for the lesson audio, generating via a live provider if allowed. None on failure."""
        cache_dir = Path(settings.audio_cache_dir)
        if lesson.audio_asset:
            p = Path(lesson.audio_asset)
            if not p.is_absolute():
                p = (Path(settings.lesson_manifest).parent / p) if (Path(settings.lesson_manifest).parent / p).exists() else cache_dir / p.name
            if p.exists():
                return ("cache", str(p))
        if not settings.enable_live_tts:
            return None
        for provider in ("sarvam", "openai"):
            try:
                path = synthesize(provider, lesson.language, lesson.speech_script, cache_dir)
                if path:
                    return (provider, str(path))
            except Exception as e:  # noqa: BLE001
                print(f"[audio] {provider} failed: {e}")
        return None

    def speak_lesson(self, lesson: Lesson, on_done=None) -> dict:
        r = self.resolve(lesson)
        if r is None:
            self.last_source, self.last_asset = "none", ""
            if on_done:
                on_done()
            return {"source": "none", "asset": None, "error": "no cached audio and live TTS unavailable"}
        source, path = r
        self.last_source, self.last_asset = source, Path(path).name
        self.play_file(path, on_done=on_done)
        return {"source": source, "asset": Path(path).name}


# ---- live providers (used by scripts/gen_audio.py and by the live fallback) ----

def synthesize(provider: str, language: str, script: str, cache_dir: Path, voice: str | None = None) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    if provider == "sarvam":
        if not settings.sarvam_api_key:
            return None
        model, voice = "bulbul:v3", voice or "priya"
        key = cache_key(provider, model, voice, language, script)
        out = cache_dir / f"tts_{provider}_{key}.wav"
        if out.exists():
            return out
        body = json.dumps({"text": script, "target_language_code": "mr-IN" if language == "mr" else "hi-IN",
                           "speaker": voice, "model": model, "speech_sample_rate": 22050, "pace": 0.9}).encode()
        req = urllib.request.Request("https://api.sarvam.ai/text-to-speech", data=body,
                                     headers={"api-subscription-key": settings.sarvam_api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=settings.tts_timeout_s + 6) as r:
            data = json.loads(r.read().decode())
        import base64
        out.write_bytes(base64.b64decode(data["audios"][0]))
        return out
    if provider == "openai":
        if not settings.openai_api_key:
            return None
        model, voice = "gpt-4o-mini-tts", voice or "alloy"
        key = cache_key(provider, model, voice, language, script)
        out = cache_dir / f"tts_{provider}_{key}.wav"
        if out.exists():
            return out
        lang_name = "Marathi" if language == "mr" else "Hindi"
        body = json.dumps({"model": model, "voice": voice, "input": script, "response_format": "wav",
                           "instructions": f"Speak slowly and clearly in {lang_name} for a young child learning to read. "
                                           f"Pronounce each syllable distinctly. Pause briefly at each full stop."}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body,
                                     headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=settings.tts_timeout_s + 10) as r:
            out.write_bytes(r.read())
        return out
    if provider == "macos":
        # Diagnostic last resort: Hindi system voice reading Devanagari.
        key = cache_key(provider, "say", "Lekha", language, script)
        out = cache_dir / f"tts_{provider}_{key}.wav"
        if out.exists():
            return out
        aiff = out.with_suffix(".aiff")
        subprocess.run(["say", "-v", "Lekha", "-r", "150", "-o", str(aiff), script], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(out)], check=True)
        aiff.unlink(missing_ok=True)
        return out
    raise ValueError(provider)
