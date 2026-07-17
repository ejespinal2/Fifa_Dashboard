from fifa_analytics.model.features import (
    OVERALL_WEIGHTS,
    perf_score_to_rating,
    position_group,
    score_match_performance,
)

# The exact stats from the first verified real-screenshot run (Tchouaméni,
# 46 minutes vs Leverkusen).
REAL_MATCH_STATS = {
    "goals": 0.0,
    "assists": 0.0,
    "shots": 0.0,
    "shot_accuracy_pct": 0.0,
    "passes": 8.0,
    "pass_accuracy_pct": 100.0,
    "dribbles": 6.0,
    "dribble_success_rate_pct": 100.0,
    "tackles": 1.0,
    "tackle_success_rate_pct": 100.0,
    "offsides": 0.0,
    "fouls_committed": 0.0,
    "possession_won": 4.0,
    "possession_lost": 0.0,
    "minutes_played_vs_team_avg": 46.0,
    "distance_covered_vs_team_avg_km": 5.3,
    "distance_sprinted_vs_team_avg_km": 1.8,
    "match_rating": 7.5,
}


def test_all_position_groups_weights_sum_to_one():
    for group, weights in OVERALL_WEIGHTS.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, group


def test_position_group_mapping():
    assert position_group("LDM") == "DM"
    assert position_group("st") == "ST"
    assert position_group("SUB") == "GEN"
    assert position_group(None) == "GEN"
    assert position_group("GK") == "GK"


def test_no_shooting_evidence_when_no_shots_taken():
    scores = score_match_performance(REAL_MATCH_STATS)
    assert "shooting" not in scores  # 0 shots -> no evidence, not a bad score


def test_real_match_produces_expected_evidence():
    scores = score_match_performance(REAL_MATCH_STATS)
    # Everything except shooting had actions behind it this match
    assert set(scores) == {"pace", "passing", "dribbling", "defending", "physical"}
    for attr, score in scores.items():
        assert 0.0 <= score <= 1.0, (attr, score)
    # Perfect success rates + a 7.5 rating should score clearly above midpoint
    assert scores["dribbling"] > 0.5
    assert scores["defending"] > 0.4


def test_zero_minutes_produces_no_evidence():
    assert score_match_performance({**REAL_MATCH_STATS, "minutes_played_vs_team_avg": 0.0}) == {}


def test_missing_ocr_values_are_tolerated():
    stats = dict(REAL_MATCH_STATS)
    stats["dribble_success_rate_pct"] = None  # a failed OCR read
    scores = score_match_performance(stats)
    assert 0.0 <= scores["dribbling"] <= 1.0


def test_perf_rating_scale_bounds():
    assert perf_score_to_rating(0.0) == 45.0
    assert perf_score_to_rating(1.0) == 100.0
    assert perf_score_to_rating(2.0) == 100.0  # clamped
