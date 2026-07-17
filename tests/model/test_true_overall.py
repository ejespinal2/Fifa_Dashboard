from fifa_analytics.model.features import PERF_RATING_FLOOR, PERF_RATING_SPAN
from fifa_analytics.model.true_overall import compute_player_history
from tests.model.test_features import REAL_MATCH_STATS


class FakePlayerRow(dict):
    def __getitem__(self, key):
        return dict.get(self, key)


TCHOUAMENI = FakePlayerRow(
    player_id=54,
    name="A. Tchouaméni",
    position="LDM",
    base_pace=70,
    base_shooting=72,
    base_passing=79,
    base_dribbling=78,
    base_defending=87,
    base_physical=85,
)

REGEN = FakePlayerRow(
    player_id=99,
    name="Some Academy Kid",
    position="UNK",
    base_pace=None,
    base_shooting=None,
    base_passing=None,
    base_dribbling=None,
    base_defending=None,
    base_physical=None,
)


def test_single_match_barely_moves_off_the_prior():
    history = compute_player_history(TCHOUAMENI, [(1, REAL_MATCH_STATS)])
    assert len(history) == 1
    entry = history[0]
    # 46 minutes of evidence vs PRIOR_STRENGTH=5 full matches: the estimate
    # must stay close to the card values -- the cold-start guarantee.
    assert abs(entry["true_defending"] - 87) < 4
    assert abs(entry["true_passing"] - 79) < 4
    # No shooting evidence at all -> shooting holds the card value exactly
    assert entry["true_shooting"] == 72
    assert 0 < entry["confidence_score"] < 0.15


def test_repeated_strong_matches_pull_estimate_toward_performance():
    strong = {**REAL_MATCH_STATS, "minutes_played_vs_team_avg": 90.0, "match_rating": 9.0}
    many = [(i, strong) for i in range(1, 21)]
    history = compute_player_history(TCHOUAMENI, many)
    first, last = history[0], history[-1]
    # Confidence grows with accumulated minutes
    assert last["confidence_score"] > first["confidence_score"]
    # 20 full strong matches: pace (card 70) should be pulled up measurably
    assert last["true_pace"] > first["true_pace"]
    assert last["true_pace"] > 75


def test_regen_uses_pure_performance_estimate():
    history = compute_player_history(REGEN, [(1, REAL_MATCH_STATS)])
    entry = history[0]
    floor, ceiling = PERF_RATING_FLOOR, PERF_RATING_FLOOR + PERF_RATING_SPAN
    # Attributes with evidence: pure performance scale, no prior to blend
    assert floor <= entry["true_dribbling"] <= ceiling
    # No shooting evidence and no card value -> midpoint placeholder
    assert entry["true_shooting"] == floor + PERF_RATING_SPAN / 2


def test_history_is_one_row_per_match_in_order():
    matches = [(3, REAL_MATCH_STATS), (7, REAL_MATCH_STATS)]
    history = compute_player_history(TCHOUAMENI, matches)
    assert [h["match_id"] for h in history] == [3, 7]
    # Second match adds evidence, so confidence must not decrease
    assert history[1]["confidence_score"] >= history[0]["confidence_score"]
