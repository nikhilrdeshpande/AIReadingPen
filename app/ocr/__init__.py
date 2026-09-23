"""OCR adapter interface. Engines are preloaded at app start; never download after the audience arrives."""
from __future__ import annotations

import time
from typing import Literal, Protocol, TypedDict

import numpy as np


class OCRCandidate(TypedDict):
    text: str
    confidence: float | None


class OCRResult(TypedDict):
    engine: str
    raw_text: str
    candidates: list[OCRCandidate]
    latency_ms: int


class OCREngine(Protocol):
    name: str
    def warmup(self) -> None: ...
    def recognize(self, image: np.ndarray, language: Literal["mr", "hi"]) -> OCRResult: ...


_ENGINES: dict[str, OCREngine] = {}


def get_engine(name: str) -> OCREngine:
    if name in _ENGINES:
        return _ENGINES[name]
    if name == "paddle":
        from .paddle_engine import PaddleEngine
        eng: OCREngine = PaddleEngine()
    elif name == "tesseract":
        from .tesseract_engine import TesseractEngine
        eng = TesseractEngine()
    else:
        raise ValueError(f"unknown OCR engine {name!r}")
    _ENGINES[name] = eng
    return eng


def timed(fn):
    """Run fn() and return (result, elapsed_ms)."""
    t0 = time.perf_counter()
    r = fn()
    return r, int((time.perf_counter() - t0) * 1000)
