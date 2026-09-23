"""Per-capture trace object, persisted as JSON so timings survive the session."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_counter = 0


def new_capture_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"{datetime.now().strftime('%Y%m%d')}-{_counter:03d}"


class Trace(dict):
    def __init__(self, capture_id: str, language: str, image_source: str, trigger: dict, crop: dict):
        super().__init__()
        self.update({
            "capture_id": capture_id, "language": language, "image_source": image_source,
            "trigger": trigger, "crop": crop, "ocr": None, "gate": None, "lesson_id": None,
            "audio": None, "timing_ms": {}, "started_at": datetime.now().isoformat(timespec="milliseconds"),
        })
        self._t0 = time.perf_counter()

    def mark(self, stage: str, ms: int | None = None) -> None:
        self["timing_ms"][stage] = int((time.perf_counter() - self._t0) * 1000) if ms is None else ms

    def save(self, trace_dir: str | Path) -> Path:
        p = Path(trace_dir)
        p.mkdir(parents=True, exist_ok=True)
        out = p / f"{self['capture_id']}.json"
        out.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
