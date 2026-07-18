"""Formation definitions and positional-fit multipliers for the best-XI
solver.

Formations are expressed as 11 position-group slots (using the same groups
as model/features.py: GK/CB/FB/DM/CM/AM/W/ST). Add a formation by adding a
list here — nothing else needs to change.

FAMILIARITY[slot_group][player_group] multiplies a player's rating when
they're placed in that slot: 1.0 in their natural group, discounted as the
role gets further from home (a CM in the DM slot loses little; a striker at
center-back loses a lot). GK is near-binary in both directions. Values are
judgment calls in the spirit of EA's own out-of-position penalties, not
fitted to data — tune freely.
"""

FORMATIONS = {
    "4-3-3": ["GK", "FB", "CB", "CB", "FB", "DM", "CM", "CM", "W", "ST", "W"],
    "4-4-2": ["GK", "FB", "CB", "CB", "FB", "W", "CM", "CM", "W", "ST", "ST"],
    "4-2-3-1": ["GK", "FB", "CB", "CB", "FB", "DM", "DM", "AM", "W", "W", "ST"],
    "3-5-2": ["GK", "CB", "CB", "CB", "FB", "DM", "CM", "CM", "FB", "ST", "ST"],
}

_GK_ONLY = {"GK": 1.0, "CB": 0.05, "FB": 0.05, "DM": 0.05, "CM": 0.05,
            "AM": 0.05, "W": 0.05, "ST": 0.05, "GEN": 0.05}

FAMILIARITY = {
    "GK": _GK_ONLY,
    "CB": {"GK": 0.05, "CB": 1.00, "FB": 0.85, "DM": 0.85, "CM": 0.65, "AM": 0.55, "W": 0.55, "ST": 0.55, "GEN": 0.80},
    "FB": {"GK": 0.05, "CB": 0.85, "FB": 1.00, "DM": 0.75, "CM": 0.65, "AM": 0.60, "W": 0.80, "ST": 0.55, "GEN": 0.80},
    "DM": {"GK": 0.05, "CB": 0.85, "FB": 0.70, "DM": 1.00, "CM": 0.90, "AM": 0.70, "W": 0.60, "ST": 0.55, "GEN": 0.80},
    "CM": {"GK": 0.05, "CB": 0.65, "FB": 0.65, "DM": 0.90, "CM": 1.00, "AM": 0.90, "W": 0.70, "ST": 0.65, "GEN": 0.80},
    "AM": {"GK": 0.05, "CB": 0.55, "FB": 0.60, "DM": 0.70, "CM": 0.90, "AM": 1.00, "W": 0.85, "ST": 0.80, "GEN": 0.80},
    "W":  {"GK": 0.05, "CB": 0.55, "FB": 0.75, "DM": 0.60, "CM": 0.70, "AM": 0.85, "W": 1.00, "ST": 0.85, "GEN": 0.80},
    "ST": {"GK": 0.05, "CB": 0.55, "FB": 0.55, "DM": 0.55, "CM": 0.65, "AM": 0.80, "W": 0.85, "ST": 1.00, "GEN": 0.80},
}


def familiarity(slot_group: str, player_group: str) -> float:
    return FAMILIARITY[slot_group].get(player_group, 0.6)
