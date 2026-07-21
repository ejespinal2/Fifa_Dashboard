"""Runs OCR on a preprocessed crop and parses the expected numeric type."""

import os
import re
from functools import lru_cache

import numpy as np


def _gpu_available() -> bool:
    """GPU use is opt-in (FIFA_OCR_GPU=1), not auto-detected. A real 40-image
    run got measurably SLOWER after this used to auto-detect and use any
    CUDA GPU it found: every call here is one small, independent crop (a
    single stat field, a single name) OCR'd one at a time, never batched --
    a GPU's fixed per-call transfer/kernel-launch overhead dominates at
    that size and loses to plain CPU, it doesn't win. Set FIFA_OCR_GPU=1 to
    try GPU anyway; anything else (unset, "0", etc.) stays on CPU."""
    return os.environ.get("FIFA_OCR_GPU") == "1"


@lru_cache(maxsize=1)
def _reader():
    import easyocr

    use_gpu = _gpu_available()
    print(f"EasyOCR: using {'GPU' if use_gpu else 'CPU'} "
          f"({'FIFA_OCR_GPU=1 set' if use_gpu else 'set FIFA_OCR_GPU=1 to try GPU instead'})")
    return easyocr.Reader(["en"], gpu=use_gpu)


# EasyOCR's recognizer scores every character against its FULL trained
# alphabet (letters, digits, punctuation) unless told otherwise, so a digit
# crop is competing against visually similar letters too -- 0/O, 1/I/l,
# 8/B, 5/S. Passing an allowlist for a field known to be numeric removes
# those letters from consideration entirely, which is a real accuracy
# fix (fewer candidates to confuse a digit with), not a pixel-threshold
# guess: EasyOCR's own docs recommend this for exactly this kind of
# constrained field. Kept narrow -- read_text's other callers (names,
# event rows mixing names with minutes) must NOT get this, so it's opt-in
# per call, not a global default.
NUMERIC_ALLOWLIST = "0123456789.,%-"


def read_text(crop: np.ndarray, allowlist: str | None = None) -> tuple[str, float]:
    """Returns (raw_text, confidence in [0, 1]). Empty crop -> ("", 0.0).
    Pass allowlist (e.g. NUMERIC_ALLOWLIST) for a crop known to hold only
    those characters -- see the constant's docstring for why this helps."""
    kwargs = {"allowlist": allowlist} if allowlist else {}
    results = _reader().readtext(crop, detail=1, paragraph=False, **kwargs)
    if not results:
        return "", 0.0
    # Multiple text fragments in one crop (shouldn't happen for a
    # single-field region, but be defensive) — join them and average confidence.
    text = " ".join(r[1] for r in results)
    confidence = sum(r[2] for r in results) / len(results)
    return text, confidence


def group_fragments_into_lines(fragments: list[dict]) -> list[dict]:
    """Groups OCR fragments ({text, confidence, y_top, y_bottom, x_left})
    into visual lines: a fragment joins the current line when its vertical
    center falls inside the line's band, else it starts a new one. Within a
    line, fragments read left-to-right. Pure — testable without EasyOCR."""
    ordered = sorted(fragments, key=lambda f: (f["y_top"] + f["y_bottom"]) / 2)
    lines: list[dict] = []
    for fragment in ordered:
        center = (fragment["y_top"] + fragment["y_bottom"]) / 2
        target = None
        for line in lines:
            if line["y_top"] <= center <= line["y_bottom"]:
                target = line
                break
        if target is None:
            target = {"fragments": [], "y_top": fragment["y_top"], "y_bottom": fragment["y_bottom"]}
            lines.append(target)
        target["fragments"].append(fragment)
        target["y_top"] = min(target["y_top"], fragment["y_top"])
        target["y_bottom"] = max(target["y_bottom"], fragment["y_bottom"])

    out = []
    for line in sorted(lines, key=lambda l: l["y_top"]):
        parts = sorted(line["fragments"], key=lambda f: f["x_left"])
        out.append(
            {
                "text": " ".join(p["text"] for p in parts),
                "confidence": sum(p["confidence"] for p in parts) / len(parts),
                "y_top": line["y_top"],
                "y_bottom": line["y_bottom"],
            }
        )
    return out


def read_fragments(crop: np.ndarray) -> list[dict]:
    """OCR a crop and return every raw text fragment with its position,
    all coordinates as fractions of the crop's size:
    [{text, confidence, x_left, x_right, y_top, y_bottom}]. This is the
    input for layout-aware parsing (e.g. the Events tab's center-spine
    rows, where WHERE a fragment sits decides which team it belongs to)."""
    if crop is None or crop.size == 0:
        return []
    results = _reader().readtext(crop, detail=1, paragraph=False)
    height, width = crop.shape[0], crop.shape[1]
    return [
        {
            "text": text,
            "confidence": confidence,
            "x_left": min(point[0] for point in box) / width,
            "x_right": max(point[0] for point in box) / width,
            "y_top": min(point[1] for point in box) / height,
            "y_bottom": max(point[1] for point in box) / height,
        }
        for box, text, confidence in results
    ]


def read_lines(crop: np.ndarray) -> list[dict]:
    """OCR a crop and return its visual lines top-to-bottom:
    [{text, confidence, y_top, y_bottom}] with y as fractions of the crop
    height — so callers can map a line back to a vertical slice of the
    source image (e.g. to find the icon that belongs to an event row)."""
    return group_fragments_into_lines(read_fragments(crop))


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
    """End-to-end: OCR a crop, parse it as a number, return (value, confidence).
    Every caller of this is a stat/rating value crop, never a name -- so it
    always OCRs with NUMERIC_ALLOWLIST, cutting out the letter/digit
    confusions (0/O, 1/I, 8/B, 5/S) a full-alphabet read is prone to."""
    raw_text, confidence = read_text(crop, allowlist=NUMERIC_ALLOWLIST)
    return parse_numeric(raw_text), confidence
