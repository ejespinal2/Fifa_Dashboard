"""Parses the Events tab's center-spine layout and classifies event icons.

Layout (calibrated against real multi-event screenshots of a full match —
Atlético 0:2 Man Utd, three scrolled captures):

- A vertical spine runs down the screen center with a minute circle per
  event row ("41'", "65'", also "HT" markers and scroll arrows — skipped).
- The HOME team's events extend LEFT of the spine, the AWAY team's RIGHT —
  matching the header's "HOME score : score AWAY" order. The side a name
  sits on is therefore the team attribution.
- A goal/card/missed-penalty icon sits between the spine and the scoring
  player's face photo, in a narrow x-band per side.
- Substitutions are the one case with NO icon in that zone at all — the
  "icon" is a pair of tiny green up / red down chevrons printed directly
  beside the NAMES themselves (too small and mis-positioned for the
  goal/card icon zone to ever catch), and the outgoing player is a
  separate, smaller line hanging just below the row. Because of that,
  substitutions are detected STRUCTURALLY, not by icon color: any row
  with a name-only line hanging below it IS a substitution, full stop —
  parsed as sub_on (the row's primary name) + sub_off (the hanging name).
  This is what parse_event_rows' sub_off_name field means; the pipeline
  checks it BEFORE ever looking at icon color for a row.

Card icon colors are EA's standard color-coding; a real red/yellow card
screenshot hasn't been through yet, so treat those as plausible-but-
unverified. Goal vs missed penalty: both are a similarly-sized white ball
icon (an earlier "wider blob" shape assumption was wrong — the missed-
penalty mark does not extend the icon's width), discriminated instead by
how much dark/black pixel area sits inside the same-sized ball crop (the
X/check mark adds noticeably more dark area than a plain ball's stitching).
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
# row. Gated on an ABSOLUTE fraction of the band's height, not a multiple of
# the row's own OCR'd text bbox height: text-only bounding boxes are small
# and inconsistent (a lone minute+name line reads much shorter than the
# full photo-height row it visually belongs to), so a relative multiplier
# undershot the real gap on real screenshots and silently dropped every
# outgoing name. The row spacing between DIFFERENT events is comfortably
# larger than this on every real capture seen so far.
SUB_OFF_MAX_GAP_FRACTION = 0.08


def parse_minute(text: str) -> int | None:
    """"65'" -> 65, "45+2'" -> 45 (base minute), "HT"/names -> None."""
    match = MINUTE_RE.match(text.strip())
    return int(match.group(1)) if match else None


def _name_of(fragments: list[dict]) -> str | None:
    text = " ".join(f["text"] for f in sorted(fragments, key=lambda f: f["x_left"])).strip()
    return text or None


def _find_minute_fragment(line: list[dict]) -> dict | None:
    """Finds the row's minute token. Usually a single fragment ("41'"), but
    a real run showed EasyOCR occasionally splitting a two-digit minute
    into separate detections ("90'" read as "9" + "0'"), silently
    truncating the minute to 9 — so this first tries concatenating ALL
    spine-zone fragments in x-order into one string and parsing that;
    only falls back to a lone fragment match if concatenation doesn't
    parse as a minute (e.g. a short name happens to straddle the zone)."""
    spine_fragments = sorted(
        (f for f in line if SPINE_ZONE[0] <= (f["x_left"] + f["x_right"]) / 2 <= SPINE_ZONE[1]),
        key=lambda f: f["x_left"],
    )
    if spine_fragments:
        merged_text = "".join(f["text"] for f in spine_fragments)
        if parse_minute(merged_text) is not None:
            return {
                "text": merged_text,
                "x_left": min(f["x_left"] for f in spine_fragments),
                "x_right": max(f["x_right"] for f in spine_fragments),
            }
    return next((f for f in spine_fragments if parse_minute(f["text"]) is not None), None)


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
        minute_fragment = _find_minute_fragment(line)

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
                close_below = 0 <= y_top - event["y_bottom"] <= SUB_OFF_MAX_GAP_FRACTION
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

# Within a ball-colored (white-dominant) icon crop, this much of the crop
# being dark/black means it's not a plain ball — a missed-penalty or
# converted-penalty mark adds noticeably more dark ink than a ball's own
# stitching detail does. A first cut, not yet confirmed against a real
# converted-penalty capture (see module docstring) — recalibrate against
# real Match Facts output if goal vs missed_penalty comes out wrong.
PENALTY_MARK_DARK_FRACTION = 0.12


def classify_event_icon(icon_crop: np.ndarray) -> str:
    """Returns "goal", "missed_penalty", "penalty_goal" (converted penalty),
    "yellow_card", "red_card", or "unknown". Never returns "substitution" —
    subs are detected structurally (see module docstring), never by icon
    color, so this function is only ever called for non-sub rows.

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
    dark = val < 80

    total_pixels = icon_crop.shape[0] * icon_crop.shape[1]
    threshold = MIN_ICON_PIXEL_FRACTION * total_pixels

    counts = {
        "goal": int(np.count_nonzero(white)),
        "yellow_card": int(np.count_nonzero(yellow)),
        "red_card": int(np.count_nonzero(red)),
    }
    best = max(counts, key=counts.get)
    if counts[best] < threshold:
        return "unknown"
    if best != "goal":
        return best

    # A ball-shaped icon: goal, missed penalty, or converted penalty all
    # look alike in color (white-dominant), same size — only the amount of
    # dark ink inside the ball itself tells them apart. Restricted to an
    # ellipse inscribed in the white blob's bounding box, not the raw box:
    # a circle's bounding SQUARE always has non-circular corners outside
    # the disk, which are background-dark by construction (nothing to do
    # with any drawn mark) — counting those inflated every ball's "dark
    # fraction" regardless of whether it actually carried a mark.
    ys, xs = np.nonzero(white)
    box_dark = dark[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    height, width = box_dark.shape
    row_grid, col_grid = np.ogrid[:height, :width]
    center_y, center_x = (height - 1) / 2, (width - 1) / 2
    ellipse = ((row_grid - center_y) / max(center_y, 1)) ** 2 + ((col_grid - center_x) / max(center_x, 1)) ** 2 <= 1.0
    marked = box_dark & ellipse
    ellipse_area = np.count_nonzero(ellipse)
    dark_fraction = float(np.count_nonzero(marked)) / ellipse_area if ellipse_area else 0.0
    if dark_fraction < PENALTY_MARK_DARK_FRACTION:
        return "goal"
    return _classify_penalty_mark(marked)


def _classify_penalty_mark(marked: np.ndarray) -> str:
    """Distinguishes the ✗ (missed) from the ✓ (converted) mark within a
    ball icon already known to carry extra dark ink (marked: dark pixels,
    already restricted to the ball's disk — see classify_event_icon): an
    ✗ is symmetric (both top corners of its own dark region roughly
    equally dark), a ✓'s long stroke rises through the top-right while its
    top-left stays comparatively clear. Ambiguous cases default to
    missed_penalty — the variant confirmed against a real screenshot;
    unverified until a real converted-penalty capture goes through (see
    module docstring)."""
    ys, xs = np.nonzero(marked)
    if len(xs) == 0:
        return "missed_penalty"
    mark = marked[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    mid_y, mid_x = mark.shape[0] // 2, mark.shape[1] // 2
    top_left = int(np.count_nonzero(mark[:mid_y, :mid_x]))
    top_right = int(np.count_nonzero(mark[:mid_y, mid_x:]))
    if top_right > 0 and top_left <= 0.4 * top_right:
        return "penalty_goal"
    return "missed_penalty"
