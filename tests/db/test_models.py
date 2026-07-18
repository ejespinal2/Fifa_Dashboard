import sqlite3

from fifa_analytics.db.models import (
    connect,
    create_match,
    delete_match,
    get_or_create_season,
    get_or_create_team,
    init_db,
    reset_match_data,
    set_player_team,
    update_match_result,
    upsert_player,
    upsert_scouting_candidate,
)


def test_init_db_is_idempotent(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    init_db(path)  # re-running against an already-current db must not error


def test_init_db_migrates_stale_scouting_candidates_table(tmp_path):
    """A database created before Phase 4 widened scouting_candidates (added
    name/club_name/sub-attributes, dropped fit_score) gets stuck on the old
    columns forever, since CREATE TABLE IF NOT EXISTS is a no-op once the
    table exists -- init_db must detect and migrate it rather than leaving
    every later scouting_importer run to crash on a missing column."""
    path = str(tmp_path / "stale.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE scouting_candidates (
            candidate_id INTEGER PRIMARY KEY,
            position TEXT, age INTEGER, current_overall INTEGER,
            potential INTEGER, fit_score REAL
        )"""
    )
    conn.commit()
    conn.close()

    init_db(path)

    conn = connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(scouting_candidates)")}
    assert "name" in columns and "club_name" in columns and "fit_score" not in columns
    # and the migrated table actually works
    upsert_scouting_candidate(
        conn, "Test Player", "Test FC", "test", "CB", 22, 70, 80,
        base_pace=70, base_shooting=40, base_passing=60,
        base_dribbling=55, base_defending=75, base_physical=70, estimated_wage=None,
    )
    conn.close()


def test_init_db_adds_competition_column_to_old_matches_table(tmp_path):
    """Pre-schedule databases have a matches table without `competition`;
    init_db must ALTER it in additively (matches is real user data, never
    drop-and-recreate like the scouting snapshot)."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE matches (
            match_id INTEGER PRIMARY KEY, season_id INTEGER NOT NULL,
            matchweek INTEGER NOT NULL, home_team_id INTEGER NOT NULL,
            away_team_id INTEGER NOT NULL, home_score INTEGER,
            away_score INTEGER, date TEXT, screenshot_dir TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    init_db(path)

    conn = connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(matches)")}
    conn.close()
    assert "competition" in columns


def _match_with_data(conn):
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, us, them, "dir", date="2026-07-18", competition="Premier League")
    player = upsert_player(conn, "Alice", "ST", 80, "test", team_id=us)
    capture = conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path) VALUES (?, 'team_summary', 'x.png')",
        (match,),
    ).lastrowid
    conn.execute(
        "INSERT INTO match_stat_values (capture_id, stat_name, stat_value) VALUES (?, 'goals', 1)", (capture,)
    )
    conn.execute(
        "INSERT INTO true_overall_history (player_id, match_id, true_overall) VALUES (?, ?, 80.5)", (player, match)
    )
    conn.execute(
        "INSERT INTO team_match_expected (match_id, team_id, expected_points) VALUES (?, ?, 2.0)", (match, us)
    )
    conn.commit()
    return us, them, match, player


def test_update_match_result_and_delete_match(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    us, them, match, player = _match_with_data(conn)

    update_match_result(conn, match, 3, 1)
    row = conn.execute("SELECT home_score, away_score FROM matches WHERE match_id = ?", (match,)).fetchone()
    assert (row["home_score"], row["away_score"]) == (3, 1)

    delete_match(conn, match)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ocr_captures").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM match_stat_values").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM true_overall_history").fetchone()[0] == 0
    # players/teams untouched
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1
    conn.close()


def test_reset_match_data_keeps_teams_players_and_scouting(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    _match_with_data(conn)
    upsert_scouting_candidate(
        conn, "Candidate", "Club", "test", "CB", 20, 70, 85,
        base_pace=70, base_shooting=40, base_passing=60,
        base_dribbling=55, base_defending=75, base_physical=70, estimated_wage=None,
    )

    deleted = reset_match_data(conn)
    assert deleted["matches"] == 1 and deleted["seasons"] == 1

    for wiped in ("matches", "seasons", "ocr_captures", "match_stat_values",
                  "true_overall_history", "team_match_expected", "match_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {wiped}").fetchone()[0] == 0, wiped
    for kept in ("teams", "players", "scouting_candidates"):
        assert conn.execute(f"SELECT COUNT(*) FROM {kept}").fetchone()[0] > 0, kept
    conn.close()


def test_set_player_team(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    player = upsert_player(conn, "Alice", "ST", 80, "test", team_id=us)

    set_player_team(conn, player, them)
    row = conn.execute("SELECT team_id FROM players WHERE player_id = ?", (player,)).fetchone()
    assert row["team_id"] == them
    conn.close()
