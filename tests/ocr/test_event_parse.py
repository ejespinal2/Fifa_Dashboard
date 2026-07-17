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
