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

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

import cv2

from fifa_analytics.cards.eafc26_datahub_importer import (
    RAW_CSV_URL,
    find_by_exact_name,
    load_rows,
    upsert_player_from_row,
)
from fifa_analytics.db.models import (
    capture_hash_exists,
    connect,
    create_capture,
    create_match_event,
    event_exists,
    get_team_id_by_name,
    player_capture_exists,
    players_for_teams,
    upsert_player,
    write_stat_values,
)
from fifa_analytics.ocr import regions
from fifa_analytics.ocr.event_parse import classify_event_icon, parse_event_rows
from fifa_analytics.ocr.extract import (
    group_fragments_into_lines,
    read_field,
    read_fragments,
    read_number_column,
    read_text,
)
from fifa_analytics.ocr.player_match import clean_ocr_name, match_player
from fifa_analytics.ocr.preprocess import clean_for_ocr, crop_fractional
from fifa_analytics.ocr.team_match import match_team_header

REASSIGNED_SOURCE_LABEL = "eafc26-datahub:reassigned"
REGEN_SOURCE_LABEL = "ocr:regen"

# A run_match_dir call for the same fixture can take 10s of minutes. If the
# UI's Process button gets clicked again before that finishes (it doesn't
# visibly disable during a long blocking call, so a second click looks like
# the only way to "unstick" it), a second full pass would start in parallel
# -- doubling every classification/OCR call and racing writes to the same
# DB. This lock makes a second concurrent call for the same match fail
# fast with a clear message instead.
LOCK_STALE_SECONDS = 2 * 60 * 60  # a crashed run's lock is abandoned after this


class MatchAlreadyProcessingError(RuntimeError):
    pass


def _lock_path(db_path: str, match_id: int) -> Path:
    lock_dir = Path(db_path).resolve().parent / ".ocr_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"match_{match_id}.lock"


def _acquire_lock_file(path: Path) -> None:
    """Raises MatchAlreadyProcessingError if a live lock holds path; clears
    a stale one (crashed/killed prior run) and retries the atomic create
    once before giving up."""
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(time.time()).encode())
            os.close(fd)
            return
        except FileExistsError:
            age = time.time() - path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                raise MatchAlreadyProcessingError(
                    "This match is already being processed (started "
                    f"{age / 60:.0f} min ago) — wait for it to finish rather than clicking "
                    "Process again; a second run would double every OCR call and race writes "
                    "to the same database."
                )
            if attempt == 0:
                path.unlink(missing_ok=True)  # stale -- clear it and retry the atomic create
    raise MatchAlreadyProcessingError("Could not acquire the processing lock — try again.")


