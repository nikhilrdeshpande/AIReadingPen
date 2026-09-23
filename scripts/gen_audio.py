#!/usr/bin/env python3
"""Generate cached lesson audio from the manifest speech scripts.

  .venv/bin/python scripts/gen_audio.py --provider openai            # all lessons
  .venv/bin/python scripts/gen_audio.py --provider openai --only approved_for_demo
  .venv/bin/python scripts/gen_audio.py --provider macos             # offline placeholder (Hindi voice Lekha)

Writes content/audio/<lesson id>.wav (the manifest's audio_asset) and a checksum list."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.audio import synthesize  # noqa: E402
from app.config import settings  # noqa: E402
from app.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="openai", choices=["openai", "sarvam", "macos"])
    ap.add_argument("--only", default="", help="review_status filter, e.g. approved_for_demo")
    ap.add_argument("--voice", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    m = Manifest(settings.lesson_manifest)
    cache = Path(settings.audio_cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    sums = []
    for lesson in m.lessons:
        if a.only and lesson.review_status != a.only:
            continue
        target = Path(settings.lesson_manifest).parent / lesson.audio_asset
        if target.exists() and not a.force:
            print(f"keep   {target.name}")
        else:
            src = synthesize(a.provider, lesson.language, lesson.speech_script, cache, voice=a.voice)
            if src is None:
                print(f"SKIP   {lesson.id}: provider {a.provider} unavailable (missing key?)")
                continue
            shutil.copyfile(src, target)
            print(f"wrote  {target.name}  <- {a.provider}: {lesson.speech_script}")
        sums.append(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}")
    (cache / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"checksums -> {cache / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
