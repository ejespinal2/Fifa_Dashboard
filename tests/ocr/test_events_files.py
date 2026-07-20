from pathlib import Path

from fifa_analytics.ocr.pipeline import _find_team_events_files, _side_icon_region
from fifa_analytics.ocr import regions


def test_finds_single_and_scrolled_team_events_files(tmp_path):
    for name in ("team_events.png", "team_events_2.jpg", "team_events_3.jpeg", "player_summary_1.png"):
        (tmp_path / name).touch()
    found = [p.name for p in _find_team_events_files(Path(tmp_path))]
    assert found == ["team_events.png", "team_events_2.jpg", "team_events_3.jpeg"]


def test_no_team_events_files(tmp_path):
    (tmp_path / "team_summary.png").touch()
    assert _find_team_events_files(Path(tmp_path)) == []


def test_side_icon_region_uses_each_sides_zone_at_the_rows_height():
    band = regions.TEAM_EVENTS_REGIONS["event_band"]  # (x0, y0, x1, y1)
    event = {"y_top": 0.0, "y_bottom": 0.1}  # top row of the band
    hx0, hy0, hx1, hy1 = _side_icon_region(band, event, "home")
    ax0, ay0, ax1, ay1 = _side_icon_region(band, event, "away")
    assert (hx0, hx1) == regions.TEAM_EVENTS_ICON_ZONES["home"]
    assert (ax0, ax1) == regions.TEAM_EVENTS_ICON_ZONES["away"]
    assert hx1 < ax0                          # zones flank the spine, home left
    assert hy0 >= band[1]                     # padding clamped to the band
    assert band[1] < hy1 < band[3]
    # a row lower in the band maps strictly lower in the image
    lower = _side_icon_region(band, {"y_top": 0.5, "y_bottom": 0.6}, "home")
    assert lower[1] > hy0


def test_find_unreserved_images_excludes_reserved_and_calibration(tmp_path):
    from fifa_analytics.ocr.pipeline import _find_unreserved_images
    for name in ("team_summary.png", "team_events.png", "team_events_2.jpg",
                 "player_summary_1.png", "IMG_0042.jpg", "screenshot 55.jpeg",
                 "team_events_calibration.png", "notes.txt"):
        (tmp_path / name).touch()
    found = [p.name for p in _find_unreserved_images(Path(tmp_path))]
    assert found == ["IMG_0042.jpg", "screenshot 55.jpeg"]
