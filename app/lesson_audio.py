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

SCRIPT_VERSION = "6"   # danda (।) between sounds: a full stop after a lone syllable is read aloud as "dot"   # bump when the template changes so cached audio is rebuilt


@dataclass
class Segment:
    text: str
    pace: float = 0.85
    gap_ms: int = 450     # silence after this segment


PACE = 1.0          # the model's natural pace; slowing it makes the voice sound synthetic
GAP = 500

# Vowel-sign (matra) names as teachers say them. Marathi barakhadi names; Hindi "की मात्रा" names.
MATRA_NAMES = {
    "mr": {"\u093e": "काना", "\u093f": "पहिली वेलांटी", "\u0940": "दुसरी वेलांटी", "\u0941": "पहिला उकार",
           "\u0942": "दुसरा उकार", "\u0943": "ऋकार", "\u0947": "एक मात्रा", "\u0948": "दोन मात्रा",
           "\u094b": "काना एक मात्रा", "\u094c": "काना दोन मात्रा", "\u0902": "अनुस्वार", "\u0901": "चंद्रबिंदू", "\u0903": "विसर्ग"},
    "hi": {"\u093e": "आ की मात्रा", "\u093f": "छोटी इ की मात्रा", "\u0940": "बड़ी ई की मात्रा", "\u0941": "छोटी उ की मात्रा",
           "\u0942": "बड़ी ऊ की मात्रा", "\u0943": "ऋ की मात्रा", "\u0947": "ए की मात्रा", "\u0948": "ऐ की मात्रा",
           "\u094b": "ओ की मात्रा", "\u094c": "औ की मात्रा", "\u0902": "अनुस्वार", "\u0901": "चंद्रबिंदु", "\u0903": "विसर्ग"},
}


def chunk_teaching_phrase(chunk: str, language: str) -> str | None:
    """'झा' -> 'झ ला काना, झा' (mr) / 'झ में आ की मात्रा, झा' (hi). None when the chunk has no vowel sign
    (bare consonant, conjunct without matra) so it is just spoken as itself."""
    names = MATRA_NAMES[language]
    marks = [ch for ch in chunk if ch in names]
    if not marks:
        return None
    base = "".join(ch for ch in chunk if ch not in names)
    if base.endswith("\u094d"):          # conjunct fragments: keep as unit
        return None
    joiner = " ला " if language == "mr" else " में "
    return f"{base}{joiner}{' आणि '.join(names[m] for m in marks) if language == 'mr' else ' और '.join(names[m] for m in marks)}, {chunk}"


def _prompt(language: str) -> str:
    return "आता तू म्हण." if language == "mr" else "अब तुम बोलो."


def template_segments(lesson: Lesson) -> list[Segment]:
    """Plain drill in four utterances: word | each sound, then the blend | word | your turn.
    The drill is one utterance with sentence breaks so the model keeps its natural prosody;
    single-syllable utterances sound synthetic."""
    word, chunks = lesson.word, lesson.teaching_chunks
    drill = " ".join(f"{c}।" for c in chunks) + (f" {', '.join(chunks)}।" if len(chunks) > 1 else "")
    return [Segment(f"{word}.", PACE, GAP), Segment(drill, PACE, GAP), Segment(f"{word}.", PACE, GAP),
            Segment(_prompt(lesson.language), PACE, 0)]


def barakhadi_segments(lesson: Lesson) -> list[Segment]:
    """Traditional drill: word | 'झ ला काना, झा. ड. झा, ड.' | word | your turn."""
    word, chunks = lesson.word, lesson.teaching_chunks
    lines = []
    for c in chunks:
        phrase = chunk_teaching_phrase(c, lesson.language)
        lines.append(f"{phrase}।" if phrase else f"{c}।")
    if len(chunks) > 1:
        lines.append(f"{', '.join(chunks)}।")
    return [Segment(f"{word}.", PACE, GAP), Segment(" ".join(lines), PACE, GAP), Segment(f"{word}.", PACE, GAP),
            Segment(_prompt(lesson.language), PACE, 0)]


def _normalize_level(pcm: bytes, width: int, target_rms: float = 2500.0) -> bytes:
    """Match loudness across segments so the run sounds like one continuous speaker."""
    import numpy as np
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    if rms < 50:
        return pcm
    g = min(max(target_rms / rms, 0.5), 2.5)
    return np.clip(a * g, -32768, 32767).astype(np.int16).tobytes()


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
            frames.append(_normalize_level(f.readframes(p.nframes), width))
        frames.append(_silence(gaps_ms[i], rate, width, channels))
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(channels); f.setsampwidth(width); f.setframerate(rate)
        f.writeframes(b"".join(frames))
    return out.getvalue()


def cache_path(lesson: Lesson, segs: list[Segment], cache_dir: Path) -> Path:
    key = hashlib.sha256(f"sarvam|{sarvam.TTS_MODEL}|{settings.sarvam_voice}|{settings.sarvam_temperature}|{lesson.language}|{SCRIPT_VERSION}|"
                         f"{json.dumps([(s.text, s.pace, s.gap_ms) for s in segs], ensure_ascii=False)}".encode()).hexdigest()[:16]
    return cache_dir / f"lesson_{lesson.language}_{key}.wav"


def build(lesson: Lesson, cache_dir: Path, use_llm: bool = False) -> tuple[Path, list[Segment]]:
    """Synthesize (or fetch from cache) the stitched lesson audio. Raises on provider failure."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    segs = (llm_segments(lesson) if use_llm else None) or (
        barakhadi_segments(lesson) if settings.lesson_style == "barakhadi" else template_segments(lesson))
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
        out.write_bytes(sarvam.tts(text, language))
    return out
