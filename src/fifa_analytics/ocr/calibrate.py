"""Visual calibration helper — draws the region boxes from regions.py over a
real screenshot so you can see how far off the estimates are and adjust the
fractional coordinates accordingly.

Usage:
    python -m fifa_analytics.ocr.calibrate player_summary path/to/screenshot.png
    python -m fifa_analytics.ocr.calibrate team_summary path/to/screenshot.png
    python -m fifa_analytics.ocr.calibrate team_events path/to/screenshot.png

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


def calibrate_team_summary(image):
    r = regions.TEAM_SUMMARY_REGIONS
    order = regions.TEAM_SUMMARY_STAT_ORDER
    _draw_box(image, r["stat_list_box"], "stat_list_box", color=(255, 0, 0))
    _draw_box(image, r["ring_stat_home"], "ring_stat_home")
    _draw_box(image, r["ring_stat_away"], "ring_stat_away")
    for name, row_box in zip(order, regions.even_rows(r["stat_list_box"], len(order))):
        _draw_box(image, row_box, name, color=(0, 165, 255))


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
    elif screen_type == "team_events":
        calibrate_team_events(image)
    else:
        print(f"Unknown screen type: {screen_type}")
        sys.exit(1)

    out_path = image_path.rsplit(".", 1)[0] + "_calibration.png"
    cv2.imwrite(out_path, image)
    print(f"Wrote {out_path} — open it and compare the boxes against the real fields.")
