"""Tesseract LSTM adapter (mar / hin trained data from tessdata_best in models/tessdata)."""
from __future__ import annotations

import os
import time
from typing import Literal

import cv2
import numpy as np
import pytesseract

from ..config import settings
from . import OCRCandidate, OCRResult


class TesseractEngine:
    name = "tesseract-lstm-best"

    def __init__(self) -> None:
        os.environ["TESSDATA_PREFIX"] = settings.tessdata_dir
        self._cfg = "--psm 7 --oem 1"

    def warmup(self) -> None:
        img = np.full((80, 300), 255, dtype=np.uint8)
        pytesseract.image_to_string(img, lang="mar", config=self._cfg)

    def recognize(self, image: np.ndarray, language: Literal["mr", "hi"]) -> OCRResult:
        lang = "mar" if language == "mr" else "hin"
        t0 = time.perf_counter()
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        data = pytesseract.image_to_data(gray, lang=lang, config=self._cfg, output_type=pytesseract.Output.DICT)
        words, confs = [], []
        for txt, conf in zip(data["text"], data["conf"]):
            txt = (txt or "").strip()
            if not txt:
                continue
            try:
                c = float(conf)
            except (TypeError, ValueError):
                c = -1.0
            words.append(txt)
            confs.append(c)
        raw = " ".join(words)
        candidates: list[OCRCandidate] = []
        if words:
            # One card = one word; treat the whole line as the candidate with the minimum word confidence.
            valid = [c for c in confs if c >= 0]
            conf = (min(valid) / 100.0) if valid else None
            candidates.append({"text": raw, "confidence": conf})
        return {"engine": self.name, "raw_text": raw, "candidates": candidates,
                "latency_ms": int((time.perf_counter() - t0) * 1000)}
