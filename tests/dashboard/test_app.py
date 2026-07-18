"""Smoke tests for the Streamlit dashboard via streamlit's AppTest: run the
whole script headlessly against an empty and a populated database and
assert no view raises. The FIFA_DASH_DB env var is the app's test hook
(AppTest can't pass CLI args through to argparse)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from fifa_analytics.db.models import (
    connect,
    create_match,
    get_or_create_season,
    get_or_create_team,
    init_db,
    upsert_player,
    upsert_scouting_candidate,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "src" / "fifa_analytics" / "dashboard" / "app.py")


def _run(db_path, monkeypatch):
    monkeypatch.setenv("FIFA_DASH_DB", str(db_path))
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_empty_db_shows_setup_hint_not_crash(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    init_db(str(db))
    at = _run(db, monkeypatch)
    assert any("Import card data" in str(block.value) for block in at.info)


def test_populated_db_renders_all_tabs(tmp_path, monkeypatch):
    db = tmp_path / "full.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, us, them, "dir1", home_score=2, away_score=0)

    positions = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST", "CM"]
    star = None
    for i, position in enumerate(positions):
        pid = upsert_player(conn, f"Player {i}", position, 70 + i, "test", team_id=us)
        if star is None:
            star = pid
    conn.execute(
        """INSERT INTO true_overall_history (player_id, match_id, true_overall, true_pace, confidence_score)
           VALUES (?, ?, 72.5, 74.0, 0.4)""",
        (star, match),
    )
    conn.execute(
        """INSERT INTO team_match_expected (match_id, team_id, expected_goals_for,
           expected_goals_against, expected_points, actual_points) VALUES (?, ?, 1.8, 0.6, 2.2, 3.0)""",
        (match, us),
    )
    upsert_scouting_candidate(
        conn, "Target CB", "Rival FC", "test", "CB", 24, 88, 90,
        base_pace=80, base_shooting=45, base_passing=70, base_dribbling=65,
        base_defending=89, base_physical=84, estimated_wage=None,
    )
    conn.commit()
    conn.close()

    at = _run(db, monkeypatch)
    # every tab rendered at least one dataframe and no tab errored
    assert len(at.dataframe) >= 4
    assert not at.error
