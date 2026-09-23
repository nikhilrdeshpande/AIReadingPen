"""Confidence gate: exact manifest match + calibrated confidence. Never autocorrects."""
from __future__ import annotations

from dataclasses import dataclass

from .manifest import Lesson, Manifest
from .ocr import OCRResult
from .textnorm import normalize


@dataclass
class GateDecision:
    accepted: bool
    reason: str               # "ok" | "no_language" | "no_text" | "no_match" | "low_confidence"
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
        return GateDecision(False, "no_match", norm, conf, None, hint="Hold steady and move closer, then try again")
    if conf is not None and conf < threshold:
        return GateDecision(False, "low_confidence", norm, conf, lesson, hint="Improve the light and hold steady")
    return GateDecision(True, "ok", norm, conf, lesson)
