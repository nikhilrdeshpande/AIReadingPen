#!/usr/bin/env python3
"""Benchmark OCR engines on a folder of card images named <lang>_<anything>.jpg or matching manifest ids.

  .venv/bin/python scripts/bench_cards.py fixtures            # both engines on synthetic fixtures
  .venv/bin/python scripts/bench_cards.py debug_frames --engine tesseract
Each image is treated as a full frame if larger than 600 px tall (band crop applied), else as a crop."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import preprocess  # noqa: E402
from app.config import settings  # noqa: E402
from app.gate import evaluate  # noqa: E402
from app.manifest import Manifest  # noqa: E402
from app.ocr import get_engine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--engine", default="both", choices=["both", "paddle", "tesseract"])
    ap.add_argument("--expect", default="", help="optional file with lines '<image name> <word>'")
    a = ap.parse_args()
    m = Manifest(settings.lesson_manifest)
    expect = {}
    if a.expect:
        for line in Path(a.expect).read_text(encoding="utf-8").splitlines():
            if line.strip():
                k, v = line.split(None, 1)
                expect[k] = v.strip()
    engines = ["paddle", "tesseract"] if a.engine == "both" else [a.engine]
    files = sorted(p for p in Path(a.folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    for name in engines:
        eng = get_engine(name)
        eng.warmup()
        ok, n, lat = 0, 0, []
        print(f"\n== {eng.name}")
        for p in files:
            img = cv2.imread(str(p))
            if img is None:
                continue
            lang = "hi" if p.name.startswith("hi") else "mr"
            band = preprocess.band_crop(img, settings.crop_box()) if img.shape[0] > 600 else img
            ocr_in = preprocess.prepare_for_ocr(band)
            if ocr_in is None:
                print(f"  {p.name:28s} no ink found")
                n += 1
                continue
            t0 = time.perf_counter()
            r = eng.recognize(ocr_in, lang)
            lat.append(int((time.perf_counter() - t0) * 1000))
            g = evaluate(r, lang, m, settings.ocr_confidence_threshold)
            exp = expect.get(p.name)
            hit = (g.accepted and (exp is None or g.normalized == exp))
            ok += hit
            n += 1
            print(f"  {p.name:28s} {lat[-1]:5d}ms  {r['raw_text']!r:22s} conf={g.confidence if g.confidence is None else round(g.confidence,3)}  {'OK' if hit else 'MISS (' + g.reason + ')'}")
        if lat:
            lat.sort()
            print(f"  -> {ok}/{n} accepted, median {lat[len(lat)//2]} ms, p95 {lat[int(len(lat)*0.95)-1 if len(lat)>1 else 0]} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
