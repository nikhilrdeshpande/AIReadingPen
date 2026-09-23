"""Unicode normalization for OCR output. NFC + trim + collapse whitespace + strip outer punctuation.
Never touches combining marks inside the word."""
from __future__ import annotations

import re
import unicodedata

# Devanagari danda/double danda, ASCII and common punctuation, quotes, brackets
_OUTER_PUNCT = "।॥.,;:!?\"'`´‘’“”()[]{}<>«»-–—_/\\|*#@~^"
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = t.replace("​", "").replace("‌", "").replace("‍", "")  # ZW space, ZWNJ, ZWJ
    t = _WS.sub(" ", t).strip()
    t = t.strip(_OUTER_PUNCT + " ")
    return t


def has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)
