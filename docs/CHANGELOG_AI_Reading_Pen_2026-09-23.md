# AI Reading Pen Package Changelog

23 September 2026

## Decisions changed

- Made automatic capture the primary interaction, using a Mac-side quality gate, 600–900 ms stable dwell, one-shot lock, scene-change re-arm, and cooldown. A large Capture button and Space key remain immediate manual fallbacks and enter the same lock/re-arm cycle.
- Replaced continuous OCR on a stream with low frame rate preview plus one still JPEG per recognition attempt.
- Moved motion and quality checks to the Mac, keeping XIAO firmware limited to camera transport and health.
- Set Marathi as the primary event path. Hindi remains prepared but requires an explicit language selector because isolated Devanagari words do not reliably identify the language.
- Reframed Unicode grapheme analysis as text-safety infrastructure, not pedagogy. Child-facing chunks and spoken prompts now come only from a reviewed manifest.
- Made pre-cached reviewed audio the event default. Sarvam Bulbul v3 streaming is the first live fallback; OpenAI speech is the second. IndicF5 is a post-event offline experiment.
- Replaced the unverified 200 to 700 millisecond promise with an instrumented p95 target of 1.5 seconds to first cached audio and a stretch goal below 0.8 seconds.
- Added exact-manifest and confidence gating. Low-confidence OCR now asks for recapture and cannot silently autocorrect into a lesson.
- Added camera-revision verification because current XIAO ESP32S3 Sense units may use OV3660 after OV2640 discontinuation.
- Added a fixed physical camera setup, saved-image replay, visible manual override, timing traces, privacy limits, and cold-restart acceptance tests.

## Package additions

- Updated master specification in DOCX and PDF.
- Updated Claude Code build handoff with interfaces, schemas, tests, build order, cut order, and definition of done.
- Updated event-day runbook with a clock-based plan, demo script, recovery ladder, stop conditions, and packing list.
- Print-ready card pack with 24 full-page A4 Marathi and Hindi cards plus a separate answer key.

## Research basis

- Seeed Studio camera and board documentation.
- PaddleOCR PP-OCRv5 multilingual and Devanagari model documentation.
- Tesseract official Hindi and Marathi trained-data repositories.
- Sarvam Bulbul v3, streaming, and pronunciation-dictionary documentation.
- OpenAI audio API documentation.
- AI4Bharat IndicF5 repository.
- Unicode text segmentation specification.