@contextmanager
def _match_lock(db_path: str, match_id: int):
    path = _lock_path(db_path, match_id)
    _acquire_lock_file(path)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _file_hash(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _hash_unless_duplicate(conn, match_id: int, image_path: str) -> str | None:
    """Returns the image's content hash to stamp on its capture, or None
    when this exact file was already processed for this match (an
    accidental double-download or a re-clicked Process run) — skip it."""
    content_hash = _file_hash(image_path)
    if capture_hash_exists(conn, match_id, content_hash):
        print(f"{Path(image_path).name}: identical image already processed for this match — skipped.")
        return None
    return content_hash


def _split_row_value_cols(
    image, stat_list_box, stat_order, col_box, reader=None
) -> dict[str, tuple[float | None, float]]:
    """Crop each stat row down to col_box's x-range and read a number from
    it. reader is how that number is pulled out: read_field (the default)
    for a crop holding a single value column, or read_leftmost_number for a
    crop spanning two columns where the player's value is the left one.
    Defaults to None (not read_field itself) so a test monkeypatching
    pipeline.read_field is still honored — a default of read_field would
    bind the original function at definition time."""
    reader = reader or read_field
    rows = regions.even_rows(stat_list_box, len(stat_order))
    out = {}
    for stat_name, row_box in zip(stat_order, rows):
        x1, y1, x2, y2 = row_box
        col_x1, col_x2 = col_box
        field_box = (col_x1, y1, col_x2, y2)
        crop = crop_fractional(image, field_box)
        cleaned = clean_for_ocr(crop)
        out[stat_name] = reader(cleaned)
    return out


def _read_stat_column(image, stat_list_box, stat_order, col_box) -> dict[str, tuple[float | None, float]]:
    """Read a whole value column in ONE OCR pass and map each number to its
    stat by vertical position (extract.read_number_column). Far more
    reliable for a column of small single-digit values than OCR'ing each
    row's crop separately, which drops/clips isolated digits. col_box is
    the value column's x-range; the strip's height spans stat_list_box."""
    x0, y0, x1, y1 = stat_list_box
    col_x0, col_x1 = col_box
    strip = crop_fractional(image, (col_x0, y0, col_x1, y1))
    values = read_number_column(clean_for_ocr(strip), len(stat_order))
    return dict(zip(stat_order, values))


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
    content_hash = _hash_unless_duplicate(conn, match_id, image_path)
    if content_hash is None:
        return None, "duplicate_image"

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
        if player_id is not None and player_capture_exists(conn, match_id, player_id, "player_summary"):
            print(f"{Path(image_path).name}: {cleaned_name!r} already has a player_summary for this match — skipped "
                  "(a different screenshot of the same tab would double their stats).")
            return None, "duplicate_player"
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
        content_hash=content_hash,
    )

    stats = _read_stat_column(
        image,
        regions.PLAYER_SUMMARY_REGIONS["stat_list_box"],
        regions.PLAYER_SUMMARY_STAT_ORDER,
        regions.PLAYER_SUMMARY_REGIONS["stat_value_span"],
    )

    # The in-game match rating ("Total Rating: 7.5") sits outside the stat
    # list, in its own header region — parse_numeric pulls the 7.5 out of the
    # surrounding label text.
    rating_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["total_rating"])
    stats["match_rating"] = read_field(clean_for_ocr(rating_crop))

    write_stat_values(conn, capture_id, stats)
    return capture_id, confidence


def process_player_gk(
    conn,
    match_id: int,
    image_path: str,
    home_team_id: int,
    home_team_name: str,
    away_team_id: int,
    away_team_name: str,
    candidates: list,
    csv_rows: list,
) -> tuple[int | None, str]:
    """The Player Performance screen's Goalkeeping tab — one per keeper,
    captured ALONGSIDE their Summary-tab screenshot (the Summary tab
    carries minutes played, which weights all model evidence; the
    Goalkeeping tab carries the save stats that actually describe a
    keeper's match). Same screen family as player_summary, so the team
    header and player name resolve identically; only the stat list
    differs. Stored as capture_type 'player_gk' with gk_-prefixed stat
    names plus the tab's own goalkeeper_rating.
    """
    content_hash = _hash_unless_duplicate(conn, match_id, image_path)
    if content_hash is None:
        return None, "duplicate_image"

    image = cv2.imread(image_path)

    header_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["team_header"])
    header_text, _ = read_text(clean_for_ocr(header_crop))
    team_match = match_team_header(header_text, home_team_id, home_team_name, away_team_id, away_team_name)

    name_crop = crop_fractional(image, regions.PLAYER_SUMMARY_REGIONS["active_player_name"])
    ocr_name, _ = read_text(clean_for_ocr(name_crop))
    cleaned_name = clean_ocr_name(ocr_name)

    if team_match.team_id is not None:
        team_candidates = [c for c in candidates if c["team_id"] == team_match.team_id]
        player_id, confidence = resolve_player(conn, cleaned_name, team_match.team_id, team_candidates, csv_rows)
        team_id = team_match.team_id
        if player_id is not None and player_capture_exists(conn, match_id, player_id, "player_gk"):
            print(f"{Path(image_path).name}: {cleaned_name!r} already has a player_gk capture for this match — skipped.")
            return None, "duplicate_player"
    else:
        print(f"Could not tell which team {image_path} belongs to (OCR read {header_text!r}) — needs manual assignment.")
        player_id, team_id, confidence = None, None, "unresolved_team"

    capture_id = create_capture(
        conn, match_id, "player_gk", image_path,
        player_id=player_id, team_id=team_id, raw_text=ocr_name,
        match_confidence=confidence, content_hash=content_hash,
    )

    stats = _split_row_value_cols(
        image,
        regions.PLAYER_GK_REGIONS["stat_list_box"],
        regions.PLAYER_GK_STAT_ORDER,
        regions.PLAYER_GK_REGIONS["stat_value_col"],
    )
    rating_crop = crop_fractional(image, regions.PLAYER_GK_REGIONS["goalkeeper_rating"])
    stats["goalkeeper_rating"] = read_field(clean_for_ocr(rating_crop))

    write_stat_values(conn, capture_id, stats)
    return capture_id, confidence


