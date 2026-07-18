from fifa_analytics.analysis.xpts import expected_points, match_probabilities


def test_probabilities_sum_to_one():
    # Tolerance reflects the deliberate MAX_GOALS=10 truncation: the
    # untallied P(either side scores >10) is ~1e-6 at realistic xG values.
    p_home, p_draw, p_away = match_probabilities(1.6, 0.3)
    assert abs((p_home + p_draw + p_away) - 1.0) < 1e-4


def test_dominant_xg_gives_high_win_probability():
    p_home, _, p_away = match_probabilities(1.6, 0.3)  # the real captured match
    assert p_home > 0.6
    assert p_away < 0.1


def test_equal_xg_is_symmetric():
    p_home, p_draw, p_away = match_probabilities(1.2, 1.2)
    assert abs(p_home - p_away) < 1e-9
    assert p_draw > 0.2


def test_expected_points_bounds_and_ordering():
    strong = expected_points(2.5, 0.4)
    weak = expected_points(0.4, 2.5)
    assert 0.0 < weak < 1.0 < strong < 3.0
    # The two sides' xPTS can't exceed 3 combined (draws split 1+1 < 3)
    assert strong + weak <= 3.0


def test_zero_xg_both_sides_is_certain_draw():
    p_home, p_draw, p_away = match_probabilities(0.0, 0.0)
    assert p_draw == 1.0
    assert expected_points(0.0, 0.0) == 1.0
