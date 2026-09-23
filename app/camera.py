"""Camera client: MJPEG preview reader (own thread), snapshot fetch, health, reconnect.
Works with the GanapatiCam firmware over Wi-Fi (:81/stream, /capture) and the USB bridge usb_feed.py
(http://localhost:8080/stream, /capture)."""
from __future__ import annotations

import threading
import time
import urllib.request
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class CameraHealth:
    connected: bool = False
    last_frame_at: float = 0.0
    fps: float = 0.0
    frames: int = 0
    error: str = ""
    reconnects: int = 0
    frame_size: tuple[int, int] = (0, 0)

    def to_dict(self) -> dict:
        age = time.time() - self.last_frame_at if self.last_frame_at else None
        return {"connected": self.connected and age is not None and age < 3.0, "fps": round(self.fps, 1),
                "frames": self.frames, "error": self.error, "reconnects": self.reconnects,
                "frame_age_s": round(age, 2) if age is not None else None, "frame_size": list(self.frame_size)}


def decode_jpeg(jpg: bytes) -> np.ndarray | None:
    arr = np.frombuffer(jpg, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def orient(img: np.ndarray, hmirror: bool, vflip: bool) -> np.ndarray:
    """Apply the configured mirror/flip so text reads correctly. Cheap: only sampled bands and snapshots go through it."""
    if hmirror and vflip:
        return cv2.flip(img, -1)
    if hmirror:
        return cv2.flip(img, 1)
    if vflip:
        return cv2.flip(img, 0)
    return img


class CameraClient:
    def __init__(self, preview_url: str, snapshot_url: str, status_url: str = "", timeout_s: float = 3.0):
        self.preview_url = preview_url
        self.snapshot_url = snapshot_url
        self.status_url = status_url
        self.timeout_s = timeout_s
        self.health = CameraHealth()
        self._latest_jpg: bytes | None = None
        self._latest_seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- preview stream ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._read_stream()
                backoff = 1.0
            except Exception as e:  # noqa: BLE001
                self.health.connected = False
                self.health.error = f"{type(e).__name__}: {e}"[:200]
                self.health.reconnects += 1
                time.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)

    def _read_stream(self) -> None:
        req = urllib.request.Request(self.preview_url, headers={"User-Agent": "aireadingpen"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            buf = b""
            t_win, n_win = time.time(), 0
            while not self._stop.is_set():
                chunk = resp.read(16384)
                if not chunk:
                    raise ConnectionError("stream ended")
                buf += chunk
                while True:
                    a = buf.find(b"\xff\xd8")
                    b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
                    if a < 0 or b < 0:
                        if len(buf) > 4_000_000:
                            buf = b""
                        break
                    jpg = buf[a:b + 2]
                    buf = buf[b + 2:]
                    with self._lock:
                        self._latest_jpg = jpg
                        self._latest_seq += 1
                    now = time.time()
                    self.health.connected = True
                    self.health.error = ""
                    self.health.last_frame_at = now
                    self.health.frames += 1
                    n_win += 1
                    if now - t_win >= 1.0:
                        self.health.fps = n_win / (now - t_win)
                        t_win, n_win = now, 0

    def latest(self) -> tuple[bytes | None, int]:
        with self._lock:
            return self._latest_jpg, self._latest_seq

    def wait_next(self, last_seq: int, timeout: float = 1.0) -> tuple[bytes | None, int]:
        t_end = time.time() + timeout
        while time.time() < t_end:
            jpg, seq = self.latest()
            if seq != last_seq and jpg is not None:
                return jpg, seq
            time.sleep(0.01)
        return None, last_seq

    # ---- snapshot ----
    def snapshot(self, timeout: float | None = None) -> bytes:
        """One JPEG from the snapshot endpoint. Raises on failure; caller may fall back to latest preview frame."""
        req = urllib.request.Request(self.snapshot_url, headers={"User-Agent": "aireadingpen"})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout_s) as r:
            data = r.read()
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("snapshot is not a JPEG")
        return data

    def status(self) -> dict | None:
        if not self.status_url:
            return None
        try:
            import json
            with urllib.request.urlopen(self.status_url, timeout=2) as r:
                return json.loads(r.read().decode())
        except Exception:  # noqa: BLE001
            return None
