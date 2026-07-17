"""Walks a match's screenshot folder and OCRs the 3 Phase 1 capture types
into the database as draft (unreviewed) rows.

Expected folder contents for one match, e.g.
    data/screenshots/season_01/matchweek_03/match_0042/
        team_summary.png          # the scrolled-up view: Possession %..Yellow Cards
        team_events.png
        player_summary_*.png      # one per player who featured, for EITHER team —
                                   # filenames don't need to encode who's in them;
                                   # the player is identified by OCR'ing the
                                   # on-screen name and matching it against
                                   # the two teams' rosters (see player_match.py)

This only OCRs and stores stat_name -> (value, confidence) pairs, plus a raw
text dump for team_events (see regions.py for why events aren't parsed into
structured rows yet). Nothing here is marked reviewed=1 — that happens in
validate_app.py after a human confirms the values.
"""

from pathlib import Path

import cv2

from fifa_analytics.db.models import (
    connect,
    create_capture,
    get_team_id_by_name,
    players_for_teams,
    write_stat_values,
)
from fifa_analytics.ocr import regions
from fifa_analytics.ocr.extract import read_field, read_text
from fifa_analytics.ocr.player_match import match_player
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional


def _split_row_value_cols(
    image, stat_list_box, stat_order, col_box
) -> dict[str, tuple[float | None, float]]:
    rows = regions.even_rows(stat_list_box, len(stat_order))
    out = {}
    for stat_name, row_box in zip(stat_order, rows):
        x1, y1, x2, y2 = row_box
        col_x1, col_x2 = col_box
        field_box = (col_x1, y1, col_x2, y2)
        crop = crop_fractional(image, field_box)
        cleaned = clean_for_ocr(crop)
        out[stat_name] = read_field(cleaned)
    return out


def process_player_summary(conn, match_id: int, image_path: str, candidates: list) -> tuple[int, str]:
    """candidates: rows from players_for_teams(conn, [home_team_id, away_team_id]).

    Returns (capture_id, match_confidence) — match_confidence is one of
    "exact"/"surname"/"fuzzy"/"none" (see player_match.match_player). A
    "none" match still creates the capture (player_id left NULL, the OCR'd
    name saved to raw_text) so it surfaces in validate_app.py for manual
    assignment instead of silently dropping the screenshot's data.
    """
    image = cv2.imread(image_path)

    name_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["active_player_name"])
    ocr_name, name_confidence = read_text(clean_for_ocr(name_crop))
    match = match_player(ocr_name, candidates)

    if match.player_id is None:
        print(f"Could not match player in {image_path}: OCR read {ocr_name!r} — needs manual assignment.")

    capture_id = create_capture(
        conn,
        match_id,
        "player_summary",
        image_path,
        player_id=match.player_id,
        team_id=match.team_id,
        raw_text=ocr_name,
    )

    stats = _split_row_value_cols(
        image,
        regions.PLAYER_SUMMARY_REGIONS["stat_list_box"],
        regions.PLAYER_SUMMARY_STAT_ORDER,
        regions.PLAYER_SUMMARY_REGIONS["stat_value_col_player"],
    )
    write_stat_values(conn, capture_id, stats)
    return capture_id, match.confidence


def process_team_summary(conn, match_id: int, home_team_id: int, away_team_id: int, image_path: str) -> list[int]:
    """One screenshot shows both teams' columns side by side, so it produces
    two captures — one per team — sharing the same screenshot_path.
    """
    image = cv2.imread(image_path)
    capture_ids = []

    for team_id, col_box in (
        (home_team_id, regions.TEAM_SUMMARY_REGIONS["stat_value_col_home"]),
        (away_team_id, regions.TEAM_SUMMARY_REGIONS["stat_value_col_away"]),
    ):
        capture_id = create_capture(conn, match_id, "team_summary", image_path, team_id=team_id)
        stats = _split_row_value_cols(
            image,
            regions.TEAM_SUMMARY_REGIONS["stat_list_box"],
            regions.TEAM_SUMMARY_STAT_ORDER,
            col_box,
        )
        write_stat_values(conn, capture_id, stats)
        capture_ids.append(capture_id)

    return capture_ids


def process_team_events(conn, match_id: int, image_path: str) -> int:
    """Stores the raw OCR text dump only — see regions.py docstring on why
    this isn't parsed into structured (player, minute, event_type) rows yet.
    Not tied to a single team_id since one screenshot can show either side's
    events.
    """
    image = cv2.imread(image_path)
    crop = crop_fractional(image, regions.TEAM_EVENTS_REGIONS["event_band"])
    raw_text, confidence = read_text(clean_for_ocr(crop))
    capture_id = create_capture(conn, match_id, "team_events", image_path, raw_text=raw_text)
    conn.execute(
        "UPDATE ocr_captures SET ocr_confidence_avg = ? WHERE capture_id = ?",
        (confidence, capture_id),
    )
    conn.commit()
    return capture_id


def run_match_dir(db_path: str, match_dir: str, match_id: int, home_team_name: str, away_team_name: str) -> None:
    """home_team_name/away_team_name must already exist in the teams table
    (i.e. you've run the card importer for both squads first) — players are
    matched only against these two rosters.
    """
    conn = connect(db_path)
    try:
        home_team_id = get_team_id_by_name(conn, home_team_name)
        away_team_id = get_team_id_by_name(conn, away_team_name)
        if home_team_id is None or away_team_id is None:
            missing = home_team_name if home_team_id is None else away_team_name
            raise ValueError(f"Team {missing!r} not found — import its card data first.")

        candidates = players_for_teams(conn, [home_team_id, away_team_id])
        match_path = Path(match_dir)

        team_summary = match_path / "team_summary.png"
        if team_summary.exists():
            process_team_summary(conn, match_id, home_team_id, away_team_id, str(team_summary))

        team_events = match_path / "team_events.png"
        if team_events.exists():
            process_team_events(conn, match_id, str(team_events))

        for file in sorted(match_path.glob("player_summary_*.png")):
            capture_id, confidence = process_player_summary(conn, match_id, str(file), candidates)
            if confidence != "exact":
                print(f"{file.name}: matched with confidence={confidence} (capture_id={capture_id}) — double-check in validate_app.py")
    finally:
        conn.close()
