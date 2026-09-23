"""FastAPI server: serves the UI, re-broadcasts the preview, exposes state and operator actions."""
from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .audio import AudioPlayer
from .camera import CameraClient
from .capture import CaptureController
from .config import ROOT, settings
from .manifest import Manifest

app = FastAPI(title="AI Reading Pen")
STATIC = Path(__file__).parent / "static"

manifest = Manifest(settings.lesson_manifest)
camera = CameraClient(settings.camera_preview_url, settings.camera_snapshot_url, settings.camera_status_url,
                      settings.camera_timeout_s)
audio = AudioPlayer()
ctl = CaptureController(camera, manifest, audio)
_boot = {"started": time.time(), "ready": False, "steps": []}


def _boot_sequence() -> None:
    def step(name, fn):
        t0 = time.time()
        try:
            fn()
            _boot["steps"].append({"step": name, "ok": True, "ms": int((time.time() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001
            _boot["steps"].append({"step": name, "ok": False, "error": str(e)[:200]})
    step("camera", camera.start)
    step("audio", audio.warm)
    step("ocr", ctl.load_engine)
    step("controller", ctl.start)
    _boot["ready"] = ctl.engine_ready


threading.Thread(target=_boot_sequence, daemon=True).start()


# ---------------- pages ----------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/preview.mjpg")
async def preview():
    async def gen():
        last = 0
        while True:
            jpg, seq = camera.latest()
            if jpg is not None and seq != last:
                last = seq
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n"
            await asyncio.sleep(0.05)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/state")
def state():
    s = ctl.snapshot()
    s["boot"] = _boot
    return JSONResponse(s)


@app.get("/health")
def health():
    audio_ok = {l.id: (Path(settings.lesson_manifest).parent / l.audio_asset).exists() for l in manifest.lessons if l.approved}
    return {
        "ready": _boot["ready"] and camera.health.to_dict()["connected"],
        "boot": _boot, "camera": camera.health.to_dict(), "camera_status": camera.status(),
        "ocr": {"engine": ctl.engine_name, "ready": ctl.engine_ready},
        "audio_cache": audio_ok, "all_demo_audio_present": all(audio_ok.values()),
        "providers": {"sarvam": bool(settings.sarvam_api_key), "openai": bool(settings.openai_api_key),
                      "live_tts_enabled": settings.enable_live_tts},
        "manifest": {"version": manifest.version, "lessons": len(manifest.lessons), "path": settings.lesson_manifest},
        "config": {"preview": settings.camera_preview_url, "snapshot": settings.camera_snapshot_url},
    }


@app.get("/lessons")
def lessons():
    return [l.to_dict() for l in manifest.lessons]


@app.get("/debug/{name}")
def debug_image(name: str):
    p = Path(settings.debug_dir) / name
    if not p.exists() or p.suffix != ".jpg":
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


# ---------------- actions ----------------
class Lang(BaseModel):
    language: str


class Auto(BaseModel):
    enabled: bool


class Engine(BaseModel):
    engine: str


class Override(BaseModel):
    lesson_id: str


class Src(BaseModel):
    source: str = "button"


@app.post("/capture")
def capture(body: Src | None = None):
    return ctl.capture(source=(body.source if body else "button"))


@app.post("/cancel")
def cancel():
    ctl.cancel()
    return {"ok": True}


@app.post("/language")
def language(body: Lang):
    return ctl.set_language(body.language)


@app.post("/auto")
def auto(body: Auto):
    ctl.auto_enabled = body.enabled
    return {"ok": True, "auto_enabled": ctl.auto_enabled}


@app.post("/practice")
def practice_toggle(body: Auto):
    ctl.practice_enabled = body.enabled
    return {"ok": True, "practice_enabled": ctl.practice_enabled}


@app.post("/mic_test")
def mic_test():
    from .practice import mic_test as _mt
    return _mt(1.0)


@app.post("/engine")
def engine(body: Engine):
    if body.engine not in ("paddle", "tesseract"):
        return {"ok": False, "error": "engine must be paddle or tesseract"}
    threading.Thread(target=ctl.load_engine, args=(body.engine,), daemon=True).start()
    return {"ok": True}


@app.post("/override")
def override(body: Override):
    return ctl.override(body.lesson_id)


@app.post("/replay")
def replay():
    return ctl.replay_audio()


@app.post("/load_image")
async def load_image(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        return {"ok": False, "error": "empty file"}
    return ctl.capture(source="saved_image", image_bytes=data, image_source=f"file:{file.filename}")


@app.post("/load_fixture")
def load_fixture(body: Override):
    """Load a bundled fixture by name (e.g. mr_ghar) through the same capture path."""
    p = ROOT / "fixtures" / f"{body.lesson_id}.jpg"
    if not p.exists():
        return {"ok": False, "error": "no such fixture"}
    return ctl.capture(source="saved_image", image_bytes=p.read_bytes(), image_source=f"fixture:{p.name}")


@app.get("/fixtures")
def fixtures():
    return sorted(p.stem for p in (ROOT / "fixtures").glob("*.jpg"))


def _hard_exit(signum, frame):
    # PaddlePaddle's worker threads segfault during normal interpreter teardown, which macOS reports as
    # "Python quit unexpectedly". Nothing needs flushing (traces are written as they happen), so leave at once.
    os._exit(0)


@app.post("/reload")
def reload_settings():
    """Re-read .env and apply thresholds/crop/language defaults live, no restart needed."""
    changed = settings.reload()
    ctl.crop = settings.crop_box()
    return {"ok": True, "changed": changed}


def main() -> None:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    signal.signal(signal.SIGTERM, _hard_exit)
    signal.signal(signal.SIGINT, _hard_exit)
    print(f"AI Reading Pen -> http://localhost:{settings.port}  preview={settings.camera_preview_url}")
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
