"""Teaching-audio builder: segment-wise TTS stitched with real silence gaps.

A lesson is spoken as separate segments (whole word, each chunk sound slowly, the blend, the word again, the
"your turn" prompt). Each segment is synthesized on its own and joined with silence so the child hears clear
spacing. Script comes from the deterministic template, optionally rewritten by Sarvam's LLM at generation time."""
from __future__ import annotations

import hashlib
import io
import json
import wave
from dataclasses import dataclass
from pathlib import Path

from . import sarvam
from .config import settings
from .manifest import Lesson

SCRIPT_VERSION = "3"   # bump when the template changes so cached audio is rebuilt


@dataclass
class Segment:
    text: str
    pace: float = 0.85
    gap_ms: int = 450     # silence after this segment


def template_segments(lesson: Lesson) -> list[Segment]:
    mr = lesson.language == "mr"
    word, chunks = lesson.word, lesson.teaching_chunks
    segs = [Segment(f"{word}.", 0.8, 600)]
    for c in chunks:
        segs.append(Segment(f"{c}.", 0.65, 500))            # each sound alone, slowly
    if len(chunks) > 1:
        segs.append(Segment(", ".join(chunks) + ".", 0.7, 500))   # the blend, sounds run together
    segs.append(Segment(f"{word}.", 0.8, 600))
    segs.append(Segment("आता तू म्हण." if mr else "अब तुम बोलो.", 0.9, 0))   # your turn
    return segs


def llm_segments(lesson: Lesson, timeout: float = 25.0) -> list[Segment] | None:
    """Ask Sarvam's LLM for the teacher's script. Returns None on any failure so the template is used."""
    if not sarvam.available():
        return None
    lang = "Marathi" if lesson.language == "mr" else "Hindi"
    system = f"You are a {lang} primary-school reading teacher. Reply only with JSON, no markdown, no reasoning."
    user = (f"Word: {lesson.word} ({lang}). Reviewed chunks: {', '.join(lesson.teaching_chunks)}. "
            "Write the exact sentences a teacher says aloud to teach a 6 year old to blend this word. "
            'Return JSON {"segments":[{"text":"..."}]} with 6 to 8 short Devanagari segments: the whole word; '
            "each chunk sound spoken alone as it sounds inside the word; the blend; the whole word again; "
            "a one-line prompt asking the child to say it. No Latin letters, no explanations.")
    try:
        raw = sarvam.chat(system, user, timeout=timeout, max_tokens=600)
        raw = raw.strip().strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        texts = [s["text"].strip() for s in data["segments"] if s.get("text", "").strip()]
        if not (4 <= len(texts) <= 10) or any(any("a" <= ch.lower() <= "z" for ch in t) for t in texts):
            return None
        return [Segment(t, 0.7 if len(t) <= 4 else 0.85, 500) for t in texts]
    except Exception as e:  # noqa: BLE001
        print(f"[lesson_audio] llm script failed: {e}")
        return None


def script_text(segs: list[Segment]) -> str:
    return " ".join(s.text for s in segs)


def _silence(ms: int, rate: int, width: int, channels: int) -> bytes:
    return b"\x00" * int(rate * ms / 1000) * width * channels


def stitch(wavs: list[bytes], gaps_ms: list[int]) -> bytes:
    rate = width = channels = None
    frames = []
    for i, w in enumerate(wavs):
        with wave.open(io.BytesIO(w), "rb") as f:
            p = f.getparams()
            if rate is None:
                rate, width, channels = p.framerate, p.sampwidth, p.nchannels
            elif (p.framerate, p.sampwidth, p.nchannels) != (rate, width, channels):
                raise ValueError("segment format mismatch")
            frames.append(f.readframes(p.nframes))
        frames.append(_silence(gaps_ms[i], rate, width, channels))
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(channels); f.setsampwidth(width); f.setframerate(rate)
        f.writeframes(b"".join(frames))
    return out.getvalue()


def cache_path(lesson: Lesson, segs: list[Segment], cache_dir: Path) -> Path:
    key = hashlib.sha256(f"sarvam|{sarvam.TTS_MODEL}|{sarvam.TTS_VOICE}|{lesson.language}|{SCRIPT_VERSION}|"
                         f"{json.dumps([(s.text, s.pace, s.gap_ms) for s in segs], ensure_ascii=False)}".encode()).hexdigest()[:16]
    return cache_dir / f"lesson_{lesson.language}_{key}.wav"


def build(lesson: Lesson, cache_dir: Path, use_llm: bool = False) -> tuple[Path, list[Segment]]:
    """Synthesize (or fetch from cache) the stitched lesson audio. Raises on provider failure."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    segs = (llm_segments(lesson) if use_llm else None) or template_segments(lesson)
    out = cache_path(lesson, segs, cache_dir)
    if out.exists():
        return out, segs
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as ex:
        wavs = list(ex.map(lambda s: sarvam.tts(s.text, lesson.language, pace=s.pace), segs))
    out.write_bytes(stitch(wavs, [s.gap_ms for s in segs]))
    return out, segs


FEEDBACK = {
    ("mr", "correct"): "छान! बरोबर.", ("mr", "close"): "छान! जवळपास बरोबर.", ("mr", "try_again"): "पुन्हा प्रयत्न कर.",
    ("hi", "correct"): "शाबाश! बिलकुल सही.", ("hi", "close"): "शाबाश! लगभग सही.", ("hi", "try_again"): "फिर से बोलो.",
}


def feedback_path(language: str, verdict: str, cache_dir: Path) -> Path | None:
    """Cached short praise / retry clip; synthesized on first use."""
    text = FEEDBACK.get((language, verdict))
    if not text:
        return None
    out = cache_dir / f"fb_{language}_{verdict}.wav"
    if not out.exists():
        if not sarvam.available():
            return None
        out.write_bytes(sarvam.tts(text, language, pace=1.0))
    return out
