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


def test_gate_rejects_punctuation_extra_words_non_manifest(manifest, monkeypatch):
    monkeypatch.setattr(settings, "open_vocabulary", False)
    monkeypatch.setattr(settings, "multi_word", False)
    assert evaluate(_res("घर।"), "mr", manifest, 0.8).accepted           # outer punctuation is allowed normalization
    assert not evaluate(_res("घर झाड"), "mr", manifest, 0.8).accepted    # extra words (phrases off)
    assert not evaluate(_res("घरा"), "mr", manifest, 0.8).accepted       # near miss must NOT autocorrect
    assert evaluate(_res("घरा"), "mr", manifest, 0.8).reason == "near_miss"
    assert not evaluate(_res("किताब"), "mr", manifest, 0.8).accepted     # Hindi-only word under Marathi
    # with open vocabulary on, a near miss of a pack word is never mapped to घर and never taught as its own word
    monkeypatch.setattr(settings, "open_vocabulary", True)
    d = evaluate(_res("घरा", conf=0.99), "mr", manifest, 0.8)
    assert d.reason == "near_miss" and d.lesson is None


def test_gate_low_confidence(manifest):
    d = evaluate(_res("घर", conf=0.5), "mr", manifest, 0.8)
    assert not d.accepted and d.reason == "low_confidence"


def test_gate_requires_language(manifest):
    assert evaluate(_res("घर"), "", manifest, 0.8).reason == "no_language"


def test_gate_no_text(manifest):
    r = {"engine": "t", "raw_text": "", "candidates": [], "latency_ms": 1}
    assert evaluate(r, "mr", manifest, 0.8).reason == "no_text"


def test_akshara_analyzer_matches_answer_key(manifest):
    from app.akshara import split
    for l in manifest.lessons:
        assert split(l.word) == l.teaching_chunks, l.id


def test_open_vocabulary_generated_lesson(manifest, monkeypatch):
    monkeypatch.setattr(settings, "open_vocabulary", True)
    monkeypatch.setattr(settings, "open_vocab_confidence_threshold", 0.9)
    d = evaluate(_res("पुस्तक", conf=0.95), "mr", manifest, 0.8)
    assert d.accepted and d.reason == "ok_generated"
    assert d.lesson.review_status == "generated" and d.lesson.teaching_chunks == ["पु", "स्त", "क"]
    assert d.lesson.audio_asset == ""
    # stricter threshold than manifest words; non-Devanagari and multi-word never generate
    assert evaluate(_res("पुस्तक", conf=0.85), "mr", manifest, 0.8).reason == "no_match"
    assert evaluate(_res("hello", conf=0.99), "mr", manifest, 0.8).reason == "no_match"
    assert evaluate(_res("घर झाड", conf=0.99), "mr", manifest, 0.8).reason == "ok_phrase"
    monkeypatch.setattr(settings, "open_vocabulary", False)
    assert evaluate(_res("पुस्तक", conf=0.99), "mr", manifest, 0.8).reason == "no_match"


def test_near_miss_of_pack_word_is_rejected_not_generated(manifest, monkeypatch):
    monkeypatch.setattr(settings, "open_vocabulary", True)
    monkeypatch.setattr(settings, "open_vocab_validate", False)
    for frag in ("झड", "आड", "झा", "फुलपाखर", "कताब", "झाढ"):
        d = evaluate(_res(frag, conf=0.99), "mr", manifest, 0.8)
        assert d.reason == "near_miss" and d.lesson is None, frag
    # a genuinely different word still generates
    assert evaluate(_res("पुस्तक", conf=0.99), "mr", manifest, 0.8).reason == "ok_generated"


def test_open_vocab_llm_validation_gate(manifest, monkeypatch):
    import app.gate as gate
    monkeypatch.setattr(settings, "open_vocabulary", True)
    monkeypatch.setattr(settings, "open_vocab_validate", True)
    monkeypatch.setattr(gate, "is_real_word", lambda lang, w: w != "घलठ")
    assert evaluate(_res("घलठ", conf=0.99), "mr", manifest, 0.8).reason == "not_a_word"
    assert evaluate(_res("पुस्तक", conf=0.99), "mr", manifest, 0.8).reason == "ok_generated"


def test_phrase_of_pack_words(manifest, monkeypatch):
    monkeypatch.setattr(settings, "multi_word", True)
    monkeypatch.setattr(settings, "open_vocab_validate", False)
    d = evaluate(_res("घर झाड", conf=0.95), "mr", manifest, 0.8)
    assert d.accepted and d.reason == "ok_phrase" and [p.id for p in d.lesson.parts] == ["mr_ghar_v1", "mr_jhaad_v1"]
    assert d.lesson.teaching_chunks == ["घ", "र", "झा", "ड"]
    # one bad word fails the whole phrase, with the word named
    d2 = evaluate(_res("घर झड", conf=0.95), "mr", manifest, 0.8)
    assert not d2.accepted and "झड" in d2.hint
    from app.lesson_audio import barakhadi_segments
    assert [s.text for s in barakhadi_segments(d.lesson)][0] == "घर झाड."
