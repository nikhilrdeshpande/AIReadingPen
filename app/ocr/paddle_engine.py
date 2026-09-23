"""PaddleOCR PP-OCRv5 Devanagari mobile recogniser, recognition-only.
The target band is a single printed word, so we skip text detection (1.2 s with the server detector)
and feed the tight ink crop straight to the recogniser (~50 ms)."""
from __future__ import annotations

import logging
import os
import time
from typing import Literal

import numpy as np

from . import OCRCandidate, OCRResult

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
logging.getLogger("paddlex").setLevel(logging.ERROR)


class PaddleEngine:
    name = "paddle-devanagari-v5-mobile-rec"

    def __init__(self) -> None:
        from paddleocr import TextRecognition
        self._rec = TextRecognition(model_name="devanagari_PP-OCRv5_mobile_rec")

    def warmup(self) -> None:
        img = np.full((96, 320, 3), 255, dtype=np.uint8)
        list(self._rec.predict(img))

    def recognize(self, image: np.ndarray, language: Literal["mr", "hi"]) -> OCRResult:
        t0 = time.perf_counter()
        if image.ndim == 2:
            image = np.stack([image] * 3, axis=-1)
        texts, scores = [], []
        for r in self._rec.predict(image):
            texts.append(r.get("rec_text", "") or "")
            scores.append(float(r.get("rec_score", 0.0)))
        raw = " ".join(t for t in texts if t)
        candidates: list[OCRCandidate] = []
        if raw:
            candidates.append({"text": raw, "confidence": min(scores) if scores else None})
        return {"engine": self.name, "raw_text": raw, "candidates": candidates,
                "latency_ms": int((time.perf_counter() - t0) * 1000)}
