"""Confidence gate: exact manifest match + calibrated confidence. Never autocorrects."""
from __future__ import annotations

from dataclasses import dataclass

import hashlib

from . import akshara
from .config import settings
from .manifest import Lesson, Manifest
from .ocr import OCRResult
from .textnorm import normalize


@dataclass
class GateDecision:
    accepted: bool
    reason: str               # "ok" | "ok_generated" | "no_language" | "no_text" | "no_match" | "low_confidence"
    normalized: str
    confidence: float | None
    lesson: Lesson | None
    override: bool = False
    hint: str = ""            # corrective instruction for the user

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "reason": self.reason, "normalized": self.normalized,
                "confidence": self.confidence, "override": self.override, "hint": self.hint,
                "lesson_id": self.lesson.id if self.lesson else None}


def evaluate(result: OCRResult, language: str, manifest: Manifest, threshold: float) -> GateDecision:
    if language not in ("mr", "hi"):
        return GateDecision(False, "no_language", "", None, None, hint="Select Marathi or Hindi")
    if not result["candidates"]:
        return GateDecision(False, "no_text", "", None, None, hint="Show one word inside the band")
    # Best candidate by confidence; equal confidence keeps engine order.
    best = max(result["candidates"], key=lambda c: (c["confidence"] if c["confidence"] is not None else 0.0))
    norm = normalize(best["text"])
    conf = best["confidence"]
    if not norm:
        return GateDecision(False, "no_text", norm, conf, None, hint="Show one word inside the band")
    lesson = manifest.lookup(language, norm)
    if lesson is None:
        if settings.open_vocabulary and akshara.is_devanagari_word(norm) and conf is not None \
                and conf >= settings.open_vocab_confidence_threshold and " " not in norm:
            return GateDecision(True, "ok_generated", norm, conf, generated_lesson(language, norm))
        return GateDecision(False, "no_match", norm, conf, None, hint="Hold steady and move closer, then try again")
    if conf is not None and conf < threshold:
        return GateDecision(False, "low_confidence", norm, conf, lesson, hint="Improve the light and hold steady")
    return GateDecision(True, "ok", norm, conf, lesson)


def generated_lesson(language: str, word: str) -> Lesson:
    """Unreviewed lesson for a word outside the manifest. Chunks come from the deterministic akshara analyzer;
    the speech script follows the reviewed template. Labelled 'generated' so the UI and trace never present it as reviewed."""
    chunks = akshara.split(word)
    prompt = "आता तू वाच." if language == "mr" else "अब तुम पढ़ो."
    joiner = " आणि " if language == "mr" else " और "
    body = joiner.join(chunks) if len(chunks) == 2 else ", ".join(chunks)
    script = f"{word}. {body}. {word}. {prompt}" if len(chunks) > 1 else f"{word}. {word}. {prompt}"
    lid = f"gen_{language}_{hashlib.sha1(word.encode()).hexdigest()[:8]}"
    return Lesson(id=lid, language=language, word=word, normalized=word, teaching_chunks=chunks,
                  lesson_class="generated", display_prompt=" + ".join(chunks), speech_script=script,
                  audio_asset="", review_status="generated")
