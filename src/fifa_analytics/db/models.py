"""Thin sqlite3 access layer — no ORM. schema.sql is the source of truth."""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_stale_scouting_candidates(conn: sqlite3.Connection) -> None:
    """Phase 4 widened scouting_candidates (name/club_name/sub-attributes
    added, fit_score dropped) after some databases already had the old,
    narrower version of this table from an earlier CREATE TABLE IF NOT
    EXISTS run -- which leaves it in the old shape forever, since IF NOT
    EXISTS is a no-op once the table exists. Safe to just drop and let the
    schema recreate it: this table is a fully disposable, source-refreshed
    snapshot (cleared and rebuilt by every scouting_importer run), never
    hand-edited or referenced by other tables, unlike players/matches/etc.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'scouting_candidates'"
    ).fetchone()
    if row is None:
        return
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(scouting_candidates)")}
    if "name" not in columns:
        conn.execute("DROP TABLE scouting_candidates")


def init_db(db_path: str) -> None:
    conn = connect(db_path)
    try:
        _migrate_stale_scouting_candidates(conn)
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def get_or_create_team(conn: sqlite3.Connection, name: str, league: str | None = None) -> int:
    row = conn.execute("SELECT team_id FROM teams WHERE name = ?", (name,)).fetchone()
    if row:
        return row["team_id"]
    cur = conn.execute("INSERT INTO teams (name, league) VALUES (?, ?)", (name, league))
    conn.commit()
    return cur.lastrowid


def get_or_create_season(conn: sqlite3.Connection, year_label: str) -> int:
    row = conn.execute("SELECT season_id FROM seasons WHERE year_label = ?", (year_label,)).fetchone()
    if row:
        return row["season_id"]
    cur = conn.execute("INSERT INTO seasons (year_label) VALUES (?)", (year_label,))
    conn.commit()
    return cur.lastrowid


def upsert_player(
    conn: sqlite3.Connection,
    name: str,
    position: str,
    base_overall: int | None,
    source: str,
    team_id: int | None = None,
    jersey_number: int | None = None,
    base_pace: int | None = None,
    base_shooting: int | None = None,
    base_passing: int | None = None,
    base_dribbling: int | None = None,
    base_defending: int | None = None,
    base_physical: int | None = None,
    age: int | None = None,
    potential: int | None = None,
) -> int:
    row = conn.execute(
        "SELECT player_id FROM players WHERE name = ? AND source = ? AND team_id IS ?",
        (name, source, team_id),
    ).fetchone()
    if row:
        player_id = row["player_id"]
        conn.execute(
            """UPDATE players SET position=?, jersey_number=?, base_overall=?, base_pace=?,
               base_shooting=?, base_passing=?, base_dribbling=?, base_defending=?,
               base_physical=?, age=?, potential=? WHERE player_id=?""",
            (
                position, jersey_number, base_overall, base_pace, base_shooting,
                base_passing, base_dribbling, base_defending, base_physical,
                age, potential, player_id,
            ),
        )
    else:
        cur = conn.execute(
            """INSERT INTO players (name, team_id, position, jersey_number, base_overall, base_pace,
               base_shooting, base_passing, base_dribbling, base_defending, base_physical,
               age, potential, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, team_id, position, jersey_number, base_overall, base_pace, base_shooting,
                base_passing, base_dribbling, base_defending, base_physical,
                age, potential, source,
            ),
        )
        player_id = cur.lastrowid
    conn.commit()
    return player_id


def players_for_teams(conn: sqlite3.Connection, team_ids: list[int]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(team_ids))
    return conn.execute(
        f"SELECT player_id, name, team_id FROM players WHERE team_id IN ({placeholders})",
        team_ids,
    ).fetchall()


def get_team_id_by_name(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT team_id FROM teams WHERE name = ?", (name,)).fetchone()
    return row["team_id"] if row else None


def create_match(
    conn: sqlite3.Connection,
    season_id: int,
    matchweek: int,
    home_team_id: int,
    away_team_id: int,
    screenshot_dir: str,
    home_score: int | None = None,
    away_score: int | None = None,
    date: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO matches (season_id, matchweek, home_team_id, away_team_id,
           home_score, away_score, date, screenshot_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (season_id, matchweek, home_team_id, away_team_id, home_score, away_score, date, screenshot_dir),
    )
    conn.commit()
    return cur.lastrowid


def create_capture(
    conn: sqlite3.Connection,
    match_id: int,
    capture_type: str,
    screenshot_path: str,
    player_id: int | None = None,
    team_id: int | None = None,
    raw_text: str | None = None,
    match_confidence: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO ocr_captures (match_id, capture_type, player_id, team_id, screenshot_path, raw_text, match_confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (match_id, capture_type, player_id, team_id, screenshot_path, raw_text, match_confidence),
    )
    conn.commit()
    return cur.lastrowid


def write_stat_values(conn: sqlite3.Connection, capture_id: int, stats: dict) -> None:
    """stats: {stat_name: (value, confidence)}"""
    conn.executemany(
        """INSERT OR REPLACE INTO match_stat_values (capture_id, stat_name, stat_value, ocr_confidence)
           VALUES (?, ?, ?, ?)""",
        [(capture_id, name, value, conf) for name, (value, conf) in stats.items()],
    )
    conn.execute(
        "UPDATE ocr_captures SET ocr_confidence_avg = ? WHERE capture_id = ?",
        (
            sum(c for _, c in stats.values() if c is not None) / max(len(stats), 1),
            capture_id,
        ),
    )
    conn.commit()


def mark_reviewed(conn: sqlite3.Connection, capture_id: int, reviewed_at: str) -> None:
    conn.execute(
        "UPDATE ocr_captures SET reviewed = 1, reviewed_at = ? WHERE capture_id = ?",
        (reviewed_at, capture_id),
    )
    conn.commit()


def create_match_event(
    conn: sqlite3.Connection,
    match_id: int,
    capture_id: int,
    team_id: int | None,
    player_id: int | None,
    minute: int | None,
    event_type: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO match_events (match_id, capture_id, team_id, player_id, minute, event_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (match_id, capture_id, team_id, player_id, minute, event_type),
    )
    conn.commit()
    return cur.lastrowid


def clear_scouting_candidates(conn: sqlite3.Connection, source: str) -> None:
    """Scouting candidates are a refreshable snapshot, not user-owned data
    (unlike players) -- re-importing wipes the previous snapshot for that
    source first, so stale rows never linger after filters change."""
    conn.execute("DELETE FROM scouting_candidates WHERE source = ?", (source,))
    conn.commit()


def upsert_scouting_candidate(
    conn: sqlite3.Connection,
    name: str,
    club_name: str | None,
    source: str,
    position: str,
    age: int | None,
    current_overall: int | None,
    potential: int | None,
    base_pace: int | None,
    base_shooting: int | None,
    base_passing: int | None,
    base_dribbling: int | None,
    base_defending: int | None,
    base_physical: int | None,
    estimated_wage: float | None,
) -> int:
    cur = conn.execute(
        """INSERT OR REPLACE INTO scouting_candidates
           (name, club_name, source, position, age, current_overall, potential,
            base_pace, base_shooting, base_passing, base_dribbling, base_defending,
            base_physical, estimated_wage)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name, club_name, source, position, age, current_overall, potential,
            base_pace, base_shooting, base_passing, base_dribbling, base_defending,
            base_physical, estimated_wage,
        ),
    )
    conn.commit()
    return cur.lastrowid


def all_scouting_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM scouting_candidates").fetchall()
