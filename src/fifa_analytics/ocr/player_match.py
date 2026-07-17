"""Matches an OCR'd on-screen player name against a team's roster.

This is what lets the pipeline take "here are two team names and a folder of
player_summary screenshots" instead of a hand-built filename -> player_id
mapping: the active_player_name crop is OCR'd, then matched against whichever
players already belong to the match's two teams (populated by the card
importer beforehand).

The two name formats don't line up exactly. In-game shows a player's full
first + last name, sometimes wrapped across two lines (e.g. "Aurélien" /
"Tchouaméni" read by OCR as "Aurelien Tchouameni"). The card-data source's
short_name is often abbreviated to "F. Last" (e.g. "B. Mbeumo"), though full
names appear too when there's no need to abbreviate. Matching therefore
leans on the surname — the last whitespace-separated token — since it's
present in both formats and rarely collides within a single ~16-25 player
squad.
"""

import unicodedata
from dataclasses import dataclass


@dataclass
class MatchResult:
    player_id: int | None
    team_id: int | None
    confidence: str  # "exact" | "surname" | "fuzzy" | "none"


def normalize(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


def surname(name: str) -> str:
    tokens = normalize(name).split()
    return tokens[-1] if tokens else ""


def match_player(ocr_name: str, candidates: list) -> MatchResult:
    """candidates: rows/dicts with at least 'player_id', 'name', 'team_id'."""
    if not ocr_name or not candidates:
        return MatchResult(None, None, "none")

    normalized_ocr = normalize(ocr_name)

    exact = [c for c in candidates if normalize(c["name"]) == normalized_ocr]
    if len(exact) == 1:
        return MatchResult(exact[0]["player_id"], exact[0]["team_id"], "exact")

    ocr_surname = surname(ocr_name)
    if ocr_surname:
        surname_matches = [c for c in candidates if surname(c["name"]) == ocr_surname]
        if len(surname_matches) == 1:
            return MatchResult(surname_matches[0]["player_id"], surname_matches[0]["team_id"], "surname")

    import difflib

    names = [normalize(c["name"]) for c in candidates]
    close = difflib.get_close_matches(normalized_ocr, names, n=1, cutoff=0.75)
    if close:
        idx = names.index(close[0])
        return MatchResult(candidates[idx]["player_id"], candidates[idx]["team_id"], "fuzzy")

    return MatchResult(None, None, "none")
