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

from fifa_analytics.ocr.event_parse import parse_minute
from fifa_analytics.ocr.extract import read_lines, read_text
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional

HEADER_STRIP = (0.0, 0.0, 0.6, 0.16)   # catches "PLAYER PERFORMANCE" on player screens
BODY_BAND = (0.03, 0.16, 0.97, 0.95)   # everything under the tab bar on team screens

TEAM_SUMMARY_LABELS = (
    "possession", "shots", "expected goals", "passes", "tackles",
    "saves", "fouls", "corners", "offsides", "crosses", "interception",
)
UNSUPPORTED_MARKERS = ("threat", "overall possession", "possession won")
GK_MARKERS = ("goalkeeper rating", "shots against", "save success", "overall saving", "punch save")


def decide(header_text: str, body_lines: list[str]) -> str:
    """'player_summary' | 'player_gk' | 'team_summary' | 'team_events' | 'unsupported'."""
    lowered_lines = [line.lower() for line in body_lines]
    gk_hits = any(marker in line for line in lowered_lines for marker in GK_MARKERS)
    if "player" in header_text.lower():
        return "player_gk" if gk_hits else "player_summary"
    if gk_hits:  # header OCR flaked but the Goalkeeping tab is unmistakable
        return "player_gk"

    lowered = [line.lower() for line in body_lines]
    if any(marker in line for line in lowered for marker in UNSUPPORTED_MARKERS):
        return "unsupported"

    label_hits = sum(1 for line in lowered if any(label in line for label in TEAM_SUMMARY_LABELS))
    if label_hits >= 2:
        return "team_summary"

    # Events-tab rows carry a minute token ("65'", "45+2'") somewhere in the
    # line (the spine layout puts it mid-line between the two teams' names),
    # alongside at least one name-like word.
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
    header_text, _ = read_text(clean_for_ocr(header_crop))
    body_crop = crop_fractional(image, BODY_BAND)
    body_lines = [line["text"] for line in read_lines(clean_for_ocr(body_crop))]
    return decide(header_text, body_lines)
