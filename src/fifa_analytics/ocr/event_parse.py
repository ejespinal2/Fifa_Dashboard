"""Parses the Events tab's center-spine layout and classifies event icons.

Layout (calibrated against real multi-event screenshots of a full match —
Atlético 0:2 Man Utd, three scrolled captures):

- A vertical spine runs down the screen center with a minute circle per
  event row ("41'", "65'", also "HT" markers and scroll arrows — skipped).
- The HOME team's events extend LEFT of the spine, the AWAY team's RIGHT —
  matching the header's "HOME score : score AWAY" order. The side a name
  sits on is therefore the team attribution.
- The event icon (ball / ball-with-X / card / sub arrow) sits between the
  spine and the player's face photo, in a narrow x-band per side.
- Substitutions carry TWO names: the player coming on next to the minute
  (green up-arrow) and the player going off on a separate line just below
  (red down-arrow) — parsed as sub_on and sub_off events.

Card icon colors are EA's standard color-coding; a real red/yellow card
screenshot hasn't been through yet, so treat those as plausible-but-
unverified. Goal vs missed penalty is shape-discriminated (both icons are
white): the missed-penalty ball has an X beside/through it, making the
white blob visibly wider than tall.
"""

import re

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only when cv2 isn't installed
    cv2 = None


def parse_event_text(raw_text: str) -> tuple[str | None, int | None]:
    """Splits "B. Fernandes 37" (or "...37'") into (name, minute) —
    trailing-minute form, kept for screen classification probes."""
    if not raw_text:
        return None, None
    match = re.search(r"(\d+)\s*'?\s*$", raw_text.strip())
    if not match:
        return None, None
    minute = int(match.group(1))
    name = raw_text[: match.start()].strip()
    return (name or None), minute


MINUTE_RE = re.compile(r"^(\d{1,3})(?:\s*\+\s*(\d{1,2}))?\s*['\"`]?$")

# The minute circle sits on the spine at the band's horizontal center; a
# minute-looking token further out than this is something else (a shirt
# number in a name fragment, a stat). Fraction of the band's width.
SPINE_ZONE = (0.40, 0.60)

# A sub's outgoing player is printed on its own line just below the event
# row; anything further below than this many row-heights is a different row.
SUB_OFF_MAX_GAP_ROWS = 1.6


def parse_minute(text: str) -> int | None:
    """"65'" -> 65, "45+2'" -> 45 (base minute), "HT"/names -> None."""
    match = MINUTE_RE.match(text.strip())
    return int(match.group(1)) if match else None


def _name_of(fragments: list[dict]) -> str | None:
    text = " ".join(f["text"] for f in sorted(fragments, key=lambda f: f["x_left"])).strip()
    return text or None


