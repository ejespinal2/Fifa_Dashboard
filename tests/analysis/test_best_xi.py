import pytest

from fifa_analytics.analysis.best_xi import SquadPlayer, best_formation, pick_best_xi
from fifa_analytics.analysis.formations import FAMILIARITY, FORMATIONS


def _squad():
    """A tidy 4-3-3-shaped squad plus backups, with one obvious superstar."""
    players = [
        ("GK1", "GK", 85), ("GK2", "GK", 75),
        ("CB1", "CB", 84), ("CB2", "CB", 82), ("CB3", "CB", 76),
        ("FB1", "FB", 80), ("FB2", "FB", 79), ("FB3", "FB", 72),
        ("DM1", "DM", 83),
        ("CM1", "CM", 86), ("CM2", "CM", 81), ("CM3", "CM", 74),
        ("W1", "W", 88), ("W2", "W", 84),
        ("ST1", "ST", 90), ("ST2", "ST", 77),
    ]
    return [SquadPlayer(i, name, group, float(r), "card") for i, (name, group, r) in enumerate(players)]


def test_all_formations_have_eleven_slots_and_one_gk():
    for name, slots in FORMATIONS.items():
        assert len(slots) == 11, name
        assert slots.count("GK") == 1, name


def test_familiarity_rows_have_natural_position_at_one():
    for slot, row in FAMILIARITY.items():
        assert row[slot] == 1.0, slot


def test_best_xi_uses_natural_positions_when_available():
    assignments, _ = pick_best_xi(_squad(), "4-3-3")
    assert len(assignments) == 11
    # No player doubles up
    assert len({a.player.player_id for a in assignments}) == 11
    by_slot = {}
    for a in assignments:
        by_slot.setdefault(a.slot_group, []).append(a.player.name)
    # The best GK plays, not the backup
    assert by_slot["GK"] == ["GK1"]
    # The superstar striker is in the team
    all_names = {a.player.name for a in assignments}
    assert "ST1" in all_names and "W1" in all_names


def test_gk_never_assigned_outfield_when_alternatives_exist():
    assignments, _ = pick_best_xi(_squad(), "4-4-2")
    for a in assignments:
        if a.player.group == "GK":
            assert a.slot_group == "GK"


def test_too_few_players_raises():
    with pytest.raises(ValueError):
        pick_best_xi(_squad()[:5], "4-3-3")


def test_best_formation_returns_a_valid_formation():
    name, assignments, total = best_formation(_squad())
    assert name in FORMATIONS
    assert len(assignments) == 11
    assert total > 0
