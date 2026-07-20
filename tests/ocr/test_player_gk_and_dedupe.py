"""process_player_gk + duplicate-image/player guards, OCR stubbed:
read_text resolves the team header and player name, read_field returns a
canned Goalkeeping-tab stat sheet — file hashing, capture creation, stat
storage, and both dedupe layers run for real."""

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

GK_VALUES = [11.0, 6.0, 4.0, 2.0, 67.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]  # Oblak's real sheet


def _image(path, seed):
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), rng.integers(0, 255, size=(90, 160, 3), dtype=np.uint8))


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    conn = connect(path)
    home = get_or_create_team(conn, "Atletico de Madrid")
    away = get_or_create_team(conn, "Manchester United")
    upsert_player(conn, "Jan Oblak", "GK", 88, "test", team_id=home)
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, home, away, "dir")
    yield conn, match, home, away
    conn.close()


def _stub_ocr(monkeypatch):
    monkeypatch.setattr(pipeline, "read_text", lambda crop: ("ATLETICO DE MADRID", 0.9) if crop.shape[1] > crop.shape[0] * 3 else ("Jan Oblak", 0.9))
    values = iter(GK_VALUES + [5.9])
    monkeypatch.setattr(pipeline, "read_field", lambda crop: (next(values), 0.9))


def _process(conn, match, home, away, path, monkeypatch):
    candidates = players_for_teams(conn, [home, away])
    return pipeline.process_player_gk(
        conn, match, str(path), home, "Atletico de Madrid", away, "Manchester United", candidates, []
    )


def test_player_gk_capture_stores_full_stat_sheet(db, tmp_path, monkeypatch):
    conn, match, home, away = db
    image = tmp_path / "player_gk_1.png"
    _image(image, seed=1)
    _stub_ocr(monkeypatch)

    capture_id, confidence = _process(conn, match, home, away, image, monkeypatch)

    assert capture_id is not None
    row = conn.execute("SELECT * FROM ocr_captures WHERE capture_id = ?", (capture_id,)).fetchone()
    assert row["capture_type"] == "player_gk"
    assert row["content_hash"] is not None
    assert row["player_id"] is not None and row["team_id"] == home
    stats = {
        r["stat_name"]: r["stat_value"]
        for r in conn.execute("SELECT * FROM match_stat_values WHERE capture_id = ?", (capture_id,))
    }
    assert stats["gk_shots_against"] == 11.0
    assert stats["gk_save_success_rate_pct"] == 67.0
    assert stats["gk_penalty_saves"] == 1.0
    assert stats["goalkeeper_rating"] == 5.9
    assert len(stats) == len(regions.PLAYER_GK_STAT_ORDER) + 1


def test_identical_file_twice_is_skipped(db, tmp_path, monkeypatch):
    conn, match, home, away = db
    first, second = tmp_path / "player_gk_1.png", tmp_path / "IMG_999.png"
    _image(first, seed=1)
    _image(second, seed=1)  # same seed -> byte-identical content

    _stub_ocr(monkeypatch)
    capture_id, _ = _process(conn, match, home, away, first, monkeypatch)
    assert capture_id is not None

    dup_id, dup_reason = _process(conn, match, home, away, second, monkeypatch)
    assert dup_id is None and dup_reason == "duplicate_image"
    assert conn.execute("SELECT COUNT(*) FROM ocr_captures").fetchone()[0] == 1


def test_different_file_same_player_is_skipped(db, tmp_path, monkeypatch):
    conn, match, home, away = db
    first, second = tmp_path / "player_gk_1.png", tmp_path / "player_gk_2.png"
    _image(first, seed=1)
    _image(second, seed=2)  # different bytes, same player inside

    _stub_ocr(monkeypatch)
    capture_id, _ = _process(conn, match, home, away, first, monkeypatch)
    assert capture_id is not None

    _stub_ocr(monkeypatch)  # fresh value iterator for the second run
    dup_id, dup_reason = _process(conn, match, home, away, second, monkeypatch)
    assert dup_id is None and dup_reason == "duplicate_player"
    assert conn.execute("SELECT COUNT(*) FROM ocr_captures").fetchone()[0] == 1
