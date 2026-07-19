import pytest

from fifa_analytics.dashboard import queries
from fifa_analytics.db.models import (
    connect,
    create_match,
    get_or_create_season,
    get_or_create_team,
    init_db,
    upsert_player,
)


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    c = connect(path)
    yield c
    c.close()


def _history(conn, player_id, match_id, overall, pace=None, shooting=None):
    conn.execute(
        """INSERT INTO true_overall_history
           (player_id, match_id, true_overall, true_pace, true_shooting, confidence_score)
           VALUES (?, ?, ?, ?, ?, 0.5)""",
        (player_id, match_id, overall, pace, shooting),
    )
    conn.commit()


def _setup(conn):
    """Two teams, two matches, one player with history on both matches."""
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Them FC")
    season = get_or_create_season(conn, "2025-26")
    m1 = create_match(conn, season, 1, us, them, "dir1", home_score=2, away_score=0)
    m2 = create_match(conn, season, 2, them, us, "dir2", home_score=1, away_score=1)
    p1 = upsert_player(conn, "Alice", "ST", 80, "test", team_id=us)
    upsert_player(conn, "Bob", "CB", 75, "test", team_id=us)
    _history(conn, p1, m1, 80.5, pace=82.0)
    _history(conn, p1, m2, 81.2, pace=83.0, shooting=79.0)
    return us, them, m1, m2, p1


def test_teams_with_players_counts(conn):
    _setup(conn)
    teams = queries.teams_with_players(conn)
    assert [(t["name"], t["player_count"]) for t in teams] == [("Us FC", 2)]


def test_squad_overview_uses_latest_history_and_delta(conn):
    us, *_ = _setup(conn)
    squad = queries.squad_overview(conn, us)
    alice = next(p for p in squad if p["name"] == "Alice")
    bob = next(p for p in squad if p["name"] == "Bob")
    assert alice["true_overall"] == 81.2  # latest match, not the first
    assert alice["delta"] == 1.2
    assert alice["matches_modeled"] == 2
    assert bob["true_overall"] is None and bob["delta"] is None  # card-only player


def test_player_progression_orders_and_numbers_matches(conn):
    us, *_ = _setup(conn)
    rows = queries.player_progression(conn, us)
    assert [(r["player"], r["match_number"], r["true_overall"]) for r in rows] == [
        ("Alice", 1, 80.5),
        ("Alice", 2, 81.2),
    ]


def test_attribute_progression_skips_evidence_gaps(conn):
    *_, p1 = _setup(conn)
    rows = queries.attribute_progression(conn, p1)
    # match 1 scored pace only; match 2 scored pace and shooting -- no
    # zero-filled rows for the unscored attributes
    assert {(r["match_number"], r["attribute"]) for r in rows} == {
        (1, "pace"), (2, "pace"), (2, "shooting"),
    }


def test_team_match_xpts_names_opponent_both_home_and_away(conn):
    us, them, m1, m2, _ = _setup(conn)
    for match_id, team_id, xpts, pts in ((m1, us, 2.1, 3.0), (m2, us, 1.4, 1.0)):
        conn.execute(
            """INSERT INTO team_match_expected
               (match_id, team_id, expected_goals_for, expected_goals_against,
                expected_points, actual_points) VALUES (?, ?, 1.5, 0.8, ?, ?)""",
            (match_id, team_id, xpts, pts),
        )
    conn.commit()
    rows = queries.team_match_xpts(conn, us)
    assert [r["opponent"] for r in rows] == ["Them FC", "Them FC"]  # home in m1, away in m2
    assert [r["xpts"] for r in rows] == [2.1, 1.4]

    table = queries.season_xpts_table(conn)
    assert table[0]["team"] == "Us FC"
    assert table[0]["delta"] == round(4.0 - 3.5, 2)


