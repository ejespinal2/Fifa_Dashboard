"""Content-based screenshot classification, so files dropped into a match
folder don't have to follow the reserved naming convention — the pipeline
looks at each unrecognized image and decides what screen it is.

Built to be CHEAP: classification runs before any real processing, on
every unrenamed file, so it must not cost a full-screenshot OCR pass (in a
real 40-image run that was most of the wall-clock). Probes, cheapest
first:

1. Header strip (small crop, DOWNSCALED — "PLAYER PERFORMANCE" vs.
   "TEAM 1 : 0 TEAM" is large text, robust to downscaling): only the
   Player Performance screen says "PLAYER" up there.
2. Player screens: a tiny probe where the Goalkeeping tab prints
   "Goalkeeper Rating: X.X" splits player_gk from player_summary — no
   body OCR at all.
3. Team screens only (a handful per match — this is NOT where the
   40-image cost was): the body band at FULL resolution. Do not downscale
   this one: the Events tab's minute is small text in a small circle
   already, and shrinking it further can push it below EasyOCR's
   detection floor, silently losing every event row to "unsupported"
   (confirmed against a real run). Stat labels mean the Summary tab,
   minute-spine rows mean the Events tab, the Threat timeline means the
   unsupported Possession tab.

decide_player_screen/decide_team_screen are pure so the logic is testable
without OCR. Reserved filenames always win over auto-classification (see
pipeline.run_match_dir) — if a screen ever misclassifies, naming the file
is the override.
"""

try:
    import cv2
except ImportError:  # pragma: no cover - exercised only when cv2 isn't installed
    cv2 = None

from fifa_analytics.ocr.event_parse import parse_minute
from fifa_analytics.ocr.extract import read_lines, read_text
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional

HEADER_STRIP = (0.0, 0.0, 0.6, 0.16)   # catches "PLAYER PERFORMANCE" on player screens
GK_PROBE = (0.36, 0.20, 0.56, 0.26)    # where the Goalkeeping tab prints "Goalkeeper Rating: X.X"
BODY_BAND = (0.03, 0.16, 0.97, 0.95)   # everything under the tab bar on team screens

# Classification only needs to spot words, not read digits precisely —
# downscaling the body band ~halves OCR time again on 1080p+ screenshots.
MAX_CLASSIFY_WIDTH = 900

TEAM_SUMMARY_LABELS = (
    "possession", "shots", "expected goals", "passes", "tackles",
    "saves", "fouls", "corners", "offsides", "crosses", "interception",
)
UNSUPPORTED_MARKERS = ("threat", "overall possession", "possession won")
GK_MARKERS = ("goalkeeper rating", "shots against", "save success", "overall saving", "punch save")


def _shrink(crop):
    height, width = crop.shape[:2]
    if width <= MAX_CLASSIFY_WIDTH:
        return crop
    scale = MAX_CLASSIFY_WIDTH / width
    return cv2.resize(crop, (MAX_CLASSIFY_WIDTH, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)


def decide_player_screen(gk_probe_text: str) -> str:
    """'player_gk' | 'player_summary' from the Goalkeeper-Rating probe."""
    return "player_gk" if "goalkeep" in gk_probe_text.lower() else "player_summary"


def decide_team_screen(body_lines: list[str]) -> str:
    """'team_summary' | 'team_events' | 'player_gk' | 'unsupported' from the
    body band of a screen whose header did NOT say PLAYER. player_gk is the
    flaky-header fallback — its save-stat labels are unmistakable."""
    lowered = [line.lower() for line in body_lines]
    if any(marker in line for line in lowered for marker in GK_MARKERS):
        return "player_gk"
    if any(marker in line for line in lowered for marker in UNSUPPORTED_MARKERS):
        return "unsupported"

    label_hits = sum(1 for line in lowered if any(label in line for label in TEAM_SUMMARY_LABELS))
    if label_hits >= 2:
        return "team_summary"

    def is_event_row(line: str) -> bool:
        tokens = line.split()
        if len(tokens) < 2:  # a lone number (a score, a page dot) isn't a row
            return False
        has_minute = any(parse_minute(token) is not None for token in tokens)
        has_name = any(any(ch.isalpha() for ch in token) for token in tokens)
        return has_minute and has_name

    if sum(1 for line in body_lines if is_event_row(line)) >= 1:
        return "team_events"
    return "unsupported"


def classify_screenshot(image) -> str:
    header_crop = crop_fractional(image, HEADER_STRIP)
    header_text, _ = read_text(clean_for_ocr(_shrink(header_crop)))
    if "player" in header_text.lower():
        probe_crop = crop_fractional(image, GK_PROBE)
        probe_text, _ = read_text(clean_for_ocr(probe_crop))
        return decide_player_screen(probe_text)

    body_crop = crop_fractional(image, BODY_BAND)  # full resolution -- see module docstring
    body_lines = [line["text"] for line in read_lines(clean_for_ocr(body_crop))]
    return decide_team_screen(body_lines)
