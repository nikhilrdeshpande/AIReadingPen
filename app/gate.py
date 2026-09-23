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
    if lesson is None and " " in norm:
        words = norm.split()
        if not settings.multi_word or len(words) > settings.multi_word_max:
            return GateDecision(False, "no_match", norm, conf, None, hint="Show one word inside the band")
        parts = []
        for w in words:
            d = evaluate({"engine": result["engine"], "raw_text": w, "latency_ms": 0,
                          "candidates": [{"text": w, "confidence": conf}]}, language, manifest, threshold)
            if not d.accepted or d.lesson is None:
                d.normalized = norm
                d.hint = f"Could not read '{w}'. " + (d.hint or "")
                return d
            parts.append(d.lesson)
        return GateDecision(True, "ok_phrase", norm, conf, phrase_lesson(language, parts))
    if lesson is None:
        near = nearest_manifest_word(norm, manifest, language)
        if near is not None:
            # A fragment or one-mark miss of a pack word (झड / आड for झाड). Never autocorrect, never invent: ask again.
            return GateDecision(False, "near_miss", norm, conf, None,
                                hint=f"Looks like part of {near}. Hold the card flat and still, then try again")
        if settings.open_vocabulary and akshara.is_devanagari_word(norm) and conf is not None \
                and conf >= settings.open_vocab_confidence_threshold and " " not in norm and len(norm) >= 2:
            if settings.open_vocab_validate and not is_real_word(language, norm):
                return GateDecision(False, "not_a_word", norm, conf, None, hint="Could not read a real word. Hold flat and try again")
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


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def nearest_manifest_word(norm: str, manifest: Manifest, language: str) -> str | None:
    """Manifest word (any language, same script) that this result is a near miss of:
    one code point away, one akshara away, or a fragment (substring / subsequence with <= 2 code points dropped).
    A rejected near miss costs one retry; an accepted one teaches a wrong word, so this errs on rejecting."""
    a_norm = akshara.split(norm)
    for l in manifest.lessons:
        w = l.normalized
        if w == norm:
            continue
        if _edit_distance(norm, w) <= 1:
            return w
        a_w = akshara.split(w)
        if abs(len(a_w) - len(a_norm)) <= 1 and _edit_distance_seq(a_norm, a_w) <= 1:
            return w
        if len(norm) >= 2 and len(w) - len(norm) <= 2 and (norm in w or _is_subsequence(norm, w)):
            return w
    return None


def _edit_distance_seq(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_subsequence(a: str, b: str) -> bool:
    it = iter(b)
    return all(ch in it for ch in a)


_word_cache: dict[tuple[str, str], bool] = {}


def is_real_word(language: str, word: str) -> bool:
    """Ask Sarvam's LLM whether this is a real dictionary word; cached. Fails open (True) when the LLM is unavailable,
    so the gate stays usable offline, and fails closed on an explicit 'no'."""
    key = (language, word)
    if key in _word_cache:
        return _word_cache[key]
    try:
        from . import sarvam
        if not sarvam.available():
            return True
        lang = "Marathi" if language == "mr" else "Hindi"
        ans = sarvam.chat(f"You are a {lang} dictionary. Answer with exactly one word: YES or NO.",
                          f"Is \"{word}\" a real, correctly spelled {lang} word (common noun, verb, adjective or name)? "
                          "Answer NO for fragments, misspellings, or words missing a vowel sign.", timeout=6.0, max_tokens=5)
        ok = ans.strip().upper().startswith("YES")
    except Exception as e:  # noqa: BLE001
        print(f"[gate] word validation unavailable: {e}")
        ok = True
    _word_cache[key] = ok
    return ok


def phrase_lesson(language: str, parts: list[Lesson]) -> Lesson:
    """Two or three words on one line: taught word by word, then the phrase. Never presented as reviewed."""
    phrase = " ".join(p.word for p in parts)
    chunks = [c for p in parts for c in p.teaching_chunks]
    return Lesson(id=f"phrase_{language}_{hashlib.sha1(phrase.encode()).hexdigest()[:8]}", language=language,
                  word=phrase, normalized=phrase, teaching_chunks=chunks, lesson_class="phrase",
                  display_prompt="   ·   ".join(p.display_prompt for p in parts), speech_script=phrase,
                  audio_asset="", review_status="generated", parts=parts)
