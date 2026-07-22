"""Crop + clean up a region before handing it to OCR."""

import cv2
import numpy as np


def crop_fractional(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]


def clean_for_ocr(crop: np.ndarray) -> np.ndarray:
    """Grayscale -> contrast stretch -> adaptive threshold.

    EA FC's stat panels are light text on a dark translucent panel, which
    tends to binarize cleanly after a contrast stretch — verify this holds
    against your real screenshots and tune the threshold block size/offset
    if numbers are coming out broken or fused.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    stretched = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    # Upscale small crops — OCR accuracy on thin small-caps digits improves
    # noticeably above ~30px character height.
    scale = max(1, 300 // max(stretched.shape[0], 1))
    if scale > 1:
        stretched = cv2.resize(stretched, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    thresh = cv2.adaptiveThreshold(
        stretched, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, -5
    )
    return thresh


def contrast_grayscale(crop: np.ndarray) -> np.ndarray:
    """High-contrast GRAYSCALE (no hard black/white threshold) for OCR.

    The adaptive threshold in clean_for_ocr binarizes cleanly for most
    fields, but on very small digits (a ~20px stat value from a 1080p
    console share) it can fracture a thin stroke -- a lone '7' loses its
    diagonal and drops out entirely. EasyOCR is trained on natural images
    and reads a contrast-enhanced grayscale better than a broken binary, so
    the player-summary value column uses this instead: global contrast
    stretch plus CLAHE for local contrast, kept as grayscale."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    stretched = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(stretched)
