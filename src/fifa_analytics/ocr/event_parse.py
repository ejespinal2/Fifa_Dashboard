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


# The icon must occupy at least this fraction of the crop's pixels for a
# classification to be trusted — anything smaller is likely just background
# noise (stadium lights, kit colors) bleeding through the dark overlay.
MIN_ICON_PIXEL_FRACTION = 0.03


def classify_event_icon(icon_crop: np.ndarray) -> str:
    """Returns "goal" (achromatic white ball icon), "yellow_card",
    "red_card", or "unknown".

    Counts pixels per color class rather than averaging color over all
    bright pixels: a real run showed the mean-based approach getting washed
    out to "unknown" by background pixels (crowd, kits) bleeding through
    the translucent overlay, even though the white ball icon was plainly
    in-crop. Class thresholds are strict enough (bright + saturated for
    cards, bright + unsaturated for the ball) that dimmed background rarely
    qualifies for any class at all.
    """
    if icon_crop is None or icon_crop.size == 0:
        return "unknown"

    hsv = cv2.cvtColor(icon_crop, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(int)
    sat = hsv[:, :, 1].astype(int)
    val = hsv[:, :, 2].astype(int)

    white = (sat < 60) & (val > 150)
    yellow = (hue >= 15) & (hue <= 40) & (sat > 100) & (val > 100)
    red = ((hue <= 10) | (hue >= 170)) & (sat > 100) & (val > 100)

    counts = {
        "goal": int(np.count_nonzero(white)),
        "yellow_card": int(np.count_nonzero(yellow)),
        "red_card": int(np.count_nonzero(red)),
    }
    best = max(counts, key=counts.get)
    total_pixels = icon_crop.shape[0] * icon_crop.shape[1]
    if counts[best] < MIN_ICON_PIXEL_FRACTION * total_pixels:
        return "unknown"
    return best
