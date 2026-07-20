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


def test_classify_substitution_green_arrow():
    # EA's sub icon is a green+red arrow pair; green must win even though
    # red pixels are present (otherwise subs would misread as red cards).
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[5:15, 2:9] = (0, 200, 0)     # green arrow (BGR)
    crop[5:15, 11:18] = (0, 0, 220)   # red arrow
    assert classify_event_icon(crop) == "substitution"


def test_red_card_without_green_still_red():
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[4:16, 6:14] = (0, 0, 230)
    assert classify_event_icon(crop) == "red_card"


def test_classify_missed_penalty_ball_with_red_x():
    # White ball with a red X stroke through it -- white dominant plus a
    # thinner-but-real amount of red.
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[4:16, 4:16] = (235, 235, 235)          # ball
    for i in range(12):                          # the X, two diagonal strokes
        crop[4 + i, 4 + i] = (0, 0, 230)
        crop[4 + i, 15 - i] = (0, 0, 230)
    assert classify_event_icon(crop) == "missed_penalty"


def test_plain_white_ball_still_goal_not_missed_penalty():
    crop = np.full((20, 20, 3), (20, 15, 10), dtype=np.uint8)
    crop[4:16, 4:16] = (235, 235, 235)
    assert classify_event_icon(crop) == "goal"


def test_classify_missed_penalty_white_x_variant_by_shape():
    # The real screenshots' missed-pen icon is ALL white (ball + X glyph
    # beside it) -- color can't separate it from a goal ball, the wide
    # white blob can.
    crop = np.full((24, 44, 3), (20, 15, 10), dtype=np.uint8)
    crop[6:18, 26:38] = (235, 235, 235)      # the ball
    for i in range(12):                       # the X beside it
        crop[6 + i, 6 + i] = (235, 235, 235)
        crop[6 + i, 17 - i] = (235, 235, 235)
    assert classify_event_icon(crop) == "missed_penalty"


def _ball_with_glyph(draw_glyph):
    """Dark crop with a white ball on the right and a white glyph drawn by
    draw_glyph(crop, x0, x1, y0, y1) on the left -- the penalty icon shape."""
    import cv2 as _cv2
    crop = np.full((28, 52, 3), (20, 15, 10), dtype=np.uint8)
    _cv2.circle(crop, (38, 14), 9, (235, 235, 235), -1)   # the ball
    draw_glyph(crop, 6, 24, 5, 23)
    return crop


def test_classify_penalty_missed_white_x():
    import cv2 as _cv2

    def draw_x(crop, x0, x1, y0, y1):
        _cv2.line(crop, (x0, y0), (x1, y1), (235, 235, 235), 3)
        _cv2.line(crop, (x0, y1), (x1, y0), (235, 235, 235), 3)

    assert classify_event_icon(_ball_with_glyph(draw_x)) == "missed_penalty"


def test_classify_penalty_converted_white_check():
    import cv2 as _cv2

    def draw_check(crop, x0, x1, y0, y1):
        vertex_x = x0 + (x1 - x0) // 3
        _cv2.line(crop, (x0, (y0 + y1) // 2), (vertex_x, y1), (235, 235, 235), 3)
        _cv2.line(crop, (vertex_x, y1), (x1, y0), (235, 235, 235), 3)

    assert classify_event_icon(_ball_with_glyph(draw_check)) == "penalty_goal"
