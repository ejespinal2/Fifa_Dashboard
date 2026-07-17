"""Walks a match's screenshot folder and OCRs the 3 Phase 1 capture types
into the database as draft (unreviewed) rows.

Expected folder contents for one match, e.g.
    data/screenshots/season_01/matchweek_03/match_0042/
        team_summary_page1.png   # scrolled to show Tackles..Def Line Breaks Attempted
        team_summary_page2.png   # scrolled to show Possession %..Yellow Cards
        team_events.png
        player_summary_<slug>.png   # one per player, slug = lowercase name, spaces -> underscores

This only OCRs and stores stat_name -> (value, confidence) pairs, plus a raw
text dump for team_events (see regions.py for why events aren't parsed into
structured rows yet). Nothing here is marked reviewed=1 — that happens in
validate_app.py after a human confirms the values.
"""

import re
from pathlib import Path

import cv2

from fifa_analytics.db.models import connect, create_capture, write_stat_values
from fifa_analytics.ocr import regions
from fifa_analytics.ocr.extract import read_field, read_text
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


def process_player_summary(conn, match_id: int, player_id: int, image_path: str) -> int:
    image = cv2.imread(image_path)
    capture_id = create_capture(conn, match_id, "player_summary", image_path, player_id=player_id)

    stats = _split_row_value_cols(
        image,
        regions.PLAYER_SUMMARY_REGIONS["stat_list_box"],
        regions.PLAYER_SUMMARY_STAT_ORDER,
        regions.PLAYER_SUMMARY_REGIONS["stat_value_col_player"],
    )
    write_stat_values(conn, capture_id, stats)
    return capture_id


def process_team_summary(conn, match_id: int, team_id: int, page1_path: str, page2_path: str) -> list[int]:
    capture_ids = []

    image1 = cv2.imread(page1_path)
    capture1 = create_capture(conn, match_id, "team_summary", page1_path, team_id=team_id)
    stats1 = _split_row_value_cols(
        image1,
        regions.TEAM_SUMMARY_REGIONS["stat_list_box"],
        regions.TEAM_SUMMARY_PAGE_1_STAT_ORDER,
        regions.TEAM_SUMMARY_REGIONS["stat_value_col_home"],
    )
    write_stat_values(conn, capture1, stats1)
    capture_ids.append(capture1)

    image2 = cv2.imread(page2_path)
    capture2 = create_capture(conn, match_id, "team_summary", page2_path, team_id=team_id)
    stats2 = _split_row_value_cols(
        image2,
        regions.TEAM_SUMMARY_REGIONS["stat_list_box"],
        regions.TEAM_SUMMARY_PAGE_2_STAT_ORDER,
        regions.TEAM_SUMMARY_REGIONS["stat_value_col_home"],
    )
    write_stat_values(conn, capture2, stats2)
    capture_ids.append(capture2)

    return capture_ids


def process_team_events(conn, match_id: int, team_id: int, image_path: str) -> int:
    """Stores the raw OCR text dump only — see regions.py docstring on why
    this isn't parsed into structured (player, minute, event_type) rows yet.
    """
    image = cv2.imread(image_path)
    crop = crop_fractional(image, regions.TEAM_EVENTS_REGIONS["event_band"])
    raw_text, confidence = read_text(clean_for_ocr(crop))
    capture_id = create_capture(
        conn, match_id, "team_events", image_path, team_id=team_id, raw_text=raw_text
    )
    conn.execute(
        "UPDATE ocr_captures SET ocr_confidence_avg = ? WHERE capture_id = ?",
        (confidence, capture_id),
    )
    conn.commit()
    return capture_id


PLAYER_SUMMARY_FILE_RE = re.compile(r"^player_summary_(?P<slug>.+)\.png$")


def run_match_dir(db_path: str, match_dir: str, match_id: int, home_team_id: int, player_slug_to_id: dict[str, int]) -> None:
    """player_slug_to_id maps a filename slug (see PLAYER_SUMMARY_FILE_RE) to
    an existing players.player_id — build this from your roster before running.
    """
    conn = connect(db_path)
    try:
        match_path = Path(match_dir)

        team_summary_page1 = match_path / "team_summary_page1.png"
        team_summary_page2 = match_path / "team_summary_page2.png"
        if team_summary_page1.exists() and team_summary_page2.exists():
            process_team_summary(conn, match_id, home_team_id, str(team_summary_page1), str(team_summary_page2))

        team_events = match_path / "team_events.png"
        if team_events.exists():
            process_team_events(conn, match_id, home_team_id, str(team_events))

        for file in match_path.glob("player_summary_*.png"):
            m = PLAYER_SUMMARY_FILE_RE.match(file.name)
            if not m:
                continue
            slug = m.group("slug")
            player_id = player_slug_to_id.get(slug)
            if player_id is None:
                print(f"Skipping {file.name}: no player_id mapped for slug '{slug}'")
                continue
            process_player_summary(conn, match_id, player_id, str(file))
    finally:
        conn.close()
