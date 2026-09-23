import unicodedata

import pytest

from app.config import settings
from app.gate import evaluate
from app.manifest import Manifest
from app.textnorm import normalize

WORDS = ["घर", "झाड", "फुलपाखरू", "किताब", "माझं", "मित्र", "क्षमा", "ज्ञान", "स्वतंत्र", "श्रद्धा"]


@pytest.fixture(scope="module")
def manifest():
    return Manifest(settings.lesson_manifest)


def _res(text, conf=0.95, engine="test"):
    return {"engine": engine, "raw_text": text, "candidates": [{"text": text, "confidence": conf}], "latency_ms": 1}


@pytest.mark.parametrize("w", WORDS)
def test_nfc_preserves_every_word(w):
    assert normalize(w) == unicodedata.normalize("NFC", w)
    # NFD input comes back identical to NFC
    assert normalize(unicodedata.normalize("NFD", w)) == unicodedata.normalize("NFC", w)


def test_combining_marks_never_detach():
    w = "फुलपाखरू"
    n = normalize(w)
    assert n == w
    # every combining mark still follows a consonant
    for i, ch in enumerate(n):
        if unicodedata.combining(ch):
            assert i > 0 and not unicodedata.combining(n[i - 1])


def test_trim_and_punctuation():
    assert normalize("  घर। ") == "घर"
    assert normalize("\"झाड\"") == "झाड"
    assert normalize("फुल  पाखरू") == "फुल पाखरू"   # internal whitespace collapsed, not removed


def test_same_word_in_both_language_namespaces(manifest):
    assert manifest.lookup("mr", "घर").id == "mr_ghar_v1"
    assert manifest.lookup("hi", "घर").id == "hi_ghar_v1"
    assert manifest.lookup("mr", "ज्ञान").id != manifest.lookup("hi", "ज्ञान").id


def test_manifest_all_24_present_and_nfc(manifest):
    assert len(manifest.lessons) == 24
    for l in manifest.lessons:
        assert l.word == unicodedata.normalize("NFC", l.word)
        assert l.teaching_chunks, l.id
        assert "".join(l.teaching_chunks) == l.word, f"{l.id}: chunks must concatenate to the word"


def test_gate_accepts_exact_match(manifest):
    d = evaluate(_res("घर"), "mr", manifest, 0.8)
    assert d.accepted and d.lesson.id == "mr_ghar_v1" and d.override is False


def test_gate_rejects_punctuation_extra_words_non_manifest(manifest):
    assert evaluate(_res("घर।"), "mr", manifest, 0.8).accepted           # outer punctuation is allowed normalization
    assert not evaluate(_res("घर झाड"), "mr", manifest, 0.8).accepted    # extra words
    assert not evaluate(_res("घरा"), "mr", manifest, 0.8).accepted       # near miss must NOT autocorrect
    assert evaluate(_res("घरा"), "mr", manifest, 0.8).reason == "no_match"
    assert not evaluate(_res("किताब"), "mr", manifest, 0.8).accepted     # Hindi-only word under Marathi


def test_gate_low_confidence(manifest):
    d = evaluate(_res("घर", conf=0.5), "mr", manifest, 0.8)
    assert not d.accepted and d.reason == "low_confidence"


def test_gate_requires_language(manifest):
    assert evaluate(_res("घर"), "", manifest, 0.8).reason == "no_language"


def test_gate_no_text(manifest):
    r = {"engine": "t", "raw_text": "", "candidates": [], "latency_ms": 1}
    assert evaluate(r, "mr", manifest, 0.8).reason == "no_text"
