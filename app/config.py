"""Configuration from environment / .env. All thresholds are tunable without code changes."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    v = os.environ.get(name, "").strip()
    if not v or v.upper() == "CALIBRATE_ME":
        return default
    return float(v)


def _i(name: str, default: int) -> int:
    return int(_f(name, default))


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Camera. USB bridge (usb_feed.py) serves both on :8080; Wi-Fi firmware serves stream on :81.
    camera_preview_url: str = os.environ.get("CAMERA_PREVIEW_URL", "http://localhost:8080/stream")
    camera_snapshot_url: str = os.environ.get("CAMERA_SNAPSHOT_URL", "http://localhost:8080/capture")
    camera_status_url: str = os.environ.get("CAMERA_STATUS_URL", "")
    camera_timeout_s: float = _f("CAMERA_TIMEOUT_S", 3.0)

    demo_language: str = os.environ.get("DEMO_LANGUAGE", "mr")
    auto_capture_enabled: bool = _b("AUTO_CAPTURE_ENABLED", True)

    # Capture controller
    preview_sample_ms: int = _i("PREVIEW_SAMPLE_MS", 200)
    stable_dwell_ms: int = _i("STABLE_DWELL_MS", 750)
    motion_threshold: float = _f("MOTION_THRESHOLD", 6.0)        # mean abs diff (0-255) between sampled bands
    sharpness_threshold: float = _f("SHARPNESS_THRESHOLD", 60.0)  # variance of Laplacian on the band
    text_occupancy_min: float = _f("TEXT_OCCUPANCY_MIN", 0.015)   # fraction of dark pixels in band
    text_occupancy_max: float = _f("TEXT_OCCUPANCY_MAX", 0.45)    # above this the band is covered / dark
    exposure_min: float = _f("EXPOSURE_MIN", 60.0)                # mean gray of band
    exposure_max: float = _f("EXPOSURE_MAX", 253.0)
    rearm_change_ms: int = _i("REARM_CHANGE_MS", 400)
    rearm_diff_threshold: float = _f("REARM_DIFF_THRESHOLD", 18.0)  # mean abs diff vs locked band to count as "changed"
    min_capture_cooldown_ms: int = _i("MIN_CAPTURE_COOLDOWN_MS", 2000)

    # Target band as fractions of the preview frame
    crop_x: float = _f("CROP_X", 0.10)
    crop_y: float = _f("CROP_Y", 0.36)
    crop_w: float = _f("CROP_W", 0.80)
    crop_h: float = _f("CROP_H", 0.28)

    # OCR
    ocr_engine: str = os.environ.get("OCR_ENGINE", "paddle")
    ocr_confidence_threshold: float = _f("OCR_CONFIDENCE_THRESHOLD", 0.80)
    # Words outside the reviewed manifest: accepted only with a stricter confidence, chunks auto-generated and labelled.
    open_vocabulary: bool = _b("OPEN_VOCABULARY", True)
    open_vocab_confidence_threshold: float = _f("OPEN_VOCAB_CONFIDENCE_THRESHOLD", 0.90)
    tessdata_dir: str = os.environ.get("TESSDATA_DIR", str(ROOT / "models" / "tessdata"))

    # Content
    lesson_manifest: str = os.environ.get("LESSON_MANIFEST", str(ROOT / "content" / "lessons.yaml"))
    audio_cache_dir: str = os.environ.get("AUDIO_CACHE_DIR", str(ROOT / "content" / "audio"))

    # TTS
    enable_live_tts: bool = _b("ENABLE_LIVE_TTS", True)   # cache-only automatically when no provider key is set
    sarvam_api_key: str = os.environ.get("SARVAM_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    tts_timeout_s: float = _f("TTS_TIMEOUT_S", 4.0)

    debug_save_frames: bool = _b("DEBUG_SAVE_FRAMES", True)
    debug_dir: str = os.environ.get("DEBUG_DIR", str(ROOT / "debug_frames"))
    trace_dir: str = os.environ.get("TRACE_DIR", str(ROOT / "traces"))

    port: int = _i("PORT", 8000)

    def crop_box(self) -> dict:
        return {"x": self.crop_x, "y": self.crop_y, "w": self.crop_w, "h": self.crop_h}


settings = Settings()
