"""Runs OCR on a preprocessed crop and parses the expected numeric type."""

import re
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def _reader():
    import easyocr

    return easyocr.Reader(["en"], gpu=False)


def read_text(crop: np.ndarray) -> tuple[str, float]:
    """Returns (raw_text, confidence in [0, 1]). Empty crop -> ("", 0.0)."""
    results = _reader().readtext(crop, detail=1, paragraph=False)
    if not results:
        return "", 0.0
    # Multiple text fragments in one crop (shouldn't happen for a
    # single-field region, but be defensive) — join them and average confidence.
    text = " ".join(r[1] for r in results)
    confidence = sum(r[2] for r in results) / len(results)
    return text, confidence


def parse_numeric(raw_text: str) -> float | None:
    """Extracts a single number from OCR text, ignoring surrounding label
    text and a trailing %.

    A comma is treated as a decimal point, not a thousands separator: no
    value on these stat screens is ever >=1,000 (the largest are pass counts
    around ~120), while OCR misreading "6.0" as "6,0" is plausible — and
    stripping the comma there would silently turn a 6.0 rating into 60.
    """
    cleaned = raw_text.replace(",", ".").strip()
    match = re.search(r"-?\d+\.?\d*", cleaned)
    return float(match.group()) if match else None


def read_field(crop: np.ndarray) -> tuple[float | None, float]:
    """End-to-end: OCR a crop, parse it as a number, return (value, confidence)."""
    raw_text, confidence = read_text(crop)
    return parse_numeric(raw_text), confidence
