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


def test_empty_db_runs_first_run_wizard_end_to_end(tmp_path, monkeypatch):
    """An empty career shows the pick-your-club wizard; completing it
    imports the club (network stubbed), stores the my-club setting, and
    lands on the normal dashboard."""
    db = tmp_path / "empty.db"
    init_db(str(db))

    from fifa_analytics.cards import eafc26_datahub_importer as datahub
    from fifa_analytics.cards import scouting_importer

    def fake_scrape_and_store(club, db_path, source, csv_source=None):
        conn = connect(db_path)
        team = get_or_create_team(conn, club)
        for i, position in enumerate(["GK", "CB", "ST"]):
            upsert_player(conn, f"{club} Player {i}", position, 75 + i, source, team_id=team)
        conn.close()
        return 3

    monkeypatch.setattr(datahub, "list_clubs", lambda csv_source=None: ["Test FC", "Other FC"])
    monkeypatch.setattr(datahub, "scrape_and_store", fake_scrape_and_store)
    monkeypatch.setattr(scouting_importer, "import_scouting_candidates",
                        lambda *args, **kwargs: 0)

    at = _run(db, monkeypatch)
    assert at.selectbox(key="wiz_club")  # the wizard rendered

    at.selectbox(key="wiz_club").select("Test FC").run()
    start_button = next(b for b in at.button if b.label == "Start career")
    start_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db))
    from fifa_analytics.db.models import get_setting
    assert get_setting(conn, "my_team_name") == "Test FC"
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 3
    conn.close()
    # and the dashboard proper rendered with the imported club selected
    assert any("Test FC" in sb.value for sb in at.sidebar.selectbox if sb.value)


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


def test_reset_button_wipes_match_data_but_keeps_rosters(tmp_path, monkeypatch):
    """Drive the Manage tab's danger-zone flow end-to-end: type RESET, click
    the button, and confirm match data is gone while rosters survive."""
    db = tmp_path / "reset.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    season = get_or_create_season(conn, "2025-26")
    create_match(conn, season, 1, us, them, "dir1", home_score=2, away_score=0)
    for i, position in enumerate(["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST"]):
        upsert_player(conn, f"Player {i}", position, 70 + i, "test", team_id=us)
    conn.commit()
    conn.close()

    at = _run(db, monkeypatch)
    at.text_input(key="reset_confirm").set_value("RESET").run()
    reset_button = next(b for b in at.button if b.label == "Reset match stats")
    reset_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 11
    conn.close()


def test_transfer_flow_moves_player(tmp_path, monkeypatch):
    db = tmp_path / "transfer.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    player_id = upsert_player(conn, "Moving Player", "ST", 80, "test", team_id=us)
    conn.commit()
    conn.close()

    at = _run(db, monkeypatch)
    at.text_input(key="pl_search").set_value("Moving").run()
    at.selectbox(key="pl_dest").select("Them FC").run()
    transfer_button = next(b for b in at.button if b.label == "Transfer player")
    transfer_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db))
    row = conn.execute("SELECT team_id FROM players WHERE player_id = ?", (player_id,)).fetchone()
    conn.close()
    assert row["team_id"] == them


def test_create_fixture_flow(tmp_path, monkeypatch):
    db = tmp_path / "fixture.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    get_or_create_team(conn, "Them FC")
    upsert_player(conn, "Someone", "ST", 80, "test", team_id=us)
    conn.commit()
    conn.close()

    at = _run(db, monkeypatch)
    at.selectbox(key="fx_opponent").select("Them FC").run()
    create_button = next(b for b in at.button if b.label == "Create fixture")
    create_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db))
    row = conn.execute(
        """SELECT m.date, m.competition, th.name AS home, ta.name AS away FROM matches m
           JOIN teams th ON th.team_id = m.home_team_id
           JOIN teams ta ON ta.team_id = m.away_team_id"""
    ).fetchone()
    conn.close()
    assert row is not None
    assert (row["home"], row["away"]) == ("Us FC", "Them FC")  # venue defaults to Home
    assert row["date"] is not None and row["competition"] is not None


def test_assistant_tab_answers_with_stubbed_llm(tmp_path, monkeypatch):
    """Chat flow end-to-end with Ollama stubbed: the question builds a
    grounded context pack and the (fake) model's answer renders."""
    db = tmp_path / "assistant.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    for i, position in enumerate(["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST"]):
        upsert_player(conn, f"Player {i}", position, 70 + i, "test", team_id=us)
    conn.commit()
    conn.close()

    from fifa_analytics.assistant import llm

    monkeypatch.setattr(llm, "is_available", lambda timeout=2.0: True)
    monkeypatch.setattr(llm, "list_models", lambda: ["fake-model"])
    seen = {}

    def fake_chat(messages, model=None, timeout=None):
        seen["system"] = messages[0]["content"]
        return "Start Player 10 up front — highest effective rating."

    monkeypatch.setattr(llm, "chat", fake_chat)

    at = _run(db, monkeypatch)
    at.chat_input[0].set_value("Pick my strongest XI on true overalls").run()
    assert not at.exception, at.exception

    rendered = " ".join(str(md.value) for md in at.markdown)
    assert "Start Player 10 up front" in rendered
    assert "CONTEXT DATA" in seen["system"]      # grounding pack reached the model
    assert "best_xi" in seen["system"]           # squad section was routed in


def test_assistant_tab_degrades_without_ollama(tmp_path, monkeypatch):
    db = tmp_path / "no_ollama.db"
    init_db(str(db))
    conn = connect(str(db))
    us = get_or_create_team(conn, "Us FC")
    for i, position in enumerate(["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST"]):
        upsert_player(conn, f"Player {i}", position, 70 + i, "test", team_id=us)
    conn.commit()
    conn.close()

    from fifa_analytics.assistant import llm

    monkeypatch.setattr(llm, "is_available", lambda timeout=2.0: False)
    at = _run(db, monkeypatch)
    assert any("ollama.com" in str(block.value) for block in at.info)  # setup help shown
    at.chat_input[0].set_value("who should I sell?").run()
    assert not at.exception, at.exception  # data pack still renders, no crash
