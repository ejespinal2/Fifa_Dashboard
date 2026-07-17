"""Walks a match's screenshot folder and OCRs the 3 Phase 1 capture types
into the database as draft (unreviewed) rows.

Expected folder contents for one match, e.g.
    data/screenshots/season_01/matchweek_03/match_0042/
        team_summary.png          # the scrolled-up view: Possession %..Yellow Cards
        team_events.png
        player_summary_*.png      # one per player who featured, for EITHER team —
                                   # filenames don't need to encode who's in them;
                                   # each one's player AND team are identified
                                   # from the screenshot itself (see below)

This only OCRs and stores stat_name -> (value, confidence) pairs, plus a raw
text dump for team_events (see regions.py for why events aren't parsed into
structured rows yet). Nothing here is marked reviewed=1 — that happens in
validate_app.py after a human confirms the values.

Every player_summary screenshot goes through 3 layers before falling back to
manual review:
    1. OCR the header team name/crest, match it against the match's two
       known team names (team_match.py) -> tells us which roster to check.
    2. Match the OCR'd player name against that team's already-imported
       roster (player_match.py) -> the common case.
    3. If no roster match: search the FULL card dataset by exact name. A hit
       means this player transferred within your save and the dataset still
       lists them under their old real-world club — they get re-imported
       under the correct in-game team_id, so future matches find them
       directly in step 2. A miss means nobody's ever heard of them (a
       Career Mode academy graduate/regen) — a bare player row is created
       with just the name, team, and OCR'd stats; base_overall etc. stay
       NULL until a "true overall" model or manual entry backfills them.
Only if step 1 itself fails (the header OCR is too garbled to tell the two
teams apart) does a capture fall all the way through to unresolved, needing
a human to assign it in validate_app.py.
"""

from pathlib import Path

import cv2

from fifa_analytics.cards.eafc26_datahub_importer import (
    RAW_CSV_URL,
    find_by_exact_name,
    load_rows,
    upsert_player_from_row,
)
from fifa_analytics.db.models import (
    connect,
    create_capture,
    create_match_event,
    get_team_id_by_name,
    players_for_teams,
    upsert_player,
    write_stat_values,
)
from fifa_analytics.ocr import regions
from fifa_analytics.ocr.event_parse import classify_event_icon, parse_event_text
from fifa_analytics.ocr.extract import read_field, read_text
from fifa_analytics.ocr.player_match import clean_ocr_name, match_player
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional
from fifa_analytics.ocr.team_match import match_team_header

REASSIGNED_SOURCE_LABEL = "eafc26-datahub:reassigned"
REGEN_SOURCE_LABEL = "ocr:regen"


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


def resolve_player(conn, ocr_name: str, team_id: int, candidates: list, csv_rows: list) -> tuple[int, str]:
    """candidates: this team's rows from players_for_teams (already filtered
    to team_id by the caller). Returns (player_id, confidence) where
    confidence is one of player_match's ("exact"/"surname"/"fuzzy"), or
    "reassigned" (found elsewhere in the dataset, re-homed to team_id), or
    "new_player" (not found anywhere -- a bare record was created).
    """
    roster_match = match_player(ocr_name, candidates)
    if roster_match.player_id is not None:
        return roster_match.player_id, roster_match.confidence

    transferred_row = find_by_exact_name(csv_rows, ocr_name)
    if transferred_row is not None:
        player_id = upsert_player_from_row(conn, transferred_row, team_id, REASSIGNED_SOURCE_LABEL)
        return player_id, "reassigned"

    player_id = upsert_player(
        conn, name=ocr_name, position="UNK", base_overall=None, source=REGEN_SOURCE_LABEL, team_id=team_id
    )
    return player_id, "new_player"


def process_player_summary(
    conn,
    match_id: int,
    image_path: str,
    home_team_id: int,
    home_team_name: str,
    away_team_id: int,
    away_team_name: str,
    candidates: list,
    csv_rows: list,
) -> tuple[int, str]:
    """candidates: rows from players_for_teams(conn, [home_team_id, away_team_id]).
    csv_rows: the full card dataset (load_rows(RAW_CSV_URL)), for the
    transferred-player fallback.

    Returns (capture_id, match_confidence) — "unresolved_team" if even the
    header OCR couldn't tell the two teams apart, in which case the capture
    still gets created (stats intact, player_id/team_id left NULL) for
    manual assignment in validate_app.py.
    """
    image = cv2.imread(image_path)

    header_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["team_header"])
    header_text, _ = read_text(clean_for_ocr(header_crop))
    team_match = match_team_header(header_text, home_team_id, home_team_name, away_team_id, away_team_name)

    name_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["active_player_name"])
    ocr_name, _ = read_text(clean_for_ocr(name_crop))
    # Strip UI numbers the crop may have picked up (e.g. the rating circle's
    # "7.5") before any matching — see clean_ocr_name's docstring.
    cleaned_name = clean_ocr_name(ocr_name)

    if team_match.team_id is not None:
        team_candidates = [c for c in candidates if c["team_id"] == team_match.team_id]
        player_id, confidence = resolve_player(conn, cleaned_name, team_match.team_id, team_candidates, csv_rows)
        team_id = team_match.team_id
    else:
        print(f"Could not tell which team {image_path} belongs to (OCR read {header_text!r}) — needs manual assignment.")
        player_id, team_id, confidence = None, None, "unresolved_team"

    capture_id = create_capture(
        conn,
        match_id,
        "player_summary",
        image_path,
        player_id=player_id,
        team_id=team_id,
        raw_text=ocr_name,
        match_confidence=confidence,
    )

    stats = _split_row_value_cols(
        image,
        regions.PLAYER_SUMMARY_REGIONS["stat_list_box"],
        regions.PLAYER_SUMMARY_STAT_ORDER,
        regions.PLAYER_SUMMARY_REGIONS["stat_value_col_player"],
    )

    # The in-game match rating ("Total Rating: 7.5") sits outside the stat
    # list, in its own header region — parse_numeric pulls the 7.5 out of the
    # surrounding label text.
    rating_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["total_rating"])
    stats["match_rating"] = read_field(clean_for_ocr(rating_crop))

    write_stat_values(conn, capture_id, stats)
    return capture_id, confidence


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


