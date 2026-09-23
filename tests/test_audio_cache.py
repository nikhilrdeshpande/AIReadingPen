from app.audio import cache_key
import app.audio as audio_mod


def test_cache_key_changes_with_script_and_dictionary(monkeypatch):
    a = cache_key("openai", "m", "v", "mr", "झाड. झा आणि ड.")
    b = cache_key("openai", "m", "v", "mr", "झाड. झा, ड.")
    assert a != b
    monkeypatch.setattr(audio_mod, "PRON_DICT_VERSION", "2")
    c = cache_key("openai", "m", "v", "mr", "झाड. झा आणि ड.")
    assert c != a
