import numpy as np
import pytest

from fifa_analytics.ocr.classify_screen import (
    HEADER_STRIP,
    decide_player_screen,
    decide_team_screen,
)

try:
    import cv2
except ImportError:
    cv2 = None


def test_gk_probe_text_wins():
    assert decide_player_screen("Goalkeeper Rating: 5.9") == "player_gk"
    assert decide_player_screen("goalkeeping") == "player_gk"


def test_gk_probe_text_absent_means_player_summary():
    assert decide_player_screen("") == "player_summary"
    assert decide_player_screen("some other stat label") == "player_summary"


def test_possession_threat_screen_is_unsupported():
    # exactly what the user's Possession-tab screenshots contain
    lines = ["Threat", "00:00", "32% Overall Possession", "45:00", "OVERALL"]
    assert decide_team_screen(lines) == "unsupported"


def test_stat_labels_mean_team_summary():
    lines = ["32 Possession % 68", "10 Shots 12", "1.8 Expected Goals 0.9", "400 Passes 620"]
    assert decide_team_screen(lines) == "team_summary"


def test_player_minute_rows_mean_team_events():
    lines = ["Events", "B. Fernandes 37'", "Casemiro 55"]
    assert decide_team_screen(lines) == "team_events"


def test_garbage_is_unsupported_not_misrouted():
    assert decide_team_screen(["random words", "no structure"]) == "unsupported"


def test_spine_layout_event_rows_classify_as_events():
    # real Events-tab lines: minute mid-line, names either side
    lines = ["J. Cardoso 65' P. Dorgu", "65' D. Spence", "HT"]
    assert decide_team_screen(lines) == "team_events"


def test_goalkeeping_labels_win_even_with_a_flaky_header_read():
    # classify_screenshot only reaches decide_team_screen when the header
    # probe missed "player" -- its own gk-marker fallback covers that case
    lines = ["Goalkeeper Rating: 5.9", "Shots Against 11", "Save Success Rate (%) 67"]
    assert decide_team_screen(lines) == "player_gk"


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_classify_screenshot_routes_by_real_header_text(monkeypatch):
    """End-to-end through classify_screenshot: stub the two OCR probes it
    makes (header strip, then either the GK probe or the body band) and
    confirm it reads the right crop and dispatches to the right decide_*."""
    from fifa_analytics.ocr import classify_screen

    calls = []

    def fake_read_text(crop):
        calls.append(crop.shape)
        return ("PLAYER PERFORMANCE", 0.9) if len(calls) == 1 else ("Goalkeeper Rating: 5.9", 0.9)

    monkeypatch.setattr(classify_screen, "read_text", fake_read_text)
    image = np.zeros((1000, 1800, 3), dtype=np.uint8)
    assert classify_screen.classify_screenshot(image) == "player_gk"
    assert len(calls) == 2  # header probe, then the GK probe -- no body-band read at all


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_shrink_downscales_wide_crops_for_cheap_classification():
    from fifa_analytics.ocr.classify_screen import MAX_CLASSIFY_WIDTH, _shrink

    wide = np.zeros((400, 3000, 3), dtype=np.uint8)
    shrunk = _shrink(wide)
    assert shrunk.shape[1] == MAX_CLASSIFY_WIDTH

    narrow = np.zeros((400, 500, 3), dtype=np.uint8)
    assert _shrink(narrow).shape == narrow.shape  # already under the cap -- untouched


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_team_screen_body_band_is_not_downscaled(monkeypatch):
    """Regression test: downscaling the body band before OCR (once shipped
    for speed) silently broke Events-tab detection -- the minute is small
    text in a small circle, and halving its resolution pushed it below
    EasyOCR's detection floor, so every event row fell through to
    'unsupported'. Team screens are only a handful per match (the 40-image
    cost was from player screens), so there's no reason to downscale this
    one -- confirm classify_screenshot passes read_lines the FULL-size body
    crop, not a shrunk one."""
    from fifa_analytics.ocr import classify_screen

    monkeypatch.setattr(classify_screen, "read_text", lambda crop: ("TEAM 1 : 0 TEAM", 0.9))
    seen_shapes = []

    def fake_read_lines(crop):
        seen_shapes.append(crop.shape)
        return [{"text": "65' B. Fernandes", "confidence": 0.9, "y_top": 0.1, "y_bottom": 0.15}]

    monkeypatch.setattr(classify_screen, "read_lines", fake_read_lines)
    image = np.zeros((1125, 2000, 3), dtype=np.uint8)  # a real capture's resolution

    result = classify_screen.classify_screenshot(image)

    assert result == "team_events"
    expected_body_width = int(2000 * (classify_screen.BODY_BAND[2] - classify_screen.BODY_BAND[0]))
    assert seen_shapes[0][1] == expected_body_width  # NOT capped to MAX_CLASSIFY_WIDTH
