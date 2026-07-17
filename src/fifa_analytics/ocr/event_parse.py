"""Classifies a team_events entry as a goal or a card, and pulls the player
name + minute out of the raw OCR text.

The icon-color classification (yellow/red card) is based on EA's standard,
consistent color-coding for cards across the FC/FIFA series — not something
that needs per-screenshot calibration the way crop positions do. It hasn't
been checked against a real card-event screenshot yet though (every sample
seen so far has been a goal), so treat "yellow_card"/"red_card" as
plausible-but-unverified until one comes through.

Multiple events per screenshot (a match with 2+ goals/cards) is still an
open question — see regions.py's TEAM_EVENTS_REGIONS docstring. This module
only handles what's parseable as a single entry from event_band; it doesn't
attempt to detect/split multiple rows.
"""

import re

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only when cv2 isn't installed
    cv2 = None


def parse_event_text(raw_text: str) -> tuple[str | None, int | None]:
    """Splits "B. Fernandes 37" (or "...37'") into (name, minute)."""
    if not raw_text:
        return None, None
    match = re.search(r"(\d+)\s*'?\s*$", raw_text.strip())
    if not match:
        return None, None
    minute = int(match.group(1))
    name = raw_text[: match.start()].strip()
    return (name or None), minute


def classify_event_icon(icon_crop: np.ndarray) -> str:
    """Returns "goal" (achromatic ball icon), "yellow_card", "red_card", or
    "unknown" (anything that doesn't clearly match — including whatever an
    assist-specific icon might look like, if EA uses a distinct one; no
    sample of that has been seen yet either).
    """
    if icon_crop is None or icon_crop.size == 0:
        return "unknown"

    hsv = cv2.cvtColor(icon_crop, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0].astype(float), hsv[:, :, 1].astype(float), hsv[:, :, 2].astype(float)

    # Ignore the dark translucent panel background around the icon itself —
    # only look at pixels bright enough to plausibly be part of the icon.
    mask = val > 60
    if not mask.any():
        return "unknown"

    mean_hue = float(np.mean(hue[mask]))
    mean_sat = float(np.mean(sat[mask]))

    if mean_sat < 40:
        return "goal"
    if 15 <= mean_hue <= 40:
        return "yellow_card"
    if mean_hue <= 10 or mean_hue >= 170:
        return "red_card"
    return "unknown"
