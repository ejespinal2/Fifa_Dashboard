import pytest

from fifa_analytics.analysis.matchup import compare_teams
from fifa_analytics.db.models import connect, get_or_create_team, init_db, upsert_player

POSITIONS = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "LW", "RW", "ST"]


def _squad(conn, team_id, base):
    for i, position in enumerate(POSITIONS):
        upsert_player(conn, f"T{team_id} P{i}", position, base + (i % 3), "test", team_id=team_id,
                      base_pace=base, base_shooting=base, base_passing=base,
                      base_dribbling=base, base_defending=base, base_physical=base)


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    c = connect(path)
    yield c
    c.close()


def test_compare_teams_unit_deltas_favor_stronger_side(conn):
    us = get_or_create_team(conn, "Strong FC")
    them = get_or_create_team(conn, "Weak FC")
    _squad(conn, us, 85)
    _squad(conn, them, 70)

    result = compare_teams(conn, us, them)
    assert result["mine"]["total_effective"] > result["theirs"]["total_effective"]
    for unit, delta in result["unit_avg_delta_mine_minus_theirs"].items():
        assert delta > 0, unit
    assert len(result["their_biggest_threats"]) == 3
    assert len(result["my_weakest_slots"]) == 3
    # every XI entry says which rating fed it (true vs card)
    assert all(p["rating_source"] == "card_only" for p in result["mine"]["xi"])


def test_compare_teams_requires_full_squads(conn):
    us = get_or_create_team(conn, "Full FC")
    them = get_or_create_team(conn, "Tiny FC")
    _squad(conn, us, 80)
    upsert_player(conn, "Lonely", "ST", 90, "test", team_id=them)

    with pytest.raises(ValueError, match="at least 11"):
        compare_teams(conn, us, them)
