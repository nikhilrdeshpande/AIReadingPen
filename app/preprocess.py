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


def _binary(band: np.ndarray) -> np.ndarray:
    g = band if band.ndim == 2 else cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def text_box(band: np.ndarray, min_area: int = 30) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of the text-like ink only. Drops background regions (large blobs that
    touch two or more band edges, e.g. the desk or a shadow), thin lines (card border) and specks."""
    binary = _binary(band)
    H, W = binary.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    boxes = []
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if area < min_area:
            continue
        touches = (x == 0) + (y == 0) + (x + w >= W) + (y + h >= H)
        if touches >= 2 and area > 0.08 * H * W:
            continue                                   # background: desk, shadow, page edge
        if (h <= 4 and w > 6 * h) or (w <= 4 and h > 6 * w):
            continue                                   # thin line: card border, fold
        if h >= 0.98 * H and w >= 0.98 * W:
            continue
        boxes.append((x, y, x + w, y + h))
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def tight_crop(band: np.ndarray, pad_frac: float = 0.25, min_ink_px: int = 30) -> np.ndarray | None:
    """Crop to the text box with padding. None if there is no text-like ink."""
    box = text_box(band)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    H, W = band.shape[:2]
    ph = int((y1 - y0) * pad_frac) + 4
    pw = ph
    return band[max(0, y0 - ph):min(H, y1 + ph), max(0, x0 - pw):min(W, x1 + pw)]


def ink_touches_edges(band: np.ndarray, margin: int = 2) -> dict:
    """Which band edges the text box touches: top/bottom means the word is cut off by the band,
    left/right means it is not fully inside. Background blobs and lines are ignored (see text_box)."""
    H, W = band.shape[:2]
    box = text_box(band)
    if box is None:
        return {"top": False, "bottom": False, "left": False, "right": False}
    x0, y0, x1, y1 = box
    return {"top": y0 <= margin, "bottom": y1 >= H - margin, "left": x0 <= margin, "right": x1 >= W - margin}


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