def test_schedule_lists_fixtures_with_capture_counts(conn):
    us, them, m1, m2, _ = _setup(conn)
    conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path) VALUES (?, 'team_summary', 'x.png')",
        (m1,),
    )
    conn.execute("UPDATE matches SET date = '2026-07-01', competition = 'Premier League' WHERE match_id = ?", (m1,))
    conn.execute("UPDATE matches SET date = '2026-07-08', competition = 'FA Cup' WHERE match_id = ?", (m2,))
    conn.commit()

    fixtures = queries.schedule(conn)
    assert [f["date"] for f in fixtures] == ["2026-07-08", "2026-07-01"]  # newest first
    by_id = {f["match_id"]: f for f in fixtures}
    assert by_id[m1]["captures"] == 1 and by_id[m2]["captures"] == 0
    assert by_id[m1]["home_team"] == "Us FC" and by_id[m1]["away_team"] == "Them FC"


def test_team_record_totals_and_per_competition(conn):
    us, them, m1, m2, _ = _setup(conn)
    # m1: us home, 2-0 win. m2: us away, 1-1 draw. (scores set in _setup)
    conn.execute("UPDATE matches SET competition = 'Premier League' WHERE match_id = ?", (m1,))
    conn.execute("UPDATE matches SET competition = 'FA Cup' WHERE match_id = ?", (m2,))
    conn.commit()

    record = queries.team_record(conn, us)
    total = record[0]
    assert total["competition"] == "All competitions"
    assert (total["played"], total["W"], total["D"], total["L"]) == (2, 1, 1, 0)
    assert (total["GF"], total["GA"], total["points"]) == (3, 1, 4)
    by_comp = {r["competition"]: r for r in record[1:]}
    assert by_comp["Premier League"]["W"] == 1
    assert by_comp["FA Cup"]["D"] == 1

    # unplayed fixtures (no scores) don't count
    conn.execute("UPDATE matches SET home_score = NULL, away_score = NULL WHERE match_id = ?", (m2,))
    conn.commit()
    record = queries.team_record(conn, us)
    assert record[0]["played"] == 1


def test_search_players_matches_substring_any_team(conn):
    us, them, *_ = _setup(conn)
    from fifa_analytics.db.models import upsert_player
    upsert_player(conn, "Alicia Keys", "CM", 70, "test", team_id=them)

    found = queries.search_players(conn, "Alic")
    names = [p["name"] for p in found]
    assert names == ["Alice", "Alicia Keys"]  # overall DESC
    assert found[0]["team"] == "Us FC" and found[1]["team"] == "Them FC"


def test_match_facts_events_and_side_by_side_stats(conn):
    us, them, m1, _, p1 = _setup(conn)
    capture = conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path, team_id) VALUES (?, 'team_summary', 'x.png', ?)",
        (m1, us),
    ).lastrowid
    conn.execute("INSERT INTO match_stat_values (capture_id, stat_name, stat_value) VALUES (?, 'possession_pct', 32)", (capture,))
    away_capture = conn.execute(
        "INSERT INTO ocr_captures (match_id, capture_type, screenshot_path, team_id) VALUES (?, 'team_summary', 'x.png', ?)",
        (m1, them),
    ).lastrowid
    conn.execute("INSERT INTO match_stat_values (capture_id, stat_name, stat_value) VALUES (?, 'possession_pct', 68)", (away_capture,))
    conn.execute(
        "INSERT INTO match_events (match_id, capture_id, team_id, player_id, minute, event_type) VALUES (?, ?, ?, ?, 37, 'goal')",
        (m1, capture, us, p1),
    )
    conn.execute(
        "INSERT INTO match_events (match_id, capture_id, team_id, player_id, minute, event_type) VALUES (?, ?, ?, ?, 12, 'missed_penalty')",
        (m1, capture, us, p1),
    )
    conn.commit()

    events = queries.match_events_list(conn, m1)
    assert [(e["minute"], e["event_type"]) for e in events] == [(12, "missed_penalty"), (37, "goal")]
    assert events[1]["player"] == "Alice" and events[1]["team"] == "Us FC"

    stats = queries.match_team_stats(conn, m1)
    assert stats == [{"stat": "possession_pct", "home": 32.0, "away": 68.0}]
