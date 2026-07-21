"""The OCR validation queue's team_events editor: the real-match testing
found a genuinely misclassified goal icon ('unknown' instead of 'goal')
and a blank penalty_kicks team-stat, and rather than guess at another
pixel threshold, the fix is giving the reviewer a way to correct the
stored data directly before a recompute. Covers the db-layer replace
helper plus the editor rendering and persisting edits end-to-end via
Streamlit's AppTest."""

import sys
from pathlib import Path

import cv2
import numpy as np
from streamlit.testing.v1 import AppTest

from fifa_analytics.db.models import (
    connect,
    create_capture,
    create_match,
    create_match_event,
    get_or_create_season,
    get_or_create_team,
    init_db,
    load_match_events,
    replace_match_events,
    upsert_player,
)

APP_PATH = str(Path(__file__).resolve().parents[2] / "src" / "fifa_analytics" / "validate_app.py")


def _seed_match_with_event(tmp_path):
    db_path = tmp_path / "fifa.db"
    conn = connect(str(db_path))
    init_db(str(db_path))
    season_id = get_or_create_season(conn, "2025/26")
    home_id = get_or_create_team(conn, "Atletico Madrid")
    away_id = get_or_create_team(conn, "Man Utd")
    screenshot_dir = tmp_path / "match_1"
    screenshot_dir.mkdir()
    match_id = create_match(conn, season_id, 1, home_id, away_id, str(screenshot_dir))
    sesko = upsert_player(conn, "B. Sesko", "ST", 82, "test", team_id=away_id)
    screenshot_path = screenshot_dir / "team_events.png"
    cv2.imwrite(str(screenshot_path), np.zeros((4, 4, 3), dtype=np.uint8))
    capture_id = create_capture(conn, match_id, "team_events", str(screenshot_path))
    # the real bug: a goal's ball icon classified as "unknown"
    create_match_event(conn, match_id, capture_id, away_id, sesko, 41, "unknown")
    conn.close()
    return db_path, match_id, capture_id, home_id, away_id, sesko


def test_replace_match_events_persists_edits(tmp_path):
    db_path, match_id, capture_id, home_id, away_id, sesko = _seed_match_with_event(tmp_path)
    conn = connect(str(db_path))

    replace_match_events(
        conn, capture_id, match_id,
        [{"player_id": sesko, "team_id": away_id, "minute": 41, "event_type": "goal"}],
    )

    events = load_match_events(conn, capture_id)
    assert len(events) == 1
    assert (events[0]["player_id"], events[0]["minute"], events[0]["event_type"]) == (sesko, 41, "goal")


def test_replace_match_events_can_add_and_remove_rows(tmp_path):
    db_path, match_id, capture_id, home_id, away_id, sesko = _seed_match_with_event(tmp_path)
    conn = connect(str(db_path))

    # a second event OCR missed entirely (e.g. the Cardoso/Koke sub) added,
    # and the original "unknown" row corrected -- both persist together
    replace_match_events(
        conn, capture_id, match_id,
        [
            {"player_id": sesko, "team_id": away_id, "minute": 41, "event_type": "goal"},
            {"player_id": sesko, "team_id": away_id, "minute": 88, "event_type": "sub_off"},
        ],
    )
    events = load_match_events(conn, capture_id)
    assert len(events) == 2
    assert {(e["minute"], e["event_type"]) for e in events} == {(41, "goal"), (88, "sub_off")}

    # deleting is just passing a shorter list -- confirms the wipe-then-
    # rewrite doesn't leave stale rows behind
    replace_match_events(conn, capture_id, match_id, [])
    assert load_match_events(conn, capture_id) == []


def test_page_shows_unknown_event_and_confirm_lets_it_be_recorrected(tmp_path, monkeypatch):
    db_path, match_id, capture_id, home_id, away_id, sesko = _seed_match_with_event(tmp_path)

    monkeypatch.setattr(sys, "argv", ["validate_app.py", "--db", str(db_path)])
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception, at.exception

    type_key = f"{capture_id}_event_1_type"
    minute_key = f"{capture_id}_event_1_minute"
    assert at.selectbox(key=type_key).value == "unknown"
    assert at.number_input(key=minute_key).value == 41

    # retype the misclassified icon from "unknown" to "goal" and confirm
    at.selectbox(key=type_key).select("goal").run()
    confirm_button = next(b for b in at.button if b.label == "Confirm and mark reviewed")
    confirm_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db_path))
    events = load_match_events(conn, capture_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "goal"
    assert events[0]["player_id"] == sesko
    assert events[0]["minute"] == 41


def test_deleting_a_row_via_the_player_dropdown_removes_it_on_confirm(tmp_path, monkeypatch):
    db_path, match_id, capture_id, home_id, away_id, sesko = _seed_match_with_event(tmp_path)

    monkeypatch.setattr(sys, "argv", ["validate_app.py", "--db", str(db_path)])
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception, at.exception

    player_key = f"{capture_id}_event_1_player"
    at.selectbox(key=player_key).select("-- delete this row --").run()
    confirm_button = next(b for b in at.button if b.label == "Confirm and mark reviewed")
    confirm_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db_path))
    assert load_match_events(conn, capture_id) == []


def test_add_another_event_row_lets_a_missed_event_be_recorded(tmp_path, monkeypatch):
    db_path, match_id, capture_id, home_id, away_id, sesko = _seed_match_with_event(tmp_path)

    monkeypatch.setattr(sys, "argv", ["validate_app.py", "--db", str(db_path)])
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception, at.exception

    add_button = next(b for b in at.button if b.label == "+ Add another event")
    add_button.click().run()

    new_player_key = f"{capture_id}_new_event_0_player"
    new_minute_key = f"{capture_id}_new_event_0_minute"
    new_type_key = f"{capture_id}_new_event_0_type"
    assert at.selectbox(key=new_player_key)  # the blank row rendered

    at.selectbox(key=new_player_key).select("B. Sesko").run()
    at.number_input(key=new_minute_key).set_value(88).run()
    at.selectbox(key=new_type_key).select("sub_off").run()

    # correct the original "unknown" row too, in the same pass
    at.selectbox(key=f"{capture_id}_event_1_type").select("goal").run()

    confirm_button = next(b for b in at.button if b.label == "Confirm and mark reviewed")
    confirm_button.click().run()
    assert not at.exception, at.exception

    conn = connect(str(db_path))
    events = load_match_events(conn, capture_id)
    assert {(e["minute"], e["event_type"]) for e in events} == {(41, "goal"), (88, "sub_off")}
