"""Content-based screenshot classification, so files dropped into a match
folder don't have to follow the reserved naming convention — the pipeline
looks at each unrecognized image and decides what screen it is.

Discriminators, cheapest-first:

- The Player Performance screen is the only one whose top strip says
  "PLAYER" — team screens show "TEAM 1 : 0 TEAM" up there instead.
- Team screens all share the same tab bar (Summary/Possession/.../Events),
  so the ACTIVE tab can't be read as text; the body content decides:
  * the Threat timeline ("Threat", "Overall Possession") = the Possession
    tab — real data, but nothing this pipeline parses → unsupported,
    skipped with a note rather than mis-ingested.
  * several known stat labels (Possession %, Shots, ...) = team Summary.
  * rows parsing as "player minute" = the Events tab.

decide() is pure so the logic is testable without OCR; classify_screenshot
does the cropping/OCR and delegates. Reserved filenames always win over
auto-classification (see pipeline.run_match_dir) — if a screen ever
misclassifies, naming the file is the override.
"""

from fifa_analytics.ocr.event_parse import parse_event_text
from fifa_analytics.ocr.extract import read_lines, read_text
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional

HEADER_STRIP = (0.0, 0.0, 0.6, 0.16)   # catches "PLAYER PERFORMANCE" on player screens
BODY_BAND = (0.03, 0.16, 0.97, 0.95)   # everything under the tab bar on team screens

TEAM_SUMMARY_LABELS = (
    "possession", "shots", "expected goals", "passes", "tackles",
    "saves", "fouls", "corners", "offsides", "crosses", "interception",
)
UNSUPPORTED_MARKERS = ("threat", "overall possession", "possession won")


def decide(header_text: str, body_lines: list[str]) -> str:
    """'player_summary' | 'team_summary' | 'team_events' | 'unsupported'."""
    if "player" in header_text.lower():
        return "player_summary"

    lowered = [line.lower() for line in body_lines]
    if any(marker in line for line in lowered for marker in UNSUPPORTED_MARKERS):
        return "unsupported"

    label_hits = sum(1 for line in lowered if any(label in line for label in TEAM_SUMMARY_LABELS))
    if label_hits >= 2:
        return "team_summary"

    event_rows = sum(1 for line in body_lines if parse_event_text(line) != (None, None))
    if event_rows >= 1:
        return "team_events"
    return "unsupported"


def classify_screenshot(image) -> str:
    header_crop = crop_fractional(image, HEADER_STRIP)
    header_text, _ = read_text(clean_for_ocr(header_crop))
    body_crop = crop_fractional(image, BODY_BAND)
    body_lines = [line["text"] for line in read_lines(clean_for_ocr(body_crop))]
    return decide(header_text, body_lines)
