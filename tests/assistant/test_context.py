import pytest

from fifa_analytics.assistant.context import build_context, build_messages, find_opponent
from fifa_analytics.assistant.knowledge import EXPLAINERS, relevant_explainers
from fifa_analytics.db.models import connect, get_or_create_team, init_db, upsert_player

POSITIONS = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST", "ST"]


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    c = connect(path)
    yield c
    c.close()


def _squads(conn):
    us = get_or_create_team(conn, "Us FC")
    them = get_or_create_team(conn, "Bayer 04 Leverkusen")
    for team, base in ((us, 80), (them, 75)):
        for i, position in enumerate(POSITIONS):
            upsert_player(conn, f"T{team} P{i}", position, base + (i % 4), "test", team_id=team,
                          age=20 + i,
                          base_pace=base, base_shooting=base, base_passing=base,
                          base_dribbling=base, base_defending=base, base_physical=base)
    return us, them


def test_find_opponent_full_and_partial_names(conn):
    us, them = _squads(conn)
    assert find_opponent("how do I beat bayer 04 leverkusen?", conn, us) == them
    assert find_opponent("set up against leverkusen at home", conn, us) == them
    assert find_opponent("who should I sell this window?", conn, us) is None
    # my own team name never matches as the opponent
    assert find_opponent("is Us FC good?", conn, us) is None


def test_matchup_section_included_when_opponent_named(conn):
    us, _ = _squads(conn)
    pack = build_context("best lineup against Leverkusen?", conn, us)
    assert "matchup" in pack["sections"]
    assert "my_squad" in pack["sections"]  # 'lineup' also triggers squad
    assert pack["my_team"] == "Us FC"


def test_transfer_questions_pull_transfer_section(conn):
    us, _ = _squads(conn)
    pack = build_context("who should I sell and who should I sign?", conn, us)
    transfers = pack["sections"]["transfers"]
    assert set(transfers) == {"transfer_targets_in", "academy_prospects", "surplus_transfers_out"}


def test_generic_question_still_gets_squad_and_all_explainers(conn):
    us, _ = _squads(conn)
    pack = build_context("hello, what can you do?", conn, us)
    assert "my_squad" in pack["sections"]  # default grounding
    assert set(pack["explainers"]) == set(EXPLAINERS)  # no keyword hits -> all docs


def test_explainer_routing_narrows_by_topic():
    hits = relevant_explainers("what does xpts mean?")
    assert "xpts" in hits and "matchup" not in hits


def test_build_messages_shape_and_history_cap(conn):
    us, _ = _squads(conn)
    history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
    messages, pack = build_messages("pick my squad", conn, us, history)
    assert messages[0]["role"] == "system"
    assert "CONTEXT DATA" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "pick my squad"}
    assert len(messages) == 1 + 6 + 1  # system + capped history + question
    assert "my_squad" in pack["sections"]
