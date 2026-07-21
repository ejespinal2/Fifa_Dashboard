"""Visual calibration helper — draws the region boxes from regions.py over a
real screenshot so you can see how far off the estimates are and adjust the
fractional coordinates accordingly.

Usage:
    python -m fifa_analytics.ocr.calibrate player_summary path/to/screenshot.png
    python -m fifa_analytics.ocr.calibrate team_summary path/to/screenshot.png
    python -m fifa_analytics.ocr.calibrate player_gk path/to/screenshot.png
    python -m fifa_analytics.ocr.calibrate team_events path/to/screenshot.png

The value-column crops OCR actually reads are drawn too (magenta = the
first value column, cyan = the second where a screen has two). If a
3-digit value like 100 comes out as 0, look at whether its digits sit
fully inside that colored box or get clipped on the left edge — that
clipping is the usual cause, and the fix is widening the matching
stat_value_col_* range in regions.py.

team_summary expects the scrolled-up view (showing Possession %..Yellow
Cards) — that's the one screenshot per team per match this pipeline uses.

Writes <screenshot>_calibration.png next to the input with boxes overlaid.
"""

import sys

import cv2

from fifa_analytics.ocr import regions


def _draw_box(image, box, label, color=(0, 255, 0)):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    pt1, pt2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
    cv2.rectangle(image, pt1, pt2, color, 2)
    cv2.putText(image, label, (pt1[0], max(pt1[1] - 5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def _draw_value_columns(image, stat_list_box, stat_order, col_ranges):
    """Overlay the EXACT value-column crops (one rectangle per stat row per
    column) that read_field actually OCRs — the single most useful thing to
    eyeball, since a column too narrow to fit a 3-digit number is what makes
    a value like 100 come back as 0 (the leading digits fall outside the
    crop). col_ranges: [(x1, x2, color), ...] for each value column."""
    rows = regions.even_rows(stat_list_box, len(stat_order))
    for _, (y1, y2) in ((s, (rb[1], rb[3])) for s, rb in zip(stat_order, rows)):
        for cx1, cx2, color in col_ranges:
            _draw_box(image, (cx1, y1, cx2, y2), "", color=color)


def calibrate_player_summary(image):
    r = regions.PLAYER_SUMMARY_REGIONS
    _draw_box(image, r["team_header"], "team_header")
    _draw_box(image, r["total_rating"], "total_rating")
    _draw_box(image, r["active_player_name"], "active_player_name")
    _draw_box(image, r["stat_list_box"], "stat_list_box", color=(255, 0, 0))
    for name, row_box in zip(
        regions.PLAYER_SUMMARY_STAT_ORDER, regions.even_rows(r["stat_list_box"], len(regions.PLAYER_SUMMARY_STAT_ORDER))
    ):
        _draw_box(image, row_box, name, color=(0, 165, 255))
    # magenta = the value span the pipeline reads. It should cover BOTH
    # number columns (player value + team value) and stay clear of the stat
    # labels on the left — the pipeline takes the leftmost number as the
    # player's own value, so exact column placement inside the box doesn't
    # matter, only that both columns fall inside it and no label text does.
    _draw_value_columns(
        image, r["stat_list_box"], regions.PLAYER_SUMMARY_STAT_ORDER,
        [(*r["stat_value_span"], (255, 0, 255))],
    )


def calibrate_team_summary(image):
    r = regions.TEAM_SUMMARY_REGIONS
    order = regions.TEAM_SUMMARY_STAT_ORDER
    _draw_box(image, r["stat_list_box"], "stat_list_box", color=(255, 0, 0))
    _draw_box(image, r["ring_stat_home"], "ring_stat_home")
    _draw_box(image, r["ring_stat_away"], "ring_stat_away")
    for name, row_box in zip(order, regions.even_rows(r["stat_list_box"], len(order))):
        _draw_box(image, row_box, name, color=(0, 165, 255))
    # magenta = home value crop, cyan = away value crop.
    _draw_value_columns(
        image, r["stat_list_box"], order,
        [(*r["stat_value_col_home"], (255, 0, 255)), (*r["stat_value_col_away"], (255, 255, 0))],
    )


def calibrate_player_gk(image):
    r = regions.PLAYER_GK_REGIONS
    _draw_box(image, r["goalkeeper_rating"], "goalkeeper_rating")
    _draw_box(image, r["stat_list_box"], "stat_list_box", color=(255, 0, 0))
    for name, row_box in zip(regions.PLAYER_GK_STAT_ORDER, regions.even_rows(r["stat_list_box"], len(regions.PLAYER_GK_STAT_ORDER))):
        _draw_box(image, row_box, name, color=(0, 165, 255))
    # magenta = value crop.
    _draw_value_columns(image, r["stat_list_box"], regions.PLAYER_GK_STAT_ORDER, [(*r["stat_value_col"], (255, 0, 255))])


def calibrate_team_events(image):
    _draw_box(image, regions.TEAM_EVENTS_REGIONS["event_band"], "event_band", color=(255, 0, 0))
    _draw_box(image, regions.TEAM_EVENTS_REGIONS["event_icon"], "event_icon")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m fifa_analytics.ocr.calibrate <player_summary|team_summary|team_events> <image_path>")
        sys.exit(1)

    screen_type, image_path = sys.argv[1], sys.argv[2]
    image = cv2.imread(image_path)
    if image is None:
        print(f"Could not read image: {image_path}")
        sys.exit(1)

    if screen_type == "player_summary":
        calibrate_player_summary(image)
    elif screen_type == "team_summary":
        calibrate_team_summary(image)
    elif screen_type == "player_gk":
        calibrate_player_gk(image)
    elif screen_type == "team_events":
        calibrate_team_events(image)
    else:
        print(f"Unknown screen type: {screen_type}")
        sys.exit(1)

    out_path = image_path.rsplit(".", 1)[0] + "_calibration.png"
    cv2.imwrite(out_path, image)
    print(f"Wrote {out_path} — open it and compare the boxes against the real fields.")