def process_team_events(conn, match_id: int, image_path: str, candidates: list) -> tuple[int, dict | None]:
    """candidates: combined rosters (players_for_teams(conn, [home_team_id,
    away_team_id])) — used to look up which team the named player belongs
    to, since a team_events screenshot doesn't show a team header the way
    player_summary does.

    Always stores the raw OCR text dump of the whole event band (as
    before). Additionally attempts to parse ONE structured event — player,
    minute, and goal/yellow_card/red_card — into the match_events table.
    See this module's docstring and event_parse.py for what's confirmed
    (goal icon, from one real sample) vs. unverified (card icon colors;
    whether multiple events even fit in event_band).

    Returns (capture_id, event_info) — event_info is None if the name/minute
    couldn't be parsed from the OCR text, or the parsed name didn't match
    either roster.
    """
    image = cv2.imread(image_path)

    band_crop = crop_fractional(image, regions.TEAM_EVENTS_REGIONS["event_band"])
    raw_text, confidence = read_text(clean_for_ocr(band_crop))
    capture_id = create_capture(conn, match_id, "team_events", image_path, raw_text=raw_text)
    conn.execute(
        "UPDATE ocr_captures SET ocr_confidence_avg = ? WHERE capture_id = ?",
        (confidence, capture_id),
    )
    conn.commit()

    event_info = None
    name, minute = parse_event_text(raw_text)
    if name is None or minute is None:
        print(f"team_events: couldn't parse a player name + minute from OCR text {raw_text!r}")
        return capture_id, event_info

    player_match = match_player(clean_ocr_name(name), candidates)
    if player_match.player_id is None:
        print(f"team_events: parsed {name!r} at minute {minute} but couldn't match to either roster.")
        return capture_id, event_info

    icon_crop = crop_fractional(image, regions.TEAM_EVENTS_REGIONS["event_icon"])
    event_type = classify_event_icon(icon_crop)
    create_match_event(conn, match_id, capture_id, player_match.team_id, player_match.player_id, minute, event_type)
    event_info = {
        "player_id": player_match.player_id,
        "team_id": player_match.team_id,
        "minute": minute,
        "event_type": event_type,
    }
    return capture_id, event_info


# PS5 screenshots, "Save As" from a browser, etc. don't reliably land as
# .png -- .jpg/.jpeg are just as likely. Checked case-sensitively per
# extension since some filesystems (notably not Windows, but worth being
# explicit) distinguish .JPG from .jpg.
IMAGE_EXTENSIONS = (".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG")


def _find_single_file(match_path: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTENSIONS:
        candidate = match_path / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _find_player_summary_files(match_path: Path) -> list[Path]:
    found = set()
    for ext in IMAGE_EXTENSIONS:
        found.update(match_path.glob(f"player_summary_*{ext}"))
    return sorted(found)


def run_match_dir(db_path: str, match_dir: str, match_id: int, home_team_name: str, away_team_name: str) -> None:
    """home_team_name/away_team_name must already exist in the teams table
    (i.e. you've run the card importer for both squads first).
    """
    conn = connect(db_path)
    try:
        home_team_id = get_team_id_by_name(conn, home_team_name)
        away_team_id = get_team_id_by_name(conn, away_team_name)
        if home_team_id is None or away_team_id is None:
            missing = home_team_name if home_team_id is None else away_team_name
            raise ValueError(f"Team {missing!r} not found — import its card data first.")

        candidates = players_for_teams(conn, [home_team_id, away_team_id])
        csv_rows = load_rows(RAW_CSV_URL)
        match_path = Path(match_dir)

        team_summary = _find_single_file(match_path, "team_summary")
        if team_summary is not None:
            process_team_summary(conn, match_id, home_team_id, away_team_id, str(team_summary))
        else:
            print(f"No team_summary.(png/jpg/jpeg) found in {match_dir}")

        team_events = _find_single_file(match_path, "team_events")
        if team_events is not None:
            process_team_events(conn, match_id, str(team_events), candidates)
        else:
            print(f"No team_events.(png/jpg/jpeg) found in {match_dir}")

        player_summary_files = _find_player_summary_files(match_path)
        if not player_summary_files:
            print(f"No player_summary_*.(png/jpg/jpeg) files found in {match_dir}")
        for file in player_summary_files:
            capture_id, confidence = process_player_summary(
                conn, match_id, str(file), home_team_id, home_team_name, away_team_id, away_team_name, candidates, csv_rows
            )
            if confidence not in ("exact",):
                print(f"{file.name}: matched with confidence={confidence} (capture_id={capture_id}) — double-check in validate_app.py")
    finally:
        conn.close()
