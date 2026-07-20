"""Crop-region definitions for the 3 screen types captured in Phase 1.

All boxes are fractions of (width, height) — (x1, y1, x2, y2), each in [0, 1] —
so they hold up across different screenshot resolutions (PS5 native capture
vs. a cropped/resized copy) as long as the in-game UI layout itself doesn't
change between patches.

IMPORTANT: these numbers are visual estimates from screenshots viewed in
chat, not pixel-measured against real files — nobody has run this against an
actual screenshot yet. Run `python -m fifa_analytics.ocr.calibrate <path>` on
a couple of real files and adjust the boxes below before trusting any OCR
output from this module.
"""


def even_rows(box: tuple[float, float, float, float], n: int) -> list[tuple[float, float, float, float]]:
    """Split a bounding box into n equal-height horizontal row bands."""
    x1, y1, x2, y2 = box
    row_h = (y2 - y1) / n
    return [(x1, y1 + i * row_h, x2, y1 + (i + 1) * row_h) for i in range(n)]


# ---------------------------------------------------------------------------
# Player Performance -> Summary tab
# ---------------------------------------------------------------------------

# On-screen order of the right-hand stat list, top to bottom. Fixed by the
# game UI — do not reorder without re-checking a real screenshot.
PLAYER_SUMMARY_STAT_ORDER = [
    "goals",
    "assists",
    "shots",
    "shot_accuracy_pct",
    "passes",
    "pass_accuracy_pct",
    "dribbles",
    "dribble_success_rate_pct",
    "tackles",
    "tackle_success_rate_pct",
    "offsides",
    "fouls_committed",
    "possession_won",
    "possession_lost",
    "minutes_played_vs_team_avg",
    "distance_covered_vs_team_avg_km",
    "distance_sprinted_vs_team_avg_km",
]

PLAYER_SUMMARY_REGIONS = {
    # Team name + crest, top-right of the header (e.g. "MAN UTD"). Used to
    # work out which of the match's two teams this screenshot belongs to
    # (see ocr/team_match.py) — not calibrated against a real screenshot yet.
    "team_header": (0.80, 0.03, 0.95, 0.10),
    # "Total Rating: 7.5" text, upper-center. Extended down slightly from the
    # first calibration pass, which was cutting it close.
    "total_rating": (0.385, 0.195, 0.55, 0.26),
    # Highlighted row in the left-hand squad list gives active player's name
    # (first + last name, sometimes wrapped across 2 lines) + position.
    # Extended down further than the first calibration pass so a 3-part name
    # (or a name that wraps to a 3rd line) doesn't get clipped. Right edge
    # pulled back from 0.34 after a real run: the header's rating circle
    # sits around x=0.28-0.33 and was bleeding into the crop, so OCR read
    # "Aurelien Tchouameni 7.5". (clean_ocr_name also strips digit tokens
    # as a second line of defense.)
    "active_player_name": (0.09, 0.185, 0.27, 0.29),
    # The full 17-row stat list on the right. Column split below handles
    # "player value" vs. "team value" — both appear on every row here.
    "stat_list_box": (0.663, 0.275, 0.955, 0.885),
    "stat_list_row_count": len(PLAYER_SUMMARY_STAT_ORDER),
    # Within each row band, x-ranges for the two number columns
    "stat_value_col_player": (0.885, 0.915),
    "stat_value_col_team": (0.915, 0.955),
}


# ---------------------------------------------------------------------------
# Team match screen -> Summary tab
#
# The stat list actually scrolls across 2 screen positions (one showing
# Tackles..Def Line Breaks Attempted, one scrolled up showing Possession
# %..Yellow Cards) but only the scrolled-up view is captured going forward —
# it's the one screenshot per team per match this pipeline expects.
# ---------------------------------------------------------------------------

TEAM_SUMMARY_STAT_ORDER = [
    "possession_pct",
    "ball_recovery_time_seconds",
    "shots",
    "expected_goals",
    "passes",
    "tackles",
    "tackles_won",
    "interceptions",
    "saves",
    "fouls_committed",
    "offsides",
    "corners",
    "free_kicks",
    "penalty_kicks",
    "yellow_cards",
]

TEAM_SUMMARY_REGIONS = {
    "home_score": (0.40, 0.05, 0.47, 0.11),
    "away_score": (0.53, 0.05, 0.60, 0.11),
    "match_clock": (0.46, 0.11, 0.54, 0.15),
    # Center stat-name column + two side columns (home left, away right)
    "stat_list_box": (0.34, 0.235, 0.665, 0.895),
    "stat_value_col_home": (0.335, 0.365),
    "stat_value_col_away": (0.635, 0.665),
    # The three ring stats (dribble success / shot accuracy / pass accuracy)
    # sit outside the main list, one per third of the screen height
    "ring_stat_home": (0.14, 0.14, 0.22, 0.93),
    "ring_stat_away": (0.78, 0.14, 0.86, 0.93),
}


# ---------------------------------------------------------------------------
# Team match screen -> Events tab
#
# Only one event was observed in the sample screenshots (a single assist,
# shown centered with a player face + minute), so it's unconfirmed whether
# multiple events in the same match render as a vertical list, a horizontal
# timeline, or one-at-a-time via a scroll/toggle control. event_band is
# deliberately generous top-to-bottom — right up against the tab bar above
# and the bottom control hints below — so it catches however many rows a
# busier match adds, rather than assuming they stack in a specific direction
# from the single-event sample. Treat this as "scan the whole band for
# face+minute markers" rather than fixed rows until a match with 2+ events
# can be checked.
# ---------------------------------------------------------------------------

TEAM_EVENTS_REGIONS = {
    "event_band": (0.03, 0.19, 0.97, 0.95),
    # The small goal/card icon sits between the player's face photo and the
    # minute circle. Estimated from the one confirmed sample (a goal) — the
    # face photo sits just to its left, so this box is deliberately narrow
    # to avoid picking up skin-tone pixels from the photo. Unverified
    # against a real card-event screenshot.
    "event_icon": (0.44, 0.34, 0.485, 0.41),
}

# The Events tab's icons sit between the center spine and each side's face
# photo — one narrow x-band per team side. Measured from real multi-event
# screenshots (Atlético 0:2 Man Utd, 2000x1125): home-side icons (sub
# arrows) at x 918-938px, away-side icons (ball, ball+X, arrows) at
# x 1058-1092px. Bands are padded a little each way.
TEAM_EVENTS_ICON_ZONES = {
    "home": (0.448, 0.492),
    "away": (0.523, 0.558),
}
