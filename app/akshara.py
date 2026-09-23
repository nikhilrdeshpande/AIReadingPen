"""Deterministic Devanagari orthographic-syllable (akshara) splitter.

Text-safety infrastructure: it never splits a combining mark from its base and keeps conjuncts (consonant +
virama + consonant) together. Used for open-vocabulary words that are NOT in the reviewed manifest; the UI
labels such chunks as auto-generated. Reviewed manifest chunks always take precedence."""
from __future__ import annotations

import unicodedata

VIRAMA = "्"
NUKTA = "़"
_INDEP_VOWELS = set("ऄअआइईउऊऋऌऍऎएऐऑऒओऔॠॡॲ")
_CONSONANTS = {chr(c) for c in range(0x0915, 0x093A)} | {chr(c) for c in range(0x0958, 0x0960)} | {"ॹ", "ॺ", "ॻ", "ॼ", "ॽ", "ॾ", "ॿ"}
_VOWEL_SIGNS = {chr(c) for c in range(0x093E, 0x094D)} | {chr(c) for c in range(0x0955, 0x0958)} | {"ऺ", "ऻ", "ॢ", "ॣ"}
_MODIFIERS = {"ऀ", "ँ", "ं", "ः", "ऽ"}  # chandrabindu, anusvara, visarga, avagraha
_ZW = {"‌", "‍"}


def split(word: str) -> list[str]:
    """Split a single NFC Devanagari word into aksharas. Non-Devanagari characters form their own chunk."""
    word = unicodedata.normalize("NFC", word)
    chunks: list[str] = []
    cur = ""
    pending_virama = False
    for ch in word:
        if ch in _ZW:
            cur += ch
            continue
        if ch in _CONSONANTS or ch in _INDEP_VOWELS:
            if cur and not pending_virama:
                chunks.append(cur)
                cur = ""
            cur += ch
            pending_virama = False
        elif ch == VIRAMA:
            cur += ch
            pending_virama = True
        elif ch in _VOWEL_SIGNS or ch in _MODIFIERS or ch == NUKTA or unicodedata.combining(ch):
            cur += ch
            pending_virama = False
        else:
            if cur:
                chunks.append(cur)
            chunks.append(ch)
            cur = ""
            pending_virama = False
    if cur:
        chunks.append(cur)
    return chunks


def is_devanagari_word(word: str) -> bool:
    return bool(word) and all(("ऀ" <= c <= "ॿ") or c in _ZW for c in word)
