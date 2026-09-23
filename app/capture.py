r"""Capture controller: the state machine that owns targeting, auto-capture, lock/re-arm, and the
single capture() primitive shared by auto, button, Space and saved-image replay.

booting -> searching -> stabilizing -> capturing -> processing -> speaking -> locked -> rearming -> searching
                                                   \-> needs_recapture -> rearming
                                                   \-> operator_recovery -> rearming
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import preprocess
from .audio import AudioPlayer
from .camera import CameraClient, decode_jpeg, orient
from .config import settings
from .gate import GateDecision, evaluate
from .manifest import Lesson, Manifest
from .ocr import OCREngine, get_engine
from .trace import Trace, new_capture_id

USER_LABELS = {
    "booting": "Starting up", "searching": "Finding word", "stabilizing": "Hold steady",
    "capturing": "Captured", "processing": "Reading", "speaking": "Listen", "locked": "Move to next word",
    "rearming": "Move to next word", "needs_recapture": "Not confident, try again",
    "operator_recovery": "Operator recovery", "camera_lost": "Camera not connected",
}
BUSY = {"capturing", "processing", "speaking", "locked"}


class CaptureController:
    def __init__(self, camera: CameraClient, manifest: Manifest, audio: AudioPlayer):
        self.camera, self.manifest, self.audio = camera, manifest, audio
        self.language = settings.demo_language
        self.auto_enabled = settings.auto_capture_enabled
        self.engine_name = settings.ocr_engine
        self.engine: OCREngine | None = None
        self.engine_ready = False
        self.state = "booting"
        self.state_since = time.time()
        self.stable_ms = 0
        self.dwell_progress = 0.0
        self.eligible = False
        self.eligibility_reason = "starting"
        self.metrics: dict = {}
        self.last_trace: dict | None = None
        self.last_result: dict = {}
        self.history: list[dict] = []
        self.hint = ""
        self.error = ""
        self._prev_gray: np.ndarray | None = None
        self._locked_gray: np.ndarray | None = None
        self._changed_since: float | None = None
        self._empty_since: float | None = None
        self._last_capture_at = 0.0
        self._stable_since: float | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_band_jpg: bytes | None = None
        self.crop = settings.crop_box()

    # ---------------- lifecycle ----------------
    def load_engine(self, name: str | None = None) -> None:
        name = name or self.engine_name
        self.engine_ready = False
        t0 = time.time()
        eng = get_engine(name)
        eng.warmup()
        with self._lock:
            self.engine, self.engine_name, self.engine_ready = eng, name, True
        print(f"[ocr] {eng.name} ready in {time.time() - t0:.1f}s")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="capture-controller", daemon=True)
        self._thread.start()

    def _set_state(self, s: str) -> None:
        with self._lock:
            if s != self.state:
                self.state, self.state_since = s, time.time()

    # ---------------- preview loop ----------------
    def _loop(self) -> None:
        last_seq = 0
        period = settings.preview_sample_ms / 1000.0
        self._set_state("searching")
        while not self._stop.is_set():
            t_start = time.time()
            jpg, seq = self.camera.wait_next(last_seq, timeout=period * 2)
            if jpg is None:
                if self.state in ("searching", "stabilizing", "camera_lost"):
                    self._set_state("camera_lost")
                    self._stable_since = None
                time.sleep(period)
                continue
            last_seq = seq
            if self.state == "camera_lost":
                self._set_state("searching")
            img = decode_jpeg(jpg)
            if img is None:
                continue
            img = orient(img, settings.camera_hmirror, settings.camera_vflip)
            band = preprocess.band_crop(img, self.crop)
            gray = preprocess.analysis_gray(band)
            m = preprocess.metrics(gray, self._prev_gray)
            self._prev_gray = gray
            self.metrics = m.to_dict()
            self._step(gray, m)
            dt = time.time() - t_start
            if dt < period:
                time.sleep(period - dt)

    def _eligibility(self, m: preprocess.BandMetrics) -> tuple[bool, str]:
        if m.exposure < settings.exposure_min:
            return False, "too dark"
        if m.exposure > settings.exposure_max:
            return False, "too bright"
        if m.occupancy < settings.text_occupancy_min:
            return False, "no word in band"
        if m.occupancy > settings.text_occupancy_max:
            return False, "band covered"
        if m.sharpness < settings.sharpness_threshold:
            return False, "blurry"
        return True, "ok"

    def _step(self, gray: np.ndarray, m: preprocess.BandMetrics) -> None:
        elig, why = self._eligibility(m)
        self.eligible, self.eligibility_reason = elig, why
        now = time.time()
        st = self.state
        if st in ("searching", "stabilizing"):
            if not self.auto_enabled:
                self._set_state("searching")
                self._stable_since = None
                self.dwell_progress = 0.0
                return
            if elig and m.motion <= settings.motion_threshold:
                if self._stable_since is None:
                    self._stable_since = now
                self.stable_ms = int((now - self._stable_since) * 1000)
                self.dwell_progress = min(1.0, self.stable_ms / settings.stable_dwell_ms)
                self._set_state("stabilizing")
                cooldown_ok = (now - self._last_capture_at) * 1000 >= settings.min_capture_cooldown_ms
                if self.stable_ms >= settings.stable_dwell_ms and cooldown_ok:
                    self.capture(source="auto", stable_ms=self.stable_ms)
            else:
                self._stable_since = None
                self.stable_ms = 0
                self.dwell_progress = 0.0
                self._set_state("searching")
        elif st in ("locked", "needs_recapture", "operator_recovery"):
            # Re-arm when the band is empty or substantially different from the locked band for REARM_CHANGE_MS.
            changed = False
            if self._locked_gray is not None and self._locked_gray.shape == gray.shape:
                diff = float(np.abs(gray.astype(np.int16) - self._locked_gray.astype(np.int16)).mean())
                changed = diff >= settings.rearm_diff_threshold
            empty = m.occupancy < settings.text_occupancy_min
            if changed or empty:
                if self._changed_since is None:
                    self._changed_since = now
                if (now - self._changed_since) * 1000 >= settings.rearm_change_ms:
                    self._rearm()
            else:
                self._changed_since = None

    def _rearm(self) -> None:
        with self._lock:
            self._locked_gray = None
            self._changed_since = None
            self._stable_since = None
            self.stable_ms = 0
            self.dwell_progress = 0.0
            self._set_state("rearming")
        self._set_state("searching")

    # ---------------- the capture primitive ----------------
    def capture(self, source: str = "button", stable_ms: int | None = None, image_bytes: bytes | None = None,
                image_source: str | None = None) -> dict:
        """Shared by auto, button, Space and saved-image replay. Rejected while busy."""
        with self._lock:
            if self.state in BUSY:
                return {"ok": False, "error": f"busy: {self.state}"}
            if self.state in ("locked",):
                return {"ok": False, "error": "locked; move the card first or cancel"}
            self._set_state("capturing")
            self._last_capture_at = time.time()
            self._locked_gray = self._prev_gray.copy() if self._prev_gray is not None else None
        threading.Thread(target=self._process, args=(source, stable_ms, image_bytes, image_source),
                         daemon=True).start()
        return {"ok": True}

    def cancel(self) -> None:
        self.audio.stop()
        self._rearm()

    def _process(self, source: str, stable_ms: int | None, image_bytes: bytes | None, image_source: str | None) -> None:
        cid = new_capture_id()
        trace = Trace(cid, self.language, image_source or "xiao",
                      {"source": source, "stable_ms": stable_ms}, dict(self.crop))
        self.hint, self.error = "", ""
        try:
            # 1. image
            t0 = time.perf_counter()
            if image_bytes is None:
                try:
                    image_bytes = self.camera.snapshot()
                    trace["image_source"] = "xiao_snapshot"
                except Exception as e:  # noqa: BLE001
                    jpg, _ = self.camera.latest()
                    if jpg is None:
                        raise RuntimeError(f"no camera image: {e}") from e
                    image_bytes = jpg
                    trace["image_source"] = "xiao_preview_frame"
            trace["timing_ms"]["transfer"] = int((time.perf_counter() - t0) * 1000)
            img = decode_jpeg(image_bytes)
            if img is None:
                raise RuntimeError("could not decode image")
            full_crop = (image_source or "xiao").startswith("xiao")
            if full_crop:
                img = orient(img, settings.camera_hmirror, settings.camera_vflip)
            if settings.debug_save_frames:
                d = Path(settings.debug_dir); d.mkdir(parents=True, exist_ok=True)
                (d / f"{cid}_full.jpg").write_bytes(image_bytes)
            # 2. crop + preprocess (saved images are already crops if they are small)
            t0 = time.perf_counter()
            band = preprocess.band_crop(img, self.crop) if full_crop else img
            ocr_in = preprocess.prepare_for_ocr(band)
            trace["timing_ms"]["preprocess"] = int((time.perf_counter() - t0) * 1000)
            if settings.debug_save_frames and ocr_in is not None:
                cv2.imwrite(str(Path(settings.debug_dir) / f"{cid}_crop.jpg"), ocr_in)
            self._set_state("processing")
            # 3. OCR
            if ocr_in is None:
                result = {"engine": self.engine.name if self.engine else "none", "raw_text": "", "candidates": [], "latency_ms": 0}
            else:
                result = self.engine.recognize(ocr_in, self.language)  # type: ignore[union-attr]
            trace["timing_ms"]["ocr"] = result["latency_ms"]
            # 4. gate
            gate = evaluate(result, self.language, self.manifest, settings.ocr_confidence_threshold)
            trace["ocr"] = {"engine": result["engine"], "raw": result["raw_text"], "normalized": gate.normalized,
                            "confidence": gate.confidence, "candidates": result["candidates"]}
            trace["gate"] = gate.to_dict()
            self._finish(trace, gate)
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            trace["error"] = self.error
            trace.mark("failed")
            self._publish(trace, None)
            self._set_state("needs_recapture")
            self.hint = "Camera problem. Use Capture again or load a saved image."

    def _finish(self, trace: Trace, gate: GateDecision) -> None:
        if not gate.accepted or gate.lesson is None:
            self.hint = gate.hint
            trace.mark("decided")
            self._publish(trace, None)
            self._set_state("needs_recapture")
            return
        self._speak(trace, gate.lesson)

    def _speak(self, trace: Trace, lesson: Lesson) -> None:
        trace["lesson_id"] = lesson.id
        trace.mark("lesson")
        self._publish(trace, lesson)          # word + chunks visible before audio starts
        self._set_state("speaking")

        def done():
            trace.mark("audio_end")
            trace.save(settings.trace_dir)
            if self.camera.health.to_dict().get("connected"):
                self._set_state("locked")     # camera decides when to re-arm (card removed / changed)
            else:
                self._rearm()                 # no camera: saved-image replay path re-arms itself
        t0 = time.perf_counter()
        audio = self.audio.speak_lesson(lesson, on_done=done)
        trace["audio"] = audio
        trace["timing_ms"]["audio_start"] = int((time.perf_counter() - t0) * 1000)
        trace.mark("first_audio")
        if audio["source"] == "none":
            self.hint = "No audio available. Replay from cache or check provider."
        self._publish(trace, lesson)

    def _publish(self, trace: Trace, lesson: Lesson | None) -> None:
        trace.save(settings.trace_dir)
        with self._lock:
            self.last_trace = dict(trace)
            self.last_result = {"lesson": lesson.to_dict() if lesson else None,
                                "word": (trace.get("ocr") or {}).get("normalized", ""),
                                "confidence": (trace.get("ocr") or {}).get("confidence"),
                                "accepted": bool((trace.get("gate") or {}).get("accepted")),
                                "override": bool((trace.get("gate") or {}).get("override")),
                                "audio": trace.get("audio"), "capture_id": trace["capture_id"]}
            entry = {"capture_id": trace["capture_id"], "word": self.last_result["word"],
                     "accepted": self.last_result["accepted"], "timing_ms": trace["timing_ms"],
                     "source": trace["trigger"]["source"]}
            if self.history and self.history[0]["capture_id"] == entry["capture_id"]:
                self.history[0] = entry            # same capture published again (after audio start)
            else:
                self.history.insert(0, entry)
            del self.history[30:]

    # ---------------- operator actions ----------------
    def override(self, lesson_id: str) -> dict:
        lesson = self.manifest.by_id(lesson_id)
        if lesson is None:
            return {"ok": False, "error": "unknown lesson"}
        if lesson.language != self.language:
            return {"ok": False, "error": "lesson language differs from selected language"}
        with self._lock:
            if self.state in ("capturing", "processing", "speaking"):
                return {"ok": False, "error": f"busy: {self.state}"}
            self._set_state("operator_recovery")
            self._locked_gray = self._prev_gray.copy() if self._prev_gray is not None else None
            self._last_capture_at = time.time()
        trace = Trace(new_capture_id(), self.language, "operator", {"source": "override", "stable_ms": None}, dict(self.crop))
        trace["ocr"] = {"engine": "none", "raw": "", "normalized": lesson.normalized, "confidence": None}
        trace["gate"] = {"accepted": True, "override": True, "reason": "operator_override", "lesson_id": lesson.id}
        self.hint, self.error = "", ""
        threading.Thread(target=self._speak, args=(trace, lesson), daemon=True).start()
        return {"ok": True}

    def replay_audio(self) -> dict:
        lid = (self.last_trace or {}).get("lesson_id")
        lesson = self.manifest.by_id(lid) if lid else None
        if lesson is None and lid and lid.startswith("gen_") and self.last_result.get("lesson"):
            from .gate import generated_lesson
            lesson = generated_lesson(self.language, self.last_result["lesson"]["word"])
        if lesson is None:
            return {"ok": False, "error": "no lesson to replay"}
        self.audio.speak_lesson(lesson)
        return {"ok": True}

    def set_language(self, lang: str) -> dict:
        if lang not in ("mr", "hi"):
            return {"ok": False, "error": "language must be mr or hi"}
        with self._lock:
            if self.state in ("capturing", "processing", "speaking"):
                return {"ok": False, "error": "cannot change language during a lesson"}
            self.language = lang
        return {"ok": True}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self.state, "label": USER_LABELS.get(self.state, self.state),
                "state_age_s": round(time.time() - self.state_since, 1),
                "language": self.language, "auto_enabled": self.auto_enabled,
                "engine": self.engine_name, "engine_ready": self.engine_ready,
                "eligible": self.eligible, "eligibility_reason": self.eligibility_reason,
                "stable_ms": self.stable_ms, "dwell_progress": round(self.dwell_progress, 3),
                "dwell_target_ms": settings.stable_dwell_ms, "metrics": self.metrics,
                "hint": self.hint, "error": self.error, "crop": self.crop,
                "mirror": {"h": settings.camera_hmirror, "v": settings.camera_vflip},
                "result": self.last_result, "trace": self.last_trace, "history": self.history[:10],
                "audio": {"playing": self.audio.playing, "source": self.audio.last_source, "asset": self.audio.last_asset},
                "camera": self.camera.health.to_dict(),
                "thresholds": {"motion": settings.motion_threshold, "sharpness": settings.sharpness_threshold,
                               "occupancy_min": settings.text_occupancy_min, "exposure": [settings.exposure_min, settings.exposure_max],
                               "confidence": settings.ocr_confidence_threshold},
            }
