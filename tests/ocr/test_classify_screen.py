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
