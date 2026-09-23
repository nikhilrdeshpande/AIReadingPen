# AI Reading Pen

Event prototype: a XIAO ESP32S3 Sense camera captures one printed Devanagari word, the Mac recognises it,
a reviewed Marathi or Hindi lesson is selected, and cached audio teaches the blend. Specs, handoff and the
event-day runbook are in `docs/`.

## Quick start (Mac)

```sh
# one-time
uv venv --python 3.12 .venv && uv pip install -r requirements.txt     # or see docs; PaddleOCR + Tesseract data are pre-fetched
cp .env.example .env                                                  # add OPENAI_API_KEY / SARVAM_API_KEY only if generating audio

# camera over USB (no Wi-Fi needed): plug the XIAO in, then in a second terminal
.venv/bin/python firmware/GanapatiCam/usb_feed.py                     # serves http://localhost:8080/stream and /capture

# app
./run.sh                                                              # open http://localhost:8000
```

Green `ready` pill = camera answering, OCR model warm, demo audio present. Without a camera the whole loop
still runs from the operator drawer (load fixture or file), which is also the rehearsed recovery path.

## Layout

| Path | What |
|---|---|
| `app/server.py` | FastAPI: UI, `/preview.mjpg`, `/state`, `/health`, operator actions |
| `app/capture.py` | Capture controller: eligibility, stable dwell, one-shot lock, scene-change re-arm, shared `capture()` |
| `app/preprocess.py` | Target-band crop, occupancy/sharpness/exposure/motion metrics, tight word crop |
| `app/ocr/` | `paddle_engine.py` (PP-OCRv5 Devanagari, recognition-only, ~50 ms) and `tesseract_engine.py` (mar/hin) |
| `app/gate.py`, `app/textnorm.py` | NFC + trim, exact manifest match, calibrated confidence threshold. No autocorrect. |
| `app/manifest.py`, `content/lessons.yaml` | Versioned reviewed lesson content, 24 cards, 4 approved for demo |
| `app/audio.py` | cache -> Sarvam -> OpenAI -> visible failure; playback via `afplay` |
| `scripts/gen_audio.py` | Generate `content/audio/*.wav` from speech scripts (`--provider openai|sarvam|macos`) |
| `scripts/bench_cards.py` | Benchmark both engines on a folder of card images |
| `fixtures/` | Synthetic crops of the 4 demo words for saved-image replay |
| `firmware/GanapatiCam/` | XIAO camera firmware + `usb_feed.py` USB bridge (Wi-Fi creds in git-ignored `wifi_secrets.h`) |
| `traces/`, `debug_frames/` | Per-capture JSON traces and saved frames/crops (git-ignored) |

## Calibration

All thresholds live in `.env`. With the camera running, open the operator drawer: the band metrics line shows
live `occupancy`, `sharpness`, `exposure`, `motion` and the current eligibility reason. Set
`TEXT_OCCUPANCY_MIN` just below the value for a card in position, `SHARPNESS_THRESHOLD` below a focused card
and above a moving one, `MOTION_THRESHOLD` just above the idle jitter. Restart the app after editing `.env`.

## Tests

```sh
.venv/bin/python -m pytest          # unit + controller tests with a fake camera, no model download
.venv/bin/python scripts/bench_cards.py fixtures
```
