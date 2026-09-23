# AI Reading Pen Claude Code Handoff

Updated 23 September 2026

## Build objective

Build a narrow event prototype that captures one printed Devanagari word from a XIAO ESP32S3 Sense, recognizes it on a Mac, selects a human-reviewed Marathi or Hindi lesson, and starts approved audio within 1.5 seconds at the 95th percentile in the fixed demonstration setup.

The core loop must work without an LLM and without live TTS. The event demonstration uses cached audio. Cloud TTS is a recovery and extension route.

## Final product constraints

- Primary demonstration language: Marathi.
- Hindi is a visible, manually selected mode. Never infer the language from Devanagari script alone.
- Input: one printed word centered in a fixed target band.
- Trigger: automatic capture is the primary interaction. A large Capture control and Space key remain available as immediate manual fallbacks.
- Audio: laptop speaker.
- No child voice, face capture, pronunciation scoring, full-page OCR, finger tracking, or on-device OCR.
- Do not invent teaching chunks for an unsupported or low-confidence OCR result.

## Architecture

```text
XIAO camera server
  preview URL + snapshot URL
            |
            v
Mac capture controller
  target quality + stability dwell + lock/re-arm
  manual fallback + fixed crop + saved-image replay + trace timing
            |
            v
OCR adapter
  PaddleOCR PP-OCRv5 Devanagari preferred
  Tesseract mar/hin benchmarked fallback
            |
            v
confidence gate
  NFC + trim + exact manifest match
            |
            v
lesson manifest
  language + approved chunks + approved speech script
            |
            v
audio adapter
  cache first -> Sarvam stream -> OpenAI stream
```

Keep optional LLM enrichment on a separate asynchronous branch after first audio.

## Required modules

### Camera client

- Configurable preview and snapshot URLs.
- Timeouts, health check, and reconnect state.
- Save every accepted JPEG under its `capture_id` when debug mode is enabled.
- Accept a local image file through the operator drawer so the complete downstream loop is replayable without hardware.

### Capture controller

- Draw a central target band over the preview.
- Crop to the target band on the Mac.
- Record transfer and preprocessing latency.
- Sample the target band at roughly 4–6 frames per second; do not run OCR on every preview frame.
- Mark a frame eligible only when text occupancy, sharpness, and exposure pass calibrated thresholds.
- Accumulate stable time while motion remains below a calibrated threshold. Reset the dwell when eligibility or stability is lost.
- After 600–900 ms of continuous stability, request one high-resolution snapshot and enter `locked`.
- Re-arm only after the target becomes empty or changes for a short continuous interval. Keep a minimum cooldown as an additional guard, not as the sole duplicate-prevention mechanism.
- Expose the same `capture()` primitive to the button and Space key. Manual capture bypasses the dwell but enters the same lock/re-arm cycle.
- Reject all capture requests while `capturing`, `processing`, `speaking`, or `locked`, unless the operator explicitly cancels.

### OCR adapter interface

```python
class OCRCandidate(TypedDict):
    text: str
    confidence: float | None

class OCRResult(TypedDict):
    engine: str
    raw_text: str
    candidates: list[OCRCandidate]
    latency_ms: int

def recognize(image, language: Literal["mr", "hi"]) -> OCRResult: ...
```

Implement PaddleOCR first. Keep a Tesseract adapter behind the same interface. Preload the selected engine at application start. Do not download a model after the audience arrives.

### Normalization and confidence gate

Normalize to NFC, trim spaces and outer punctuation, collapse internal whitespace, and preserve the raw result. Then require all of the following:

1. The operator-selected language is present.
2. The normalized result exactly matches one manifest entry for that language.
3. The engine confidence is above a threshold calibrated on the printed pack, or the selected engine has a documented alternative quality score that has been calibrated.

If any condition fails, return `needs_recapture`. Do not silently choose the nearest dictionary word. A manual operator selection must mark `override: true` in the trace.

### Lesson manifest

Use versioned UTF-8 YAML or JSON. This is content, not generated application state.

```yaml
- id: mr_jhaad_v1
  language: mr
  word: झाड
  normalized: झाड
  teaching_chunks: [झा, ड]
  lesson_class: aa_matra
  display_prompt: "झा + ड"
  speech_script: "झाड. झा आणि ड. झाड. आता तू वाच."
  audio_asset: audio/mr_jhaad_v1.wav
  review_status: approved_for_demo
```

Never derive `teaching_chunks` directly from code points or grapheme clusters. A deterministic analyzer may produce orthographic metadata for tests, but only approved manifest data reaches the child-facing lesson.

### Audio adapter

Priority order:

1. Existing cached WAV or PCM asset.
2. Sarvam Bulbul v3 streaming connection.
3. OpenAI speech streaming.
4. Visible failure with replay option.

Warm the audio device. Open the Sarvam WebSocket before rehearsal if live TTS will be shown. Cache provider output by a hash of provider, model, voice, language, exact script, and pronunciation-dictionary version. Never synthesize isolated combining marks.

### UI states

```text
booting -> searching -> stabilizing -> capturing -> processing -> speaking -> locked
               ^                                                        |
               |---------------------- rearming <-----------------------|
                                  \-> needs_recapture -> rearming
                                  \-> operator_recovery -> rearming
```

Manual Capture or Space may move `searching` or `stabilizing` directly to `capturing`. Show `Finding word`, `Hold steady`, `Captured`, and `Move to next word` so the user understands the system without watching a technical status panel.