def parse_event_rows(fragments: list[dict]) -> list[dict]:
    """Splits the event band's OCR fragments into event rows:
    [{minute, side ('home'|'away'), name, sub_off_name, y_top, y_bottom}].

    A row = a visual line containing a minute token near the spine. Names
    left of the minute belong to the home side, right of it to the away
    side — a line can carry one event per side (double substitutions land
    that way). A name-only line hanging just below an event row on the
    same side is that row's outgoing sub player.
    """
    # Same banding rule as extract.group_fragments_into_lines, but keeping
    # the fragments themselves (their x positions decide event sides).
    lines: list[list[dict]] = []
    ordered = sorted(fragments, key=lambda f: (f["y_top"] + f["y_bottom"]) / 2)
    for fragment in ordered:
        center = (fragment["y_top"] + fragment["y_bottom"]) / 2
        for line in lines:
            if min(f["y_top"] for f in line) <= center <= max(f["y_bottom"] for f in line):
                line.append(fragment)
                break
        else:
            lines.append([fragment])

    rows: list[dict] = []
    pending_sub_offs: list[dict] = []  # events awaiting an off-player line
    for line in sorted(lines, key=lambda l: min(f["y_top"] for f in l)):
        y_top = min(f["y_top"] for f in line)
        y_bottom = max(f["y_bottom"] for f in line)
        minute_fragment = next(
            (
                f for f in line
                if parse_minute(f["text"]) is not None
                and SPINE_ZONE[0] <= (f["x_left"] + f["x_right"]) / 2 <= SPINE_ZONE[1]
            ),
            None,
        )

        if minute_fragment is None:
            # maybe an outgoing-sub name line for the row just above — a
            # double-sub line carries BOTH teams' outgoing names, so every
            # pending event takes the fragments on its own side
            for event in pending_sub_offs:
                if event["sub_off_name"] is not None:
                    continue
                same_side = [
                    f for f in line
                    if (f["x_right"] <= minute_x(event) if event["side"] == "home" else f["x_left"] >= minute_x(event))
                ]
                row_height = event["y_bottom"] - event["y_top"] or 0.03
                close_below = 0 <= y_top - event["y_bottom"] <= SUB_OFF_MAX_GAP_ROWS * row_height
                if same_side and close_below:
                    event["sub_off_name"] = _name_of(same_side)
            continue

        minute = parse_minute(minute_fragment["text"])
        left = [f for f in line if f["x_right"] <= minute_fragment["x_left"]]
        right = [f for f in line if f["x_left"] >= minute_fragment["x_right"]]
        new_events = []
        for side, side_fragments in (("home", left), ("away", right)):
            name = _name_of(side_fragments)
            if name is None:
                continue
            event = {
                "minute": minute,
                "side": side,
                "name": name,
                "sub_off_name": None,
                "y_top": y_top,
                "y_bottom": y_bottom,
                "_minute_x": ((minute_fragment["x_left"] + minute_fragment["x_right"]) / 2),
            }
            rows.append(event)
            new_events.append(event)
        pending_sub_offs = new_events  # only the immediately-previous row adopts an off-line
    for event in rows:
        event.pop("_minute_x", None)
    return rows


def minute_x(event: dict) -> float:
    return event.get("_minute_x", 0.5)


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
    green = (hue >= 40) & (hue <= 85) & (sat > 100) & (val > 100)

    total_pixels = icon_crop.shape[0] * icon_crop.shape[1]
    threshold = MIN_ICON_PIXEL_FRACTION * total_pixels
    # Substitutions are checked first: EA's sub icon is a green+red arrow
    # pair, so its red pixels would otherwise win as "red_card". Any real
    # amount of icon-green means sub — no card or ball icon contains green.
    if int(np.count_nonzero(green)) >= threshold:
        return "substitution"

    white_count = int(np.count_nonzero(white))
    red_count = int(np.count_nonzero(red))
    # Missed penalty, colored-X variant: real white (the ball) AND a
    # smaller-but-real amount of red (an X stroke, thinner than a solid
    # card, hence the halved bar).
    if white_count >= threshold and red_count >= 0.5 * threshold:
        return "missed_penalty"

    counts = {
        "goal": white_count,
        "yellow_card": int(np.count_nonzero(yellow)),
        "red_card": red_count,
    }
    best = max(counts, key=counts.get)
    if counts[best] < threshold:
        return "unknown"
    if best == "goal" and _white_blob_is_wide(white):
        # Missed penalty, white-X variant (the real screenshots' form): the
        # X glyph sits beside the ball, so the white blob is clearly wider
        # than tall — a plain goal ball is round.
        return "missed_penalty"
    return best


# A goal ball's white bounding box is ~square; the ball+X missed-penalty
# glyph measures ~1.5-2x wider. Split the difference.
MISSED_PEN_MIN_ASPECT = 1.3


def _white_blob_is_wide(white_mask: np.ndarray) -> bool:
    ys, xs = np.nonzero(white_mask)
    if len(xs) == 0:
        return False
    width = xs.max() - xs.min() + 1
    height = ys.max() - ys.min() + 1
    return height > 0 and (width / height) >= MISSED_PEN_MIN_ASPECT
