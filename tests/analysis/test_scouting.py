import pytest

from fifa_analytics.analysis.scouting import academy_prospects, identify_weak_slots, transfer_targets
from fifa_analytics.db.models import (
    connect,
    get_or_create_team,
    init_db,
    upsert_player,
    upsert_scouting_candidate,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def _weak_squad(conn, team_id):
    """A full 4-3-3-shaped squad where the CB slot is deliberately the
    weakest, so transfer_targets has a clear position to search for."""
    players = [
        ("GK1", "GK", 80), ("CB1", "CB", 65), ("CB2", "CB", 64),
        ("FB1", "LB", 78), ("FB2", "RB", 76),
        ("DM1", "CDM", 79), ("CM1", "CM", 82), ("CM2", "CM", 80),
        ("W1", "LW", 84), ("W2", "RW", 83), ("ST1", "ST", 85),
    ]
    for name, position, overall in players:
        upsert_player(conn, name, position, overall, "test", team_id=team_id,
                       base_pace=overall, base_shooting=overall, base_passing=overall,
                       base_dribbling=overall, base_defending=overall, base_physical=overall)


def test_identify_weak_slots_puts_cb_first(db_path):
    conn = connect(db_path)
    team_id = get_or_create_team(conn, "Test FC")
    _weak_squad(conn, team_id)
    weak = identify_weak_slots(conn, team_id, "4-3-3")
    conn.close()
    assert weak[0].slot_group == "CB"


def test_transfer_targets_only_returns_actual_upgrades(db_path):
    conn = connect(db_path)
    team_id = get_or_create_team(conn, "Test FC")
    _weak_squad(conn, team_id)

    # A CB clearly better than our weakest (64/65) starters
    upsert_scouting_candidate(
        conn, "Great CB", "Rival FC", "scouting", "CB", 26, 88, 88,
        base_pace=75, base_shooting=40, base_passing=70,
        base_dribbling=60, base_defending=90, base_physical=85, estimated_wage=None,
    )
    # A CB worse than our current starters -- must NOT show up as a target
    upsert_scouting_candidate(
        conn, "Worse CB", "Rival FC", "scouting", "CB", 26, 55, 55,
        base_pace=50, base_shooting=30, base_passing=50,
        base_dribbling=45, base_defending=55, base_physical=60, estimated_wage=None,
    )
    # A striker -- must never be suggested for the CB slot regardless of rating
    upsert_scouting_candidate(
        conn, "Great ST", "Rival FC", "scouting", "ST", 26, 90, 90,
        base_pace=90, base_shooting=90, base_passing=70,
        base_dribbling=85, base_defending=20, base_physical=80, estimated_wage=None,
    )

    targets = transfer_targets(conn, team_id, "4-3-3")
    conn.close()

    assert "CB" in targets
    names = {t["name"] for t in targets["CB"]}
    assert "Great CB" in names
    assert "Worse CB" not in names
    assert "Great ST" not in names


def test_academy_prospects_requires_growth_room_and_floor(db_path):
    conn = connect(db_path)
    # High potential, decent floor -- should qualify
    upsert_scouting_candidate(
        conn, "Prospect A", "Academy Feeder", "scouting", "CM", 18, 65, 84,
        base_pace=70, base_shooting=60, base_passing=68, base_dribbling=70,
        base_defending=55, base_physical=60, estimated_wage=None,
    )
    # High potential but below the "won't hurt the team" floor
    upsert_scouting_candidate(
        conn, "Too Raw", "Academy Feeder", "scouting", "CM", 17, 45, 85,
        base_pace=60, base_shooting=40, base_passing=45, base_dribbling=50,
        base_defending=35, base_physical=45, estimated_wage=None,
    )
    # Good floor but no real growth room left
    upsert_scouting_candidate(
        conn, "Plateaued", "Academy Feeder", "scouting", "CM", 20, 78, 80,
        base_pace=75, base_shooting=70, base_passing=78, base_dribbling=75,
        base_defending=70, base_physical=72, estimated_wage=None,
    )
    # Too old even with great potential
    upsert_scouting_candidate(
        conn, "Too Old", "Academy Feeder", "scouting", "CM", 26, 65, 84,
        base_pace=70, base_shooting=60, base_passing=68, base_dribbling=70,
        base_defending=55, base_physical=60, estimated_wage=None,
    )

    prospects = academy_prospects(conn, min_potential_gap=8, max_age=21)
    conn.close()

    names = {p["name"] for p in prospects}
    assert names == {"Prospect A"}


def test_surplus_players_split_sell_vs_develop(db_path):
    from fifa_analytics.analysis.scouting import surplus_players
    conn = connect(db_path)
    team_id = get_or_create_team(conn, "Test FC")
    _weak_squad(conn, team_id)
    # Old bench CM far below the starters -- sale candidate
    upsert_player(conn, "Old Bench CM", "CM", 70, "test", team_id=team_id, age=30,
                  base_pace=70, base_shooting=70, base_passing=70,
                  base_dribbling=70, base_defending=70, base_physical=70)
    # Young bench CM with the same gap -- loan/develop, not sell
    upsert_player(conn, "Young Bench CM", "CM", 70, "test", team_id=team_id, age=19,
                  base_pace=70, base_shooting=70, base_passing=70,
                  base_dribbling=70, base_defending=70, base_physical=70)
    # Bench winger just under the starter -- healthy depth, not surplus
    upsert_player(conn, "Solid Backup W", "LW", 81, "test", team_id=team_id, age=27,
                  base_pace=81, base_shooting=81, base_passing=81,
                  base_dribbling=81, base_defending=81, base_physical=81)

    surplus = surplus_players(conn, team_id, "4-3-3")
    conn.close()

    verdicts = {p["player"]: p["verdict"] for p in surplus}
    assert verdicts["Old Bench CM"] == "sale_candidate"
    assert verdicts["Young Bench CM"] == "loan_or_develop"
    assert "Solid Backup W" not in verdicts