Display:

- Preview and target band.
- Marathi or Hindi selector.
- Automatic-capture progress plus Capture button and Space shortcut.
- State label.
- Recognized word and confidence.
- Approved chunks.
- Audio source: cache, Sarvam, or OpenAI.
- One corrective retry instruction.

Hide these in an operator drawer:

- Load saved image.
- Manual manifest selection.
- OCR engine switch.
- Camera, model, cache, and provider health.
- Latest timing trace.

## Trace schema

```json
{
  "capture_id": "20260923-001",
  "language": "mr",
  "image_source": "xiao",
  "trigger": {"source": "auto", "stable_ms": 742},
  "crop": {"x": 0.1, "y": 0.38, "w": 0.8, "h": 0.24},
  "ocr": {
    "engine": "paddle-devanagari-v5-mobile",
    "raw": "झाड",
    "normalized": "झाड",
    "confidence": 0.97
  },
  "gate": {"accepted": true, "override": false},
  "lesson_id": "mr_jhaad_v1",
  "audio": {"source": "cache", "asset": "mr_jhaad_v1.wav"},
  "timing_ms": {"transfer": 96, "preprocess": 18, "ocr": 214, "first_audio": 486}
}
```

## Environment configuration

```dotenv
CAMERA_PREVIEW_URL=http://xiao.local:81/stream
CAMERA_SNAPSHOT_URL=http://xiao.local/capture
DEMO_LANGUAGE=mr
AUTO_CAPTURE_ENABLED=true
PREVIEW_SAMPLE_MS=200
STABLE_DWELL_MS=750
MOTION_THRESHOLD=CALIBRATE_ME
SHARPNESS_THRESHOLD=CALIBRATE_ME
TEXT_OCCUPANCY_MIN=CALIBRATE_ME
REARM_CHANGE_MS=400
MIN_CAPTURE_COOLDOWN_MS=2000
OCR_ENGINE=paddle
OCR_CONFIDENCE_THRESHOLD=CALIBRATE_ME
LESSON_MANIFEST=content/lessons.yaml
AUDIO_CACHE_DIR=content/audio
ENABLE_LIVE_TTS=false
SARVAM_API_KEY=
OPENAI_API_KEY=
DEBUG_SAVE_FRAMES=true
```

Do not commit API keys. The application must start in cache-only mode when the provider keys are absent.

## Build order

1. Start the known Seeed camera server example and confirm twenty snapshots.
2. Build the shared capture primitive, target crop, saved-image replay, manual controls, and trace timing.
3. Add the automatic controller: eligibility, stability dwell, one-shot lock, scene-change re-arm, and progress feedback.
4. Preload PaddleOCR and test the four formal demo cards.
5. Add manifest lookup, confidence gate, and language selector.
6. Add cached audio and make the full loop pass without internet.
7. Add operator recovery controls.
8. Benchmark all 24 cards. Try Tesseract only if Paddle fails the release gate or setup is fragile.
9. Add live Sarvam or OpenAI TTS only if the offline demonstration is already frozen.

## Tests

### Unit tests

- NFC normalization preserves every supplied word.
- Combining marks never detach during display or storage.
- The same word may exist in both language namespaces.
- Exact-match gate rejects punctuation, extra words, and non-manifest candidates after the allowed normalization steps.
- Manual selection adds `override: true`.
- Audio cache key changes when the script or pronunciation dictionary changes.

### Integration tests

- Saved image to cached audio works with the camera unplugged and network disabled.
- Camera snapshot to cached audio works twenty times without a process restart.
- A provider timeout returns control to the cache route.
- Switching from Marathi to Hindi never changes an active lesson.
- The UI remains responsive while audio plays.
- Each formal demo card auto-captures once after a 600–900 ms stable dwell.
- A card held still for five seconds does not trigger a second capture.
- Removing or visibly changing the card re-arms capture within one second.
- Manual Capture and Space work when auto-capture is disabled or does not qualify, and cannot cause a double fire.
- An empty target band causes no captures during a twenty-second test.

### Release gate

- At least 22 of 24 supplied cards are recognized in the fixed setup.
- `घर`, `झाड`, `फुलपाखरू`, and `किताब` each complete five consecutive times.
- Stable target to first cached audio is below 2.2 seconds at p95 across twenty warm runs; trigger to first audio remains below 1.5 seconds.
- Each demo card fires exactly once while held in place, and the next card re-arms automatically.
- The manual Capture/Space fallback completes the same path when auto-capture is disabled.
- The saved-image and manual-manifest fallbacks each recover the demonstration in under ten seconds.
- A cold restart reaches Ready in under three minutes using only the runbook.

## Cut order

Cut features in this order when time is short:

1. Decorative capture animation; retain simple text/progress feedback.
2. Live TTS.
3. Hindi live demonstration.
4. Meaning and example generation.
5. OCR engine comparison UI.

Never cut the automatic capture controller, manual fallback, confidence gate, language selector, cached audio, saved-image replay, or visible recovery controls. If auto-capture becomes unreliable during the live demonstration, use manual capture immediately and continue; repair thresholds only after the session.

## Definition of done

The build is done when a fresh operator can use the runbook to start the system, auto-capture the four formal demonstration cards exactly once each, hear the reviewed lessons, use manual capture when auto-capture is intentionally disabled, recover from a camera failure with a saved image, and show a timing trace. More features do not improve the event once this condition is met.
