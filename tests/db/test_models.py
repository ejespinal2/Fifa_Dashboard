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


def test_settings_roundtrip(tmp_path):
    from fifa_analytics.db.models import get_setting, set_setting
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    assert get_setting(conn, "my_team_name") is None
    set_setting(conn, "my_team_name", "Us FC")
    set_setting(conn, "my_team_name", "Them FC")  # overwrite, not duplicate
    assert get_setting(conn, "my_team_name") == "Them FC"
    conn.close()


def test_event_exists_dedupes_overlapping_screenshots(tmp_path):
    from fifa_analytics.db.models import create_match_event, event_exists
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    us, them, match, player = _match_with_data(conn)
    capture = conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path) VALUES (?, 'team_events', 'e.png')",
        (match,),
    ).lastrowid

    assert not event_exists(conn, match, player, 37, "goal")
    create_match_event(conn, match, capture, us, player, 37, "goal")
    assert event_exists(conn, match, player, 37, "goal")
    # same player+minute but different type is a different event (goal + booking in the same minute)
    assert not event_exists(conn, match, player, 37, "yellow_card")
    conn.close()


def test_init_db_rebuilds_ocr_captures_for_player_gk_and_hash(tmp_path):
    """Old databases have ocr_captures with a CHECK that rejects
    'player_gk' and no content_hash column; SQLite can't ALTER a CHECK, so
    init_db rebuilds the table in place, keeping every row."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE matches (match_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE players (player_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE teams (team_id INTEGER PRIMARY KEY)")
    conn.execute(
        """CREATE TABLE ocr_captures (
            capture_id INTEGER PRIMARY KEY,
            match_id INTEGER NOT NULL REFERENCES matches(match_id),
            capture_type TEXT NOT NULL CHECK (capture_type IN ('player_summary', 'team_summary', 'team_events')),
            player_id INTEGER REFERENCES players(player_id),
            team_id INTEGER REFERENCES teams(team_id),
            screenshot_path TEXT NOT NULL,
            ocr_confidence_avg REAL, raw_text TEXT, match_confidence TEXT,
            reviewed INTEGER NOT NULL DEFAULT 0, reviewed_at TEXT
        )"""
    )
    conn.execute("INSERT INTO matches (match_id) VALUES (1)")
    conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path, reviewed) VALUES (1, 'player_summary', 'x.png', 1)"
    )
    conn.commit()
    conn.close()

    init_db(path)

    conn = connect(path)
    row = conn.execute("SELECT * FROM ocr_captures").fetchone()
    assert row["capture_type"] == "player_summary" and row["reviewed"] == 1  # data survived
    assert row["content_hash"] is None
    # and the new type + column work
    conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path, content_hash) VALUES (1, 'player_gk', 'g.png', 'abc')"
    )
    conn.commit()
    conn.close()


def test_capture_and_player_dedupe_helpers(tmp_path):
    from fifa_analytics.db.models import capture_hash_exists, create_capture, player_capture_exists
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    us, them, match, player = _match_with_data(conn)

    assert not capture_hash_exists(conn, match, "hash1")
    create_capture(conn, match, "player_summary", "a.png", player_id=player, content_hash="hash1")
    assert capture_hash_exists(conn, match, "hash1")
    assert not capture_hash_exists(conn, match, "hash2")

    assert player_capture_exists(conn, match, player, "player_summary")
    assert not player_capture_exists(conn, match, player, "player_gk")
    conn.close()
