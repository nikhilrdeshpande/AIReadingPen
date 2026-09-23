# AI Reading Pen Event Day Runbook

Updated 23 September 2026

## Pack before leaving

- XIAO ESP32S3 Sense, known-good USB cable, power bank, and Mac charger.
- Printed A4 card pack at 100 percent scale, cut into cards.
- Camera stand or fixed-height jig, removable tape, and a small diffuse lamp.
- Local copies of camera firmware, OCR models, Marathi and Hindi trained data, lesson manifest, audio cache, and four saved demonstration crops.
- API keys stored outside the repository, only if live TTS will be attempted.
- Headphones for testing and a backup portable speaker if the venue is noisy.

## Files to verify

- `content/lessons.yaml`
- `content/audio/mr_ghar_v1.wav`
- `content/audio/mr_jhaad_v1.wav`
- `content/audio/mr_phulpakhru_v1.wav`
- `content/audio/hi_kitaab_v1.wav`
- `fixtures/mr_ghar.jpg`
- `fixtures/mr_jhaad.jpg`
- `fixtures/mr_phulpakhru.jpg`
- `fixtures/hi_kitaab.jpg`

Generate a checksum list after the final audio review and keep it with the package.

## Three hours

### 0 to 20 minutes

1. Identify the camera module fitted to this board.
2. Flash or start the known camera server build.
3. Fix camera height, lighting, card position, and orientation.
4. Confirm preview and twenty consecutive snapshots.

Exit only when the framing is repeatable. If not, use the laptop or phone camera input and preserve the rest of the product loop.

### 20 to 45 minutes

1. Connect the Mac capture client to the snapshot endpoint.
2. Draw the target band and save one cropped image per capture.
3. Add the shared capture primitive, Space key, and large Capture control.
4. Add the automatic quality/stability gate with a 600–900 ms dwell, one-shot lock, scene-change re-arm, and simple progress feedback.
5. Add saved-image replay and timing IDs.

Exit when the same card auto-captures exactly once while held in place, a different card re-arms it, and twenty stored crops are readable. Confirm that manual capture works when auto-capture is disabled.

### 45 to 85 minutes

1. Start the preinstalled PaddleOCR environment and preload the Devanagari model.
2. Run MR 01, MR 02, MR 08, and HI 02 five times each.
3. Run the complete 24-card benchmark once.
4. Switch to the prevalidated Tesseract route only if Paddle setup fails or a required demo word remains unreliable.

Exit when all four formal demo cards pass. Do not tune against every hard card at the expense of the demonstration.

### 85 to 115 minutes

1. Load the reviewed manifest.
2. Add visible Marathi and Hindi selection.
3. Add NFC normalization, exact manifest matching, and the calibrated confidence threshold.
4. Show Not confident for any rejected result.

Exit when no unsupported OCR result receives a generated lesson.

### 115 to 140 minutes

1. Play cached audio for all four demo words.
2. Check laptop output device and venue volume.
3. Verify that the complete demonstration works with internet unavailable.
4. If stable, connect and prewarm Sarvam or OpenAI as a recovery route.

Exit when the cached path is flawless. Live TTS is optional.

### 140 to 160 minutes

1. Verify camera, OCR, cache, and provider health indicators.
2. Test saved-image replay.
3. Test the visible manual manifest override and confirm the log marks it.
4. Restart every component using only this runbook.

### 160 to 180 minutes

Freeze the build. Run the exact two-minute script five times. Do not retune capture thresholds, add child speech input, or add design polish after the freeze.

## Five minute pre-demo check

1. Connect power and disable sleep on the Mac.
2. Confirm the XIAO address and one fresh snapshot.
3. Confirm the app shows Searching, Marathi, and auto-capture enabled.
4. Place MR 01 inside the taped card position; confirm one automatic capture and no repeat while the card remains still.
5. Remove the card, confirm re-arm, then test Capture or Space once as the manual fallback.
6. Confirm the four cached audio assets and laptop volume.
7. Put the four demo cards in order: MR 01, MR 02, MR 08, HI 02.
8. Open the operator drawer once, confirm saved-image recovery, then close it.

## Two minute demonstration script

### 0 to 20 seconds

“This is a prototype reading pen for children learning Devanagari. The camera points to one word; the Mac recognizes it; and a reviewed lesson teaches how to blend it.”

Show the target band and bring MR 01 `घर` into position. Hold it steady while the progress indicator completes; do not click.

### 20 to 50 seconds

Let `घर` auto-capture. Let the whole audio sequence finish. Point to the recognized word, confidence, and `घ + र` lesson on screen.

### 50 to 85 seconds

Remove the first card, wait for re-arm, then hold MR 02 `झाड` steady for automatic capture. Explain that the lesson data is reviewed and separates the `झा` unit from `ड`.

### 85 to 120 seconds

Auto-capture MR 08 `फुलपाखरू`. Explain that the immediate response does not wait for an LLM.

If the audience is engaged and time remains, switch the visible language control to Hindi and auto-capture HI 02 `किताब`. State that Marathi and Hindi share the script, so the product uses an explicit language context.

## Recovery ladder

Use the first successful step and continue the script.

### Automatic capture does not fire or repeats

1. If it does not fire after roughly two seconds of a steady, readable card, press Capture or Space immediately and continue.
2. If it repeats while the card is stationary, disable auto-capture and use manual capture for the rest of the demonstration.
3. Do not tune motion, sharpness, or re-arm thresholds in front of the audience.

### Word is blurry or missing

1. Return the card to the taped position.
2. Wipe the lens and improve front light.
3. Capture once more.
4. Use the saved crop if the second attempt fails.

### XIAO disconnects

1. Do not debug firmware in front of the audience.
2. Load the saved crop through the operator drawer.
3. Say that the same recognition and teaching path is running from a captured frame.

### OCR fails or returns the wrong word

1. Confirm the selected language.
2. Recapture once.
3. Use the visible manual manifest selection and continue.
4. Never claim that a manual override was OCR.

### TTS provider stalls

1. Cancel the provider request.
2. Replay the cached asset.
3. Leave live TTS disabled for the rest of the demonstration.

### Audio cannot be heard

1. Confirm the Mac output device and volume.
2. Use the backup speaker.
3. Continue with the visual word and chunk sequence while the operator restores sound.

## Stop conditions

Stop adding features when all four formal demo cards pass five times with one automatic trigger per card. Stop changing capture or OCR parameters after the final rehearsal unless a demo word fails. Stop using a live provider after one timeout during rehearsal. Stop the camera path and use saved images if it cannot be restored within ten minutes during the build.

## After the demonstration

- Save the timing trace and card benchmark results.
- Record which fallbacks were used.
- Delete incidental frames unless they are part of the agreed test set.
- Do not retain child audio or images.
- List disputed chunking or pronunciation for educator review before expanding the vocabulary.
