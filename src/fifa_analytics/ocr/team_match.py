"""Determines which of the match's two teams a Player Performance screenshot
belongs to, by OCR'ing the team name shown in its header (e.g. "MAN UTD").

This is separate from player_match.py's roster matching: an unmatched player
could belong to either team, and we need to know which before we can decide
where to (re-)home them. In-game team names are often abbreviated ("MAN
UTD") against the full club name used elsewhere ("Manchester United"), so
this can't rely on a substring/prefix check — it uses character-level
similarity (difflib) instead, picking whichever of the two known candidates
scores higher. Since there are only ever two options, a middling absolute
score is fine as long as it ranks the true club above the other one, which
holds for any two clubs that aren't very similarly named.

Unverified against a real screenshot — the header crop region in
PLAYER_SUMMARY_REGIONS['team_header'] is a visual estimate, like the other
regions were before calibration.
"""

import difflib
from dataclasses import dataclass

from fifa_analytics.ocr.player_match import normalize

MIN_SCORE = 0.15  # below this, treat the OCR read as too garbled to trust either way


@dataclass
class TeamMatchResult:
    team_id: int | None
    confidence: str  # "matched" | "none"


def match_team_header(ocr_text: str, home_team_id: int, home_name: str, away_team_id: int, away_name: str) -> TeamMatchResult:
    if not ocr_text:
        return TeamMatchResult(None, "none")

    ocr_norm = normalize(ocr_text)
    home_score = difflib.SequenceMatcher(None, ocr_norm, normalize(home_name)).ratio()
    away_score = difflib.SequenceMatcher(None, ocr_norm, normalize(away_name)).ratio()

    if max(home_score, away_score) < MIN_SCORE:
        return TeamMatchResult(None, "none")

    return TeamMatchResult(home_team_id if home_score >= away_score else away_team_id, "matched")
