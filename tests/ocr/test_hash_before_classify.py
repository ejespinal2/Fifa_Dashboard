"""A duplicate image (byte-identical to one already processed for this
match) must be skipped BEFORE the expensive classify_screenshot OCR runs
-- not after. Previously classification ran first, so a duplicate
download/re-run paid the full classification cost for nothing."""

import numpy as np
import pytest

try:
    import cv2
except ImportError:
    cv2 = None

from fifa_analytics.db.models import (
    connect,
    create_capture,
    create_match,
    get_or_create_season,
    get_or_create_team,
    init_db,
)
from fifa_analytics.ocr import pipeline

pytestmark = pytest.mark.skipif(cv2 is None, reason="cv2 not installed")


def test_known_duplicate_skips_before_classification(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    conn = connect(db_path)
    home = get_or_create_team(conn, "Atletico de Madrid")
    away = get_or_create_team(conn, "Manchester United")
    season = get_or_create_season(conn, "2025-26")
    match = create_match(conn, season, 1, home, away, "dir")

    image_path = tmp_path / "IMG_0001.jpeg"
    cv2.imwrite(str(image_path), np.zeros((50, 50, 3), dtype=np.uint8))
    already_seen_hash = pipeline._file_hash(str(image_path))
    create_capture(conn, match, "player_summary", str(image_path), content_hash=already_seen_hash)
    conn.commit()
    conn.close()

    def boom(image):
        raise AssertionError("classify_screenshot must not run for an already-processed image")

    # run_match_dir imports classify_screenshot locally inside the function
    # body, so it must be patched at its source module, not on pipeline.
    import fifa_analytics.ocr.classify_screen as classify_screen_module
    monkeypatch.setattr(classify_screen_module, "classify_screenshot", boom)

    pipeline._run_match_dir_locked(db_path, str(tmp_path), match, "Atletico de Madrid", "Manchester United")
    # if classify_screenshot had been called, the AssertionError above would
    # have propagated out of run_match_dir and failed this test already
