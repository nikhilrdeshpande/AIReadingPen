# AI Reading Pen

Event prototype: a XIAO ESP32S3 Sense camera captures one printed Devanagari word, the Mac recognises it,
a reviewed Marathi or Hindi lesson is selected, and cached audio teaches the blend. Specs, handoff and the
event-day runbook are in `docs/`; they are the original brief and predate two later decisions made during the
build: words outside the 24-card pack are taught with auto-generated, clearly labelled drills, and an optional
"your turn" step records about three seconds of speech for transcription. That audio is held in memory only,
sent to Sarvam for transcription, and never written to disk; traces keep the transcript text only.

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
| `app/audio.py` | cache -> Sarvam stitched build -> OpenAI -> visible failure; playback via `afplay` |
| `app/lesson_audio.py`, `app/sarvam.py`, `app/practice.py` | Segment-wise teaching audio, Sarvam client (chat/TTS/STT), child "your turn" check |
| `app/akshara.py` | Deterministic Devanagari akshara splitter for words outside the manifest |
| `scripts/gen_audio.py` | Generate `content/audio/*.wav` from speech scripts (`--provider openai|sarvam|macos`) |
| `scripts/bench_cards.py` | Benchmark both engines on a folder of card images |
| `fixtures/` | Synthetic crops of the 4 demo words for saved-image replay |
| `firmware/GanapatiCam/` | XIAO camera firmware + `usb_feed.py` USB bridge (Wi-Fi creds in git-ignored `wifi_secrets.h`) |
| `traces/`, `debug_frames/` | Per-capture JSON traces and saved frames/crops (git-ignored) |

## Teaching audio and the "your turn" step

Each lesson is spoken as separate Sarvam Bulbul v3 segments stitched with real silence: the word, each sound
alone and slowly, the blend, the word again, then "आता तू म्हण" (now you say it). `scripts/gen_audio.py --provider sarvam`
builds the 24 reviewed clips (`--llm` lets Sarvam's LLM write the script instead of the template; review the
`.segments.json` sidecars, the LLM once produced a conjunct that was not in the word). Words outside the pack
are built live the same way and cached.

After the lesson the app records the child for `PRACTICE_SECONDS`, transcribes with Sarvam Saarika, compares to
the target (exact or close match) and plays a short praise/retry clip. `MIC_DEVICE` picks the microphone by
name substring. Toggle "Your turn (mic)" in the UI to skip it. macOS will ask for microphone permission for
the terminal the first time.

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

## License

MIT, see `LICENSE`. Lesson content in `content/` and the docs in `docs/` are part of the same repository and
license. Cached audio was generated with Sarvam AI Bulbul v3; OCR uses PaddleOCR (Apache 2.0) and Tesseract
(Apache 2.0). Bring your own API keys; none are included.