def process_team_summary(conn, match_id: int, home_team_id: int, away_team_id: int, image_path: str) -> list[int]:
    """One screenshot shows both teams' columns side by side, so it produces
    two captures — one per team — sharing the same screenshot_path.
    """
    content_hash = _hash_unless_duplicate(conn, match_id, image_path)
    if content_hash is None:
        return []

    image = cv2.imread(image_path)
    capture_ids = []

    for team_id, col_box in (
        (home_team_id, regions.TEAM_SUMMARY_REGIONS["stat_value_col_home"]),
        (away_team_id, regions.TEAM_SUMMARY_REGIONS["stat_value_col_away"]),
    ):
        capture_id = create_capture(conn, match_id, "team_summary", image_path, team_id=team_id, content_hash=content_hash)
        stats = _split_row_value_cols(
            image,
            regions.TEAM_SUMMARY_REGIONS["stat_list_box"],
            regions.TEAM_SUMMARY_STAT_ORDER,
            col_box,
        )
        write_stat_values(conn, capture_id, stats)
        capture_ids.append(capture_id)

    return capture_ids


# The icon sits at roughly the row's vertical center regardless of how
# tall the row's OCR'd text bbox happens to be — a lone minute+name line
# (no hanging sub-off text below it) reads as a short bbox, and a purely
# proportional pad can end up too thin to include the icon at all (this is
# the likely cause of a real goal icon reading "unknown" while others in
# the same screenshot classified fine). Pad by whichever is bigger: 50% of
# the row's own height, or this fixed fraction of the whole band.
MIN_ICON_PAD_FRACTION = 0.025


def _side_icon_region(band: tuple, event: dict, side: str) -> tuple:
    """Full-image fractional crop for one event row's icon: that side's
    icon zone horizontally, the row's own band vertically, padded to
    reliably include the icon regardless of the row's OCR'd text height."""
    x0, y0, x1, y1 = band
    icon_x0, icon_x1 = regions.TEAM_EVENTS_ICON_ZONES[side]
    band_height = y1 - y0
    row_top = y0 + event["y_top"] * band_height
    row_bottom = y0 + event["y_bottom"] * band_height
    pad = max(0.5 * (row_bottom - row_top), MIN_ICON_PAD_FRACTION * band_height)
    return (icon_x0, max(y0, row_top - pad), icon_x1, min(y1, row_bottom + pad))


def _store_event(conn, match_id, capture_id, team_id, candidates, name, minute, event_type, stored):
    """Roster-match one name (within the side's team) and store the event
    unless an overlapping scrolled screenshot already did."""
    team_candidates = [c for c in candidates if c["team_id"] == team_id]
    player_match = match_player(clean_ocr_name(name), team_candidates)
    if player_match.player_id is None:
        print(f"team_events: parsed {name!r} at minute {minute} ({event_type}) but couldn't match the roster.")
        return
    if event_exists(conn, match_id, player_match.player_id, minute, event_type):
        return  # same row seen in an overlapping scrolled screenshot
    create_match_event(conn, match_id, capture_id, team_id, player_match.player_id, minute, event_type)
    stored.append(
        {"player_id": player_match.player_id, "team_id": team_id, "minute": minute, "event_type": event_type}
    )


