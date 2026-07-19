"""Integration test for multi-event team_events processing: a synthetic
events screenshot with three rows (goal / yellow card / substitution),
icons drawn at each row's height in the real icon column. OCR text is
stubbed (EasyOCR's accuracy is its own concern); everything else — line
loop, roster matching, per-row icon geometry + color classification,
cross-screenshot dedupe — runs for real on real pixels."""

import numpy as np
import pytest

try:
    import cv2
except ImportError:
    cv2 = None

from fifa_analytics.db.models import (
    connect,
    create_match,
    get_or_create_season,
    get_or_create_team,
    init_db,
    players_for_teams,
    upsert_player,
)
from fifa_analytics.ocr import pipeline, regions

pytestmark = pytest.mark.skipif(cv2 is None, reason="cv2 not installed")

WIDTH, HEIGHT = 1920, 1080
BAND = regions.TEAM_EVENTS_REGIONS["event_band"]
ICON_X0, ICON_X1 = regions.TEAM_EVENTS_ICON_COLUMN

# three event rows at these fractions of the band's height
ROWS = [
    ("Bruno Fernandes 37", 0.10, 0.16, "goal"),
    ("Casemiro 55", 0.40, 0.46, "yellow_card"),
    ("Kobbie Mainoo 60", 0.70, 0.76, "substitution"),
]


def _synthetic_events_image(path):
    image = np.full((HEIGHT, WIDTH, 3), (25, 18, 12), dtype=np.uint8)  # dark panel
    band_y0, band_y1 = BAND[1] * HEIGHT, BAND[3] * HEIGHT
    icon_x0, icon_x1 = int(ICON_X0 * WIDTH), int(ICON_X1 * WIDTH)
    for _, y_top, y_bottom, kind in ROWS:
        row_y0 = int(band_y0 + y_top * (band_y1 - band_y0))
        row_y1 = int(band_y0 + y_bottom * (band_y1 - band_y0))
        pad_x = (icon_x1 - icon_x0) // 4
        if kind == "goal":
            cv2.circle(image, ((icon_x0 + icon_x1) // 2, (row_y0 + row_y1) // 2),
                       (row_y1 - row_y0) // 3, (235, 235, 235), -1)
        elif kind == "yellow_card":
            cv2.rectangle(image, (icon_x0 + pad_x, row_y0), (icon_x1 - pad_x, row_y1), (0, 220, 255), -1)
        elif kind == "substitution":
            mid_x = (icon_x0 + icon_x1) // 2
            cv2.rectangle(image, (icon_x0 + 2, row_y0), (mid_x - 2, row_y1), (0, 200, 0), -1)
            cv2.rectangle(image, (mid_x + 2, row_y0), (icon_x1 - 2, row_y1), (0, 0, 220), -1)
    cv2.imwrite(str(path), image)


def _fake_read_lines(crop):
    return [
        {"text": text, "confidence": 0.95, "y_top": y_top, "y_bottom": y_bottom}
        for text, y_top, y_bottom, _ in ROWS
    ]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    us = get_or_create_team(conn, "Manchester United")
    them = get_or_create_team(conn, "Bayer 04 Leverkusen")
    upsert_player(conn, "Bruno Fernandes", "CAM", 87, "test", team_id=us)
    upsert_player(conn, "Casemiro", "CDM", 80, "test", team_id=us)
    upsert_player(conn, "Kobbie Mainoo", "CM", 78, "test", team_id=us)
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, us, them, "dir")
    yield conn, match, us, them
    conn.close()


def test_three_rows_parse_with_correct_types_and_minutes(db, tmp_path, monkeypatch):
    conn, match, us, _ = db
    image_path = tmp_path / "team_events.png"
    _synthetic_events_image(image_path)
    monkeypatch.setattr(pipeline, "read_lines", _fake_read_lines)

    candidates = players_for_teams(conn, [us])
    _, stored = pipeline.process_team_events(conn, match, str(image_path), candidates)

    assert [(e["minute"], e["event_type"]) for e in stored] == [
        (37, "goal"), (55, "yellow_card"), (60, "substitution"),
    ]
    assert all(e["team_id"] == us for e in stored)


def test_overlapping_scrolled_screenshot_dedupes(db, tmp_path, monkeypatch):
    conn, match, us, _ = db
    first = tmp_path / "team_events.png"
    second = tmp_path / "team_events_2.png"
    _synthetic_events_image(first)
    _synthetic_events_image(second)  # identical = full overlap
    monkeypatch.setattr(pipeline, "read_lines", _fake_read_lines)

    candidates = players_for_teams(conn, [us])
    _, stored_1 = pipeline.process_team_events(conn, match, str(first), candidates)
    _, stored_2 = pipeline.process_team_events(conn, match, str(second), candidates)

    assert len(stored_1) == 3
    assert stored_2 == []  # every row already known
    count = conn.execute("SELECT COUNT(*) FROM match_events WHERE match_id = ?", (match,)).fetchone()[0]
    assert count == 3


def test_unparseable_and_unmatched_rows_are_skipped(db, tmp_path, monkeypatch):
    conn, match, us, _ = db
    image_path = tmp_path / "team_events.png"
    _synthetic_events_image(image_path)
    monkeypatch.setattr(pipeline, "read_lines", lambda crop: [
        {"text": "Events", "confidence": 0.9, "y_top": 0.0, "y_bottom": 0.05},          # header, no minute
        {"text": "Bruno Fernandes 37", "confidence": 0.9, "y_top": 0.10, "y_bottom": 0.16},
        {"text": "Nobody Nowhere 88", "confidence": 0.9, "y_top": 0.40, "y_bottom": 0.46},  # not on a roster
    ])

    candidates = players_for_teams(conn, [us])
    _, stored = pipeline.process_team_events(conn, match, str(image_path), candidates)
    assert len(stored) == 1
    assert stored[0]["minute"] == 37
