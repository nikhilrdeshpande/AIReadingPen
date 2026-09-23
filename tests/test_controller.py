"""Controller tests with a fake camera and fake engine: no hardware, no model download."""
import time

import cv2
import numpy as np
import pytest

from app import capture as capmod
from app.audio import AudioPlayer
from app.config import settings
from app.manifest import Manifest


class FakeCamera:
    def __init__(self):
        self.jpg = None
        self.seq = 0
        self.health = type("H", (), {"to_dict": lambda self: {"connected": True, "fps": 5, "frames": 1, "error": "", "reconnects": 0}})()
    def set(self, img):
        ok, buf = cv2.imencode(".jpg", img)
        self.jpg, self.seq = buf.tobytes(), self.seq + 1
    def latest(self):
        return self.jpg, self.seq
    def wait_next(self, last_seq, timeout=1.0):
        t = time.time() + timeout
        while time.time() < t:
            if self.seq != last_seq and self.jpg is not None:
                return self.jpg, self.seq
            time.sleep(0.005)
        return None, last_seq
    def snapshot(self):
        return self.jpg


class FakeEngine:
    name = "fake"
    def __init__(self, text, conf=0.99):
        self.text, self.conf, self.calls = text, conf, 0
    def warmup(self): pass
    def recognize(self, image, language):
        self.calls += 1
        return {"engine": self.name, "raw_text": self.text, "candidates": [{"text": self.text, "confidence": self.conf}], "latency_ms": 1}


class FakeAudio(AudioPlayer):
    def speak_lesson(self, lesson, on_done=None):
        self.last_source = "cache"
        if on_done:
            on_done()
        return {"source": "cache", "asset": "fake.wav"}


def frame_with_word(word: str, shift=0, blank=False):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 480), "white")
    if not blank:
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/DevanagariMT.ttc", 90)
        d.text((200 + shift, 190), word, font=f, fill="black")
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


@pytest.fixture
def ctl(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "preview_sample_ms", 40)
    monkeypatch.setattr(settings, "stable_dwell_ms", 200)
    monkeypatch.setattr(settings, "min_capture_cooldown_ms", 300)
    monkeypatch.setattr(settings, "rearm_change_ms", 100)
    monkeypatch.setattr(settings, "debug_dir", str(tmp_path / "dbg"))
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path / "tr"))
    cam = FakeCamera()
    c = capmod.CaptureController(cam, Manifest(settings.lesson_manifest), FakeAudio())
    c.engine, c.engine_ready = FakeEngine("घर"), True
    c.start()
    yield c, cam
    c._stop.set()


def feed(cam, img, seconds, period=0.04):
    t = time.time() + seconds
    while time.time() < t:
        cam.set(img)
        time.sleep(period)


def wait_state(c, states, timeout=3.0):
    t = time.time() + timeout
    while time.time() < t:
        if c.state in states:
            return True
        time.sleep(0.01)
    return False


def test_auto_capture_fires_once_and_rearms_on_change(ctl):
    c, cam = ctl
    img = frame_with_word("घर")
    feed(cam, img, 0.8)                       # held still: should fire exactly once
    assert wait_state(c, {"locked"}, 2.0), c.state
    assert c.engine.calls == 1
    feed(cam, img, 1.5)                       # still held: no second capture
    assert c.engine.calls == 1 and c.state == "locked"
    feed(cam, frame_with_word("", blank=True), 0.5)   # card removed: re-arm
    assert wait_state(c, {"searching", "stabilizing"}, 1.0), c.state
    assert c.last_result["lesson"]["id"] == "mr_ghar_v1"
    assert c.last_trace["gate"]["override"] is False


def test_empty_band_never_captures(ctl):
    c, cam = ctl
    feed(cam, frame_with_word("", blank=True), 1.2)
    assert c.engine.calls == 0 and c.state in ("searching",)


def test_manual_capture_bypasses_dwell_and_blocks_double_fire(ctl):
    c, cam = ctl
    c.auto_enabled = False
    feed(cam, frame_with_word("घर"), 0.3)
    assert c.state == "searching" and c.engine.calls == 0
    r1 = c.capture(source="space")
    r2 = c.capture(source="button")
    assert r1["ok"] and not r2["ok"]
    assert wait_state(c, {"locked"}, 2.0)
    assert c.engine.calls == 1
    assert c.last_trace["trigger"]["source"] == "space"


def test_low_confidence_needs_recapture_and_no_lesson(ctl):
    c, cam = ctl
    c.engine = FakeEngine("घर", conf=0.3)
    feed(cam, frame_with_word("घर"), 0.8)
    assert wait_state(c, {"needs_recapture"}, 2.0), c.state
    assert c.last_result["lesson"] is None and c.hint


def test_language_switch_blocked_during_lesson_and_override_marked(ctl):
    c, cam = ctl
    feed(cam, frame_with_word("घर"), 0.4)
    r = c.override("mr_jhaad_v1")
    assert r["ok"]
    assert wait_state(c, {"locked", "operator_recovery"}, 2.0)
    assert c.last_trace["gate"]["override"] is True
    assert c.override("hi_kitaab_v1")["ok"] is False   # language mismatch
    assert c.set_language("hi")["ok"]                   # allowed when not speaking


def test_saved_image_replay_without_camera(ctl):
    c, cam = ctl
    c.engine = FakeEngine("किताब")
    c.set_language("hi")
    data = open("fixtures/hi_kitaab.jpg", "rb").read()
    assert c.capture(source="saved_image", image_bytes=data, image_source="fixture")["ok"]
    assert wait_state(c, {"locked"}, 2.0)
    assert c.last_result["lesson"]["id"] == "hi_kitaab_v1"
    assert c.last_trace["image_source"] == "fixture"