def process_team_events(
    conn, match_id: int, image_path: str, home_team_id: int, away_team_id: int, candidates: list
) -> tuple[int, list[dict]]:
    """Parses the Events tab's center-spine layout (see event_parse.py):
    minute circles on a central spine, the home team's events extending
    left and the away team's right — so the side a name sits on IS its
    team. A row with a name hanging below it is STRUCTURALLY a
    substitution (see event_parse's module docstring) and is stored as
    sub_on (the row's name) + sub_off (the hanging name) without ever
    consulting icon color. Every other row's icon is classified from that
    side's icon zone: goal, missed_penalty, penalty_goal (converted
    penalty), yellow_card, or red_card.

    A match with more events than fit on screen is captured as several
    scrolled screenshots (team_events.png, team_events_2.png, ... or
    unrenamed files auto-classified as events screens) — rows visible in
    two of them dedupe on (player, minute, type), so overlapping scroll
    positions are safe. 'HT' markers and scroll arrows parse as nothing
    and are skipped.

    The band's full raw text is always stored on the capture regardless,
    so nothing is lost when parsing falls short.

    Returns (capture_id, [event_info dicts actually stored]).
    """
    content_hash = _hash_unless_duplicate(conn, match_id, image_path)
    if content_hash is None:
        return None, []

    image = cv2.imread(image_path)

    band = regions.TEAM_EVENTS_REGIONS["event_band"]
    band_crop = crop_fractional(image, band)
    fragments = read_fragments(clean_for_ocr(band_crop))
    raw_text = "\n".join(line["text"] for line in group_fragments_into_lines(fragments))
    confidence = (
        sum(f["confidence"] for f in fragments) / len(fragments) if fragments else 0.0
    )
    capture_id = create_capture(conn, match_id, "team_events", image_path, raw_text=raw_text, content_hash=content_hash)
    conn.execute(
        "UPDATE ocr_captures SET ocr_confidence_avg = ? WHERE capture_id = ?",
        (confidence, capture_id),
    )
    conn.commit()

    events = parse_event_rows(fragments)
    if not events:
        print(f"team_events: no event rows parsed from OCR text {raw_text!r}")

    stored: list[dict] = []
    for event in events:
        side = event["side"]
        team_id = home_team_id if side == "home" else away_team_id

        if event["sub_off_name"]:
            # Structural: a hanging name below means this IS a substitution,
            # regardless of what the icon zone would say (there's no
            # reliable icon there for subs at all — see event_parse.py).
            _store_event(conn, match_id, capture_id, team_id, candidates,
                         event["name"], event["minute"], "sub_on", stored)
            _store_event(conn, match_id, capture_id, team_id, candidates,
                         event["sub_off_name"], event["minute"], "sub_off", stored)
        else:
            icon_crop = crop_fractional(image, _side_icon_region(band, event, side))
            event_type = classify_event_icon(icon_crop)
            _store_event(conn, match_id, capture_id, team_id, candidates,
                         event["name"], event["minute"], event_type, stored)
    return capture_id, stored


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


def _find_player_gk_files(match_path: Path) -> list[Path]:
    found = set()
    for ext in IMAGE_EXTENSIONS:
        found.update(match_path.glob(f"player_gk{ext}"))
        found.update(match_path.glob(f"player_gk_*{ext}"))
    return sorted(found)


def _find_team_events_files(match_path: Path) -> list[Path]:
    """team_events.png alone, or team_events.png + team_events_2.png + ...
    when the events list needed scrolling to capture in full. Overlap
    between scroll positions is fine — rows dedupe on (player, minute,
    type) at insert time."""
    found = set()
    for ext in IMAGE_EXTENSIONS:
        found.update(match_path.glob(f"team_events{ext}"))
        found.update(match_path.glob(f"team_events_*{ext}"))
    return sorted(found)


def _find_unreserved_images(match_path: Path) -> list[Path]:
    """Every image in the folder that ISN'T using a reserved name
    (team_summary / team_events[_N] / player_summary_*) — these get
    content-classified instead of name-routed, so straight-off-the-console
    filenames like IMG_0042.jpg work without renaming."""
    reserved = (
        set(_find_player_summary_files(match_path))
        | set(_find_team_events_files(match_path))
        | set(_find_player_gk_files(match_path))
    )
    single = _find_single_file(match_path, "team_summary")
    if single is not None:
        reserved.add(single)
    found = set()
    for ext in IMAGE_EXTENSIONS:
        found.update(match_path.glob(f"*{ext}"))
    return sorted(p for p in found if p not in reserved and "calibration" not in p.name)


