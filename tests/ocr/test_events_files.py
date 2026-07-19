from pathlib import Path

from fifa_analytics.ocr.pipeline import _find_team_events_files, _row_icon_region
from fifa_analytics.ocr import regions


def test_finds_single_and_scrolled_team_events_files(tmp_path):
    for name in ("team_events.png", "team_events_2.jpg", "team_events_3.jpeg", "player_summary_1.png"):
        (tmp_path / name).touch()
    found = [p.name for p in _find_team_events_files(Path(tmp_path))]
    assert found == ["team_events.png", "team_events_2.jpg", "team_events_3.jpeg"]


def test_no_team_events_files(tmp_path):
    (tmp_path / "team_summary.png").touch()
    assert _find_team_events_files(Path(tmp_path)) == []


def test_row_icon_region_maps_band_fractions_to_image_fractions():
    band = regions.TEAM_EVENTS_REGIONS["event_band"]  # (x0, y0, x1, y1)
    line = {"y_top": 0.0, "y_bottom": 0.1}  # top row of the band
    x0, y0, x1, y1 = _row_icon_region(band, line)
    assert (x0, x1) == regions.TEAM_EVENTS_ICON_COLUMN
    assert y0 >= band[1]                      # padding clamped to the band
    assert band[1] < y1 < band[3]
    # a row lower in the band maps strictly lower in the image
    lower = _row_icon_region(band, {"y_top": 0.5, "y_bottom": 0.6})
    assert lower[1] > y0
