"""Opponent matchup: your best XI (true overalls where available) against
theirs, compared where formations actually allow comparison.

Slot-by-slot comparison across different formations is apples-to-oranges
(a 3-5-2 has no winger slots), so the cross-formation comparison happens at
the unit level — GK / defense / midfield / attack — where totals stay
meaningful whatever shape either side plays. The per-slot XI lists are
still returned for display and for the assistant to reason over.
"""

from fifa_analytics.analysis.best_xi import Assignment, best_formation, load_squad

UNIT_OF_GROUP = {
    "GK": "goalkeeper",
    "CB": "defense", "FB": "defense",
    "DM": "midfield", "CM": "midfield", "AM": "midfield",
    "W": "attack", "ST": "attack",
    "GEN": "midfield",
}
UNITS = ("goalkeeper", "defense", "midfield", "attack")


def _xi_payload(formation: str, assignments: list[Assignment], total: float) -> dict:
    return {
        "formation": formation,
        "total_effective": total,
        "avg_effective": round(total / len(assignments), 1),
        "xi": [
            {
                "slot": a.slot_group,
                "player": a.player.name,
                "rating": a.player.rating,
                "rating_source": "true_overall" if a.player.rating_source == "true" else "card_only",
                "effective": a.effective_rating,
            }
            for a in assignments
        ],
    }


def _unit_totals(assignments: list[Assignment]) -> dict:
    totals = {unit: 0.0 for unit in UNITS}
    counts = {unit: 0 for unit in UNITS}
    for a in assignments:
        unit = UNIT_OF_GROUP.get(a.slot_group, "midfield")
        totals[unit] += a.effective_rating
        counts[unit] += 1
    return {
        unit: {"avg": round(totals[unit] / counts[unit], 1), "players": counts[unit]}
        for unit in UNITS
        if counts[unit]
    }


def compare_teams(conn, my_team_id: int, opponent_team_id: int) -> dict:
    """Both sides' best XI (each in its own best formation), unit-level
    deltas, their biggest threats, and our weakest slots — the data pack
    behind 'how should I set up against them?'."""
    my_squad = load_squad(conn, my_team_id)
    opp_squad = load_squad(conn, opponent_team_id)
    if len(my_squad) < 11 or len(opp_squad) < 11:
        raise ValueError(
            "Both teams need at least 11 rated players for a matchup comparison "
            f"(have {len(my_squad)} vs {len(opp_squad)}) — import card data for both first."
        )

    mine = best_formation(my_squad)
    theirs = best_formation(opp_squad)
    my_units = _unit_totals(mine[1])
    opp_units = _unit_totals(theirs[1])

    unit_deltas = {
        unit: round(my_units[unit]["avg"] - opp_units[unit]["avg"], 1)
        for unit in UNITS
        if unit in my_units and unit in opp_units
    }
    return {
        "mine": _xi_payload(mine[0], mine[1], mine[2]),
        "theirs": _xi_payload(theirs[0], theirs[1], theirs[2]),
        "unit_avg_delta_mine_minus_theirs": unit_deltas,
        "their_biggest_threats": [
            {"player": a.player.name, "slot": a.slot_group, "effective": a.effective_rating}
            for a in sorted(theirs[1], key=lambda a: -a.effective_rating)[:3]
        ],
        "my_weakest_slots": [
            {"player": a.player.name, "slot": a.slot_group, "effective": a.effective_rating}
            for a in sorted(mine[1], key=lambda a: a.effective_rating)[:3]
        ],
    }
