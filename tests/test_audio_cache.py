from app.audio import cache_key
import app.audio as audio_mod


def test_cache_key_changes_with_script_and_dictionary(monkeypatch):
    a = cache_key("openai", "m", "v", "mr", "झाड. झा आणि ड.")
    b = cache_key("openai", "m", "v", "mr", "झाड. झा, ड.")
    assert a != b
    monkeypatch.setattr(audio_mod, "PRON_DICT_VERSION", "2")
    c = cache_key("openai", "m", "v", "mr", "झाड. झा आणि ड.")
    assert c != a


def test_stitch_and_template_segments():
    import io, wave
    from app.lesson_audio import stitch, template_segments, script_text
    from app.manifest import Manifest
    from app.config import settings
    def tone(ms):
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b"\x01\x00" * int(22050 * ms / 1000))
        return b.getvalue()
    out = stitch([tone(100), tone(100)], [400, 0])
    with wave.open(io.BytesIO(out), "rb") as w:
        assert abs(w.getnframes() / 22050 - 0.6) < 0.01
    m = Manifest(settings.lesson_manifest)
    segs = template_segments(m.by_id("mr_jhaad_v1"))
    assert [s.text for s in segs] == ["झाड.", "झा.", "ड.", "झा, ड.", "झाड.", "आता तू म्हण."]
    assert "झा" in script_text(segs)


def test_practice_compare():
    from app.practice import compare
    assert compare("घर", "घर.")["verdict"] == "correct"
    assert compare("झाड", "झाड़")["verdict"] == "close"
    assert compare("झाड", "जाड")["verdict"] == "try_again"
    assert compare("घर", "")["match"] is False
