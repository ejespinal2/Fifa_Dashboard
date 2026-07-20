"""Integration test for Events-tab processing against synthetic screenshots
built to the REAL layout (calibrated from the Atlético 0:2 Man Utd
captures): minute circles on a center spine, home events left / away
events right, icons in the per-side zones, hanging outgoing-sub names.
OCR text is stubbed (EasyOCR's accuracy is its own concern); geometry,
icon color/shape classification, side->team attribution, roster matching,
and cross-screenshot dedupe all run for real on real pixels."""

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

WIDTH, HEIGHT = 2000, 1125
BAND = regions.TEAM_EVENTS_REGIONS["event_band"]

# (kind, side, minute, band y-range) — mirrors the real screenshots:
# away goal, home yellow, away missed pen (white ball + X), away sub
ROWS = [
    ("goal", "away", 41, 0.06, 0.11),
    ("yellow_card", "home", 52, 0.22, 0.27),
    ("missed_penalty", "away", 75, 0.38, 0.43),
    ("penalty_goal", "home", 58, 0.54, 0.59),
    ("substitution", "away", 65, 0.70, 0.75),
]
SUB_OFF_LINE = (0.765, 0.795)  # hanging outgoing-name line below the sub row


def _band_to_image_y(y_frac):
    return int((BAND[1] + y_frac * (BAND[3] - BAND[1])) * HEIGHT)


def _synthetic_events_image(path):
    image = np.full((HEIGHT, WIDTH, 3), (25, 18, 12), dtype=np.uint8)
    for kind, side, _, y_top, y_bottom in ROWS:
        zone_x0, zone_x1 = regions.TEAM_EVENTS_ICON_ZONES[side]
        x0, x1 = int(zone_x0 * WIDTH), int(zone_x1 * WIDTH)
        row_y0, row_y1 = _band_to_image_y(y_top), _band_to_image_y(y_bottom)
        center = ((x0 + x1) // 2, (row_y0 + row_y1) // 2)
        radius = (row_y1 - row_y0) // 3
        if kind == "goal":
            cv2.circle(image, center, radius, (235, 235, 235), -1)
        elif kind == "yellow_card":
            cv2.rectangle(image, (center[0] - radius // 2, row_y0), (center[0] + radius // 2, row_y1), (0, 220, 255), -1)
        elif kind == "missed_penalty":
            # white ball with an X glyph beside it -> one wide white blob
            cv2.circle(image, (center[0] + radius, center[1]), radius, (235, 235, 235), -1)
            x_left = center[0] - 2 * radius
            cv2.line(image, (x_left - radius, center[1] - radius), (x_left + radius, center[1] + radius), (235, 235, 235), 4)
            cv2.line(image, (x_left - radius, center[1] + radius), (x_left + radius, center[1] - radius), (235, 235, 235), 4)
        elif kind == "penalty_goal":
            # white ball with a check beside it: long arm to the top-right
            cv2.circle(image, (center[0] + radius, center[1]), radius, (235, 235, 235), -1)
            x_left = center[0] - 2 * radius
            vertex = (x_left - radius // 2, center[1] + radius)
            cv2.line(image, (x_left - radius, center[1]), vertex, (235, 235, 235), 4)
            cv2.line(image, vertex, (x_left + radius, center[1] - radius), (235, 235, 235), 4)
        elif kind == "substitution":
            mid = center[0]
            cv2.rectangle(image, (x0 + 2, row_y0), (mid - 2, row_y1), (0, 200, 0), -1)
            cv2.rectangle(image, (mid + 2, row_y0), (x1 - 2, row_y1), (0, 0, 220), -1)
    cv2.imwrite(str(path), image)


def _fake_read_fragments(crop):
    def fragment(text, x_left, x_right, y_top, y_bottom):
        return {"text": text, "confidence": 0.95,
                "x_left": x_left, "x_right": x_right, "y_top": y_top, "y_bottom": y_bottom}

    out = []
    for kind, side, minute, y_top, y_bottom in ROWS:
        out.append(fragment(f"{minute}'", 0.485, 0.515, y_top, y_bottom))
        name = {"goal": "Benjamin Sesko", "yellow_card": "Koke", "missed_penalty": "Bruno Fernandes",
                "penalty_goal": "Julian Alvarez", "substitution": "Kobbie Mainoo"}[kind]
        x = (0.62, 0.75) if side == "away" else (0.30, 0.43)
        out.append(fragment(name, x[0], x[1], y_top, y_bottom))
    out.append(fragment("Marcus Rashford", 0.56, 0.68, *SUB_OFF_LINE))
    return out


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    home = get_or_create_team(conn, "Atletico de Madrid")
    away = get_or_create_team(conn, "Manchester United")
    upsert_player(conn, "Koke", "CM", 82, "test", team_id=home)
    upsert_player(conn, "Julian Alvarez", "ST", 89, "test", team_id=home)
    for name in ("Benjamin Sesko", "Bruno Fernandes", "Kobbie Mainoo", "Marcus Rashford"):
        upsert_player(conn, name, "ST", 84, "test", team_id=away)
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, home, away, "dir")
    yield conn, match, home, away
    conn.close()


def _process(conn, match, home, away, image_path, monkeypatch):
    monkeypatch.setattr(pipeline, "read_fragments", _fake_read_fragments)
    candidates = players_for_teams(conn, [home, away])
    return pipeline.process_team_events(conn, match, str(image_path), home, away, candidates)


def test_full_layout_types_sides_and_sub_pair(db, tmp_path, monkeypatch):
    conn, match, home, away = db
    image_path = tmp_path / "team_events.png"
    _synthetic_events_image(image_path)

    _, stored = _process(conn, match, home, away, image_path, monkeypatch)

    by_type = {e["event_type"]: e for e in stored}
    assert set(by_type) == {"goal", "yellow_card", "missed_penalty", "penalty_goal", "sub_on", "sub_off"}
    assert by_type["penalty_goal"]["minute"] == 58 and by_type["penalty_goal"]["team_id"] == home
    assert by_type["goal"]["minute"] == 41 and by_type["goal"]["team_id"] == away
    assert by_type["yellow_card"]["team_id"] == home  # side attribution
    assert by_type["missed_penalty"]["minute"] == 75
    assert by_type["sub_on"]["minute"] == 65 and by_type["sub_off"]["minute"] == 65
    assert by_type["sub_on"]["team_id"] == away and by_type["sub_off"]["team_id"] == away


def test_overlapping_scrolled_screenshot_dedupes(db, tmp_path, monkeypatch):
    conn, match, home, away = db
    first, second = tmp_path / "team_events.png", tmp_path / "team_events_2.png"
    _synthetic_events_image(first)
    _synthetic_events_image(second)

    _, stored_1 = _process(conn, match, home, away, first, monkeypatch)
    _, stored_2 = _process(conn, match, home, away, second, monkeypatch)

    assert len(stored_1) == 6
    assert stored_2 == []
    count = conn.execute("SELECT COUNT(*) FROM match_events WHERE match_id = ?", (match,)).fetchone()[0]
    assert count == 6
