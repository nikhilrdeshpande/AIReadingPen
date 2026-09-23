"""Target-band cropping, frame quality metrics and tight word cropping. Pure numpy/OpenCV, no model."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def band_crop(img: np.ndarray, crop: dict) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0 = int(crop["x"] * w), int(crop["y"] * h)
    x1, y1 = int((crop["x"] + crop["w"]) * w), int((crop["y"] + crop["h"]) * h)
    return img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]


@dataclass
class BandMetrics:
    occupancy: float   # fraction of "ink" pixels after Otsu
    sharpness: float   # variance of Laplacian
    exposure: float    # mean gray
    motion: float      # mean abs diff vs previous sample (0 when no previous)

    def to_dict(self) -> dict:
        return {"occupancy": round(self.occupancy, 4), "sharpness": round(self.sharpness, 1),
                "exposure": round(self.exposure, 1), "motion": round(self.motion, 2)}


def analysis_gray(band: np.ndarray, width: int = 320) -> np.ndarray:
    """Downscale the band for cheap, resolution-independent metrics."""
    g = band if band.ndim == 2 else cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    h, w = g.shape[:2]
    if w > width:
        g = cv2.resize(g, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
    return g


def metrics(gray: np.ndarray, prev_gray: np.ndarray | None) -> BandMetrics:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    exposure = float(gray.mean())
    # ink = pixels clearly darker than the local paper level
    thr, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = (blur < min(thr, exposure - 25)).mean() if exposure > 40 else (blur < thr).mean()
    sharp = float(cv2.Laplacian(blur, cv2.CV_64F).var())
    motion = 0.0
    if prev_gray is not None and prev_gray.shape == gray.shape:
        motion = float(np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16)).mean())
    return BandMetrics(float(ink), sharp, exposure, motion)


def tight_crop(band: np.ndarray, pad_frac: float = 0.25, min_ink_px: int = 30) -> np.ndarray | None:
    """Crop to the bounding box of the ink in the band, with padding. None if there is no ink."""
    g = band if band.ndim == 2 else cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    thr, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # remove specks so a dust mark cannot define the box
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ys, xs = np.where(binary > 0)
    if len(ys) < min_ink_px:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    ph = int((y1 - y0 + 1) * pad_frac) + 4
    pw = int((y1 - y0 + 1) * pad_frac) + 4
    H, W = g.shape[:2]
    return band[max(0, y0 - ph):min(H, y1 + ph + 1), max(0, x0 - pw):min(W, x1 + pw + 1)]


def prepare_for_ocr(band: np.ndarray, target_height: int = 96) -> np.ndarray | None:
    """Tight crop + scale so the word is ~target_height px tall + mild contrast stretch. Returns BGR."""
    crop = tight_crop(band)
    if crop is None:
        return None
    h, w = crop.shape[:2]
    if h < target_height * 0.6 or h > target_height * 2.5:
        s = target_height / h
        crop = cv2.resize(crop, (max(8, int(w * s)), target_height),
                          interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    lo, hi = np.percentile(g, 2), np.percentile(g, 98)
    if hi - lo > 20:
        g = np.clip((g.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)



def ink_touches_edges(band: np.ndarray, margin: int = 2) -> dict:
    """Which band edges the ink touches. A word touching top/bottom is cut off by the band; left/right means
    it is not fully inside. Used to refuse captures of partial words (the source of fragments like आड for झाड)."""
    g = band if band.ndim == 2 else cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    H, W = binary.shape
    return {"top": bool(binary[:margin + 1].any()), "bottom": bool(binary[H - margin - 1:].any()),
            "left": bool(binary[:, :margin + 1].any()), "right": bool(binary[:, W - margin - 1:].any())}