def run_match_dir(db_path: str, match_dir: str, match_id: int, home_team_name: str, away_team_name: str) -> None:
    """home_team_name/away_team_name must already exist in the teams table
    (i.e. you've run the card importer for both squads first).

    Files using the reserved names are routed by name, exactly as before.
    Any OTHER image in the folder is classified by content
    (ocr/classify_screen.py) and routed the same way — so a folder of
    unrenamed console screenshots works. Screens the pipeline can't parse
    (e.g. the Possession tab's Threat timeline) are skipped with a note.
    """
    with _match_lock(db_path, match_id):
        _run_match_dir_locked(db_path, match_dir, match_id, home_team_name, away_team_name)


def _run_match_dir_locked(db_path: str, match_dir: str, match_id: int, home_team_name: str, away_team_name: str) -> None:
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

        # 1) content-classify every non-reserved image, then merge into the
        #    name-routed buckets below. Hash-check FIRST, before the (much
        #    pricier) classification OCR: a duplicate download/re-run is
        #    common and should cost nothing, not a wasted classify pass.
        from fifa_analytics.ocr.classify_screen import classify_screenshot

        unreserved = _find_unreserved_images(match_path)
        classified: dict[str, list[Path]] = {"team_summary": [], "team_events": [], "player_summary": [], "player_gk": []}
        total = len(unreserved)
        for i, file in enumerate(unreserved, start=1):
            if capture_hash_exists(conn, match_id, _file_hash(str(file))):
                print(f"[{i}/{total}] {file.name}: identical image already processed for this match — skipped.")
                continue
            kind = classify_screenshot(cv2.imread(str(file)))
            if kind in classified:
                classified[kind].append(file)
                print(f"[{i}/{total}] {file.name}: auto-classified as {kind}")
            else:
                print(f"[{i}/{total}] {file.name}: unsupported screen (e.g. Possession/Threat tab) — skipped. "
                      "Rename to a reserved name to force a type.")

        # 2) name-routed files, exactly as before
        team_summary_files = []
        team_summary = _find_single_file(match_path, "team_summary")
        if team_summary is not None:
            team_summary_files.append(team_summary)
        team_summary_files.extend(classified["team_summary"])
        if not team_summary_files:
            print(f"No team_summary screenshot found in {match_dir}")
        for file in team_summary_files:
            process_team_summary(conn, match_id, home_team_id, away_team_id, str(file))

        team_events_files = _find_team_events_files(match_path) + classified["team_events"]
        if not team_events_files:
            print(f"No team_events screenshot found in {match_dir}")
        for i, file in enumerate(team_events_files, start=1):
            started = time.monotonic()
            _, stored = process_team_events(conn, match_id, str(file), home_team_id, away_team_id, candidates)
            print(f"[events {i}/{len(team_events_files)}] {file.name}: {len(stored)} new event(s) "
                  f"({time.monotonic() - started:.1f}s)")

        player_summary_files = _find_player_summary_files(match_path) + classified["player_summary"]
        if not player_summary_files:
            print(f"No player_summary screenshots found in {match_dir}")
        for i, file in enumerate(player_summary_files, start=1):
            started = time.monotonic()
            capture_id, confidence = process_player_summary(
                conn, match_id, str(file), home_team_id, home_team_name, away_team_id, away_team_name, candidates, csv_rows
            )
            elapsed = time.monotonic() - started
            flag = "" if confidence == "exact" else f" — double-check in validate_app.py (confidence={confidence})"
            print(f"[player_summary {i}/{len(player_summary_files)}] {file.name}: {confidence} ({elapsed:.1f}s){flag}")

        player_gk_files = _find_player_gk_files(match_path) + classified["player_gk"]
        for i, file in enumerate(player_gk_files, start=1):
            started = time.monotonic()
            capture_id, confidence = process_player_gk(
                conn, match_id, str(file), home_team_id, home_team_name, away_team_id, away_team_name, candidates, csv_rows
            )
            elapsed = time.monotonic() - started
            flag = "" if confidence == "exact" else f" — double-check in validate_app.py (confidence={confidence})"
            print(f"[player_gk {i}/{len(player_gk_files)}] {file.name}: {confidence} ({elapsed:.1f}s){flag}")
    finally:
        conn.close()
