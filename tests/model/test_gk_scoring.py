"""Goalkeeping-tab evidence in the true-overall model."""

from fifa_analytics.model.features import score_match_performance

OBLAK_SHEET = {  # the real Atlético match: 6 on target, 4 saves, 1 pen saved
    "gk_shots_against": 11, "gk_shots_on_target": 6, "gk_saves": 4,
    "gk_goals_conceded": 2, "gk_save_success_rate_pct": 67,
    "gk_penalty_saves": 1, "gk_cross_claim": 1,
    "goalkeeper_rating": 5.9,
}


def test_gk_stats_produce_defending_and_physical_evidence():
    scores = score_match_performance({"minutes_played_vs_team_avg": 90, **OBLAK_SHEET})
    assert "defending" in scores and "physical" in scores
    # 4/6 saves + pen kicker, pulled toward the modest 5.9 gk rating
    assert 0.4 < scores["defending"] < 0.9
    assert 0.0 < scores["physical"] < 0.5


def test_clean_sheet_with_more_saves_beats_leaky_day():
    good = score_match_performance({
        "minutes_played_vs_team_avg": 90,
        "gk_shots_on_target": 6, "gk_saves": 6, "goalkeeper_rating": 8.5,
    })
    bad = score_match_performance({
        "minutes_played_vs_team_avg": 90,
        "gk_shots_on_target": 6, "gk_saves": 2, "goalkeeper_rating": 4.0,
    })
    assert good["defending"] > bad["defending"]


def test_no_shots_faced_means_no_shotstopping_evidence():
    scores = score_match_performance({
        "minutes_played_vs_team_avg": 90,
        "gk_shots_against": 0, "gk_shots_on_target": 0, "gk_saves": 0,
    })
    assert "defending" not in scores  # quiet day isn't a bad day


def test_outfield_players_unaffected():
    scores = score_match_performance({
        "minutes_played_vs_team_avg": 90, "shots": 3, "goals": 1, "shot_accuracy_pct": 66,
    })
    assert "shooting" in scores and "defending" not in scores
