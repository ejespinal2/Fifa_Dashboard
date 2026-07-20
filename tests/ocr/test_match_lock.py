"""The file-based per-match lock that stops a second concurrent
run_match_dir call for the same fixture — the actual bug behind "every
filename in the log appeared twice": a second click/reload during a long
blocking OCR run started a full second pass in parallel."""

import time

import pytest

from fifa_analytics.ocr.pipeline import MatchAlreadyProcessingError, _match_lock


def test_second_concurrent_lock_is_rejected(tmp_path):
    db_path = str(tmp_path / "fifa.db")
    with _match_lock(db_path, match_id=1):
        with pytest.raises(MatchAlreadyProcessingError, match="already being processed"):
            with _match_lock(db_path, match_id=1):
                pass  # pragma: no cover -- must never be entered


def test_different_matches_dont_contend(tmp_path):
    db_path = str(tmp_path / "fifa.db")
    with _match_lock(db_path, match_id=1):
        with _match_lock(db_path, match_id=2):
            pass  # no error -- independent fixtures, independent locks


def test_lock_releases_after_the_with_block(tmp_path):
    db_path = str(tmp_path / "fifa.db")
    with _match_lock(db_path, match_id=1):
        pass
    with _match_lock(db_path, match_id=1):  # would raise if the first lock leaked
        pass


def test_lock_releases_even_if_the_body_raises(tmp_path):
    db_path = str(tmp_path / "fifa.db")
    with pytest.raises(ValueError):
        with _match_lock(db_path, match_id=1):
            raise ValueError("boom")
    with _match_lock(db_path, match_id=1):  # released despite the exception
        pass


def test_stale_lock_is_taken_over(tmp_path, monkeypatch):
    from fifa_analytics.ocr import pipeline

    monkeypatch.setattr(pipeline, "LOCK_STALE_SECONDS", 0.05)
    db_path = str(tmp_path / "fifa.db")
    path = pipeline._lock_path(db_path, match_id=1)
    path.write_text(str(time.time()))
    time.sleep(0.1)
    with _match_lock(db_path, match_id=1):  # stale -- must not raise
        pass
