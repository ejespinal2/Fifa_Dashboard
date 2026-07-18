from fifa_analytics.analysis.tactics import TACTIC_ADJUSTMENTS, attribute_composite, tactical_weights
from fifa_analytics.model.features import ATTRIBUTES, OVERALL_WEIGHTS

STRIKER_ATTRS = {"pace": 80, "shooting": 85, "passing": 60, "dribbling": 75, "defending": 30, "physical": 70}


def test_all_tactic_weights_sum_to_one():
    for tactic in TACTIC_ADJUSTMENTS:
        for group in OVERALL_WEIGHTS:
            weights = tactical_weights(group, tactic)
            assert abs(sum(weights.values()) - 1.0) < 1e-9, (tactic, group)


def test_balanced_tactic_matches_plain_position_weights():
    # "balanced" applies a no-op adjustment then renormalizes -- compares
    # approximately since dividing-by-sum-of-itself isn't bit-exact.
    for group in OVERALL_WEIGHTS:
        weights = tactical_weights(group, "balanced")
        for attr in ATTRIBUTES:
            assert abs(weights[attr] - OVERALL_WEIGHTS[group][attr]) < 1e-9


def test_possession_tactic_upweights_passing_for_a_striker():
    balanced = tactical_weights("ST", "balanced")
    possession = tactical_weights("ST", "possession")
    assert possession["passing"] > balanced["passing"]


def test_direct_play_upweights_physical_over_dribbling():
    direct = tactical_weights("W", "direct_play")
    balanced = tactical_weights("W", "balanced")
    assert direct["physical"] > balanced["physical"]
    assert direct["dribbling"] < balanced["dribbling"]


def test_composite_score_is_plausible_for_a_striker():
    score = attribute_composite(STRIKER_ATTRS, "ST", "balanced")
    assert 60 < score < 80


def test_composite_returns_none_on_missing_attribute():
    incomplete = dict(STRIKER_ATTRS)
    incomplete["passing"] = None
    assert attribute_composite(incomplete, "ST") is None


def test_unknown_tactic_falls_back_to_balanced():
    assert tactical_weights("CM", "nonexistent-tactic") == OVERALL_WEIGHTS["CM"]
