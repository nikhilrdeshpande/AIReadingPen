"""'Your turn' step: record the child, transcribe with Sarvam, compare with the target word."""
from __future__ import annotations

import io
import re
import unicodedata
import wave
from difflib import SequenceMatcher

import numpy as np

from . import sarvam
from .config import settings
from .textnorm import normalize


def pick_input_device() -> int | None:
    import sounddevice as sd
    want = (settings.mic_device or "").lower()
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and want and want in d["name"].lower():
            return i
    return None   # system default


def mic_name() -> str:
    try:
        import sounddevice as sd
        dev = pick_input_device()
        d = sd.query_devices(dev if dev is not None else sd.default.device[0])
        return d["name"] + ("" if dev is not None else " (system default)")
    except Exception as e:  # noqa: BLE001
        return f"no microphone ({e})"


def mic_test(seconds: float = 1.0) -> dict:
    """Record briefly and report the peak level so the operator can confirm the mic is live."""
    try:
        _, peak = record(seconds)
        return {"ok": True, "device": mic_name(), "peak": round(peak, 3),
                "verdict": "silent" if peak < 0.01 else ("quiet" if peak < 0.05 else "ok")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "device": mic_name(), "error": str(e)[:160]}


def record(seconds: float, rate: int = 16000) -> tuple[bytes, float]:
    """Record mono 16-bit WAV from the microphone. Returns (wav_bytes, peak_level 0..1)."""
    import sounddevice as sd
    dev = pick_input_device()
    data = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16", device=dev)
    sd.wait()
    peak = float(np.abs(data).max()) / 32768.0
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(data.tobytes())
    return out.getvalue(), peak


def _strip(s: str) -> str:
    s = normalize(s)
    s = re.sub(r"[^ऀ-ॿ\s]", "", s)       # keep Devanagari + spaces
    return re.sub(r"\s+", " ", s).strip()


def compare(target: str, transcript: str) -> dict:
    """Exact word present -> match; else best token similarity -> 'close' above 0.75."""
    t = _strip(target)
    heard = _strip(transcript)
    tokens = heard.split() if heard else []
    if t in tokens or heard.replace(" ", "") == t.replace(" ", "") or (t in heard and " " in t):
        return {"match": True, "verdict": "correct", "score": 1.0, "heard": heard}
    best = max((SequenceMatcher(None, t, tok).ratio() for tok in tokens + [heard.replace(" ", "")]), default=0.0)
    return {"match": best >= 0.75, "verdict": "close" if best >= 0.75 else "try_again", "score": round(best, 2), "heard": heard}


def listen_and_check(target: str, language: str, seconds: float) -> dict:
    wav, peak = record(seconds)
    if peak < 0.01:
        return {"match": False, "verdict": "no_speech", "score": 0.0, "heard": "", "peak": peak}
    transcript = sarvam.stt(wav, language)
    r = compare(target, transcript)
    r["peak"] = round(peak, 3)
    r["transcript"] = transcript
    return r
