import numpy as np

from fifa_analytics.ocr.event_parse import classify_event_icon, parse_event_text


def _solid_patch(bgr):
    return np.full((20, 20, 3), bgr, dtype=np.uint8)


def test_parse_event_text_with_trailing_apostrophe():
    name, minute = parse_event_text("B. Fernandes 37'")
    assert name == "B. Fernandes"
    assert minute == 37


def test_parse_event_text_without_apostrophe():
    name, minute = parse_event_text("B. Fernandes 37")
    assert name == "B. Fernandes"
    assert minute == 37


def test_parse_event_text_no_minute_found():
    assert parse_event_text("garbled ocr text") == (None, None)


def test_parse_event_text_empty():
    assert parse_event_text("") == (None, None)


def test_classify_goal_icon_is_grayscale():
    assert classify_event_icon(_solid_patch((180, 180, 180))) == "goal"


def test_classify_yellow_card():
    assert classify_event_icon(_solid_patch((0, 255, 255))) == "yellow_card"


def test_classify_red_card():
    assert classify_event_icon(_solid_patch((0, 0, 255))) == "red_card"


def test_classify_all_dark_background_is_unknown():
    # Nothing bright enough to be an icon -- just the dark panel background.
    assert classify_event_icon(_solid_patch((20, 15, 10))) == "unknown"


def test_classify_empty_crop_is_unknown():
    assert classify_event_icon(np.zeros((0, 0, 3), dtype=np.uint8)) == "unknown"


def test_classify_small_white_icon_on_noisy_dark_background():
    # Simulates the real failure: mostly dim background with a white ball
    # icon occupying ~10% of the crop. The old mean-color approach returned
    # "unknown" here; pixel-class counting should see the ball.
    crop = np.random.randint(20, 90, size=(30, 30, 3), dtype=np.uint8)
    crop[10:19, 10:20] = (230, 230, 230)
    assert classify_event_icon(crop) == "goal"


# classify_event_icon never returns "substitution" -- real screenshots showed
# the sub "icon" is actually a pair of tiny chevrons printed next to the
# NAMES, not anything sitting in the goal/card icon zone at all. Subs are
# detected structurally (a hanging name below the row -- see
# event_parse.parse_event_rows and pipeline.process_team_events), never by
# icon color, so this function only ever needs to tell goal/penalty/card
# icons apart.


def test_red_card_is_detected():
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[4:16, 6:14] = (0, 0, 230)
    assert classify_event_icon(crop) == "red_card"


def _ball_with_dark_mark(mark_pixels):
    """A same-sized white ball icon (14x14, matching a real goal-ball's
    proportions) with `mark_pixels` (row, col) set to near-black, simulating
    the extra ink a penalty mark adds INSIDE the ball itself -- not a
    separate wider glyph beside it (that was the wrong shape assumption;
    see event_parse.py's module docstring)."""
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[3:17, 3:17] = (235, 235, 235)  # the ball, 14x14
    for row, col in mark_pixels:
        crop[row, col] = (10, 10, 10)
    return crop


def test_plain_ball_with_light_stitching_is_still_goal():
    # A few dark pixels (normal ball stitching/pentagon detail) stay under
    # the penalty-mark threshold.
    stitching = [(5, 5), (5, 6), (10, 10), (14, 8)]
    assert classify_event_icon(_ball_with_dark_mark(stitching)) == "goal"


def test_symmetric_x_mark_inside_ball_is_missed_penalty():
    # An X: both diagonals, hitting all four quadrants roughly symmetrically
    # -- enough dark ink to clear the penalty-mark threshold, but no
    # top-left/top-right asymmetry, so it defaults to missed_penalty.
    mark = [(3 + i, 3 + i) for i in range(14)] + [(3 + i, 16 - i) for i in range(14)]
    assert classify_event_icon(_ball_with_dark_mark(mark)) == "missed_penalty"


def test_check_mark_inside_ball_is_penalty_goal():
    # A check: dark ink confined to the right half (short stroke bottom-left
    # rising to a vertex, then a long, THICK stroke up through the
    # top-right), leaving the top-left quadrant clear -- the
    # converted-penalty shape. Thickened (each point plus its neighbor)
    # so the mark clears the minimum-dark-area threshold.
    mark = []
    for i in range(5):  # short stroke, lower-left rising to the vertex
        mark += [(9 + i, 8 + i), (9 + i, 9 + i)]
    for i in range(11):  # long stroke, rising into the top-right corner
        col = 13 - i // 2
        mark += [(3 + i, col), (3 + i, col + 1)]
    assert classify_event_icon(_ball_with_dark_mark(mark)) == "penalty_goal"
