"""Tactical fit scoring: nudges the position-based attribute weights
(model.features.OVERALL_WEIGHTS) toward what a chosen tactic demands. This
was part of the spec's Phase 3 "Team Analysis" section (§5) but wasn't
built there — best_xi.py only scored positional fit. It's needed now for
Phase 4's transfer-target scoring (§6: "positional weaknesses... and
tactical fit"), so it lives here instead.

Multipliers are directional judgment calls (a possession tactic values
passing/dribbling over physicality), not fitted to data — same spirit as
formations.py's familiarity table. Weights are renormalized after applying
a tactic's multipliers so they still sum to 1 and stay comparable to the
untactical position weights.
"""

from fifa_analytics.model.features import ATTRIBUTES, OVERALL_WEIGHTS

TACTIC_ADJUSTMENTS = {
    "balanced": {},
    "possession": {"passing": 1.3, "dribbling": 1.2, "physical": 0.8},
    "counter_attack": {"pace": 1.4, "physical": 1.1, "passing": 0.85},
    "direct_play": {"physical": 1.3, "pace": 1.2, "passing": 0.8, "dribbling": 0.85},
    "high_press": {"physical": 1.2, "defending": 1.2, "pace": 1.1, "passing": 0.9},
}


def tactical_weights(position_grp: str, tactic: str = "balanced") -> dict:
    base = OVERALL_WEIGHTS[position_grp]
    adjustment = TACTIC_ADJUSTMENTS.get(tactic, {})
    adjusted = {attr: base[attr] * adjustment.get(attr, 1.0) for attr in ATTRIBUTES}
    total = sum(adjusted.values())
    return {attr: v / total for attr, v in adjusted.items()}


def attribute_composite(attrs: dict, position_grp: str, tactic: str = "balanced") -> float | None:
    """attrs: {attribute: value or None}. Returns the tactic-weighted
    composite, or None if any of the six attributes is missing — can't
    meaningfully score a candidate with incomplete card data.
    """
    if any(attrs.get(a) is None for a in ATTRIBUTES):
        return None
    weights = tactical_weights(position_grp, tactic)
    return sum(weights[a] * attrs[a] for a in ATTRIBUTES)
