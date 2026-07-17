"""One-off script to run a single real match through the pipeline and print
what actually got read. Not part of the package -- just a convenience
runner for manual testing. Delete or ignore once you have a real CLI/UI
flow you prefer.

Usage:
    python test_real_match.py <path_to_match_screenshot_folder>

The folder must contain team_summary.png, team_events.png, and one or more
player_summary_*.png files (see README.md for the naming convention).
"""

import sys

from fifa_analytics.db.models import (
    init_db,
    connect,
    get_or_create_season,
    create_match,
    get_team_id_by_name,
)
from fifa_analytics.cards.eafc26_datahub_importer import scrape_and_store
from fifa_analytics.ocr.pipeline import run_match_dir

DB_PATH = "data/fifa.db"
HOME_TEAM = "Manchester United"
AWAY_TEAM = "Bayer 04 Leverkusen"


def main(match_dir: str):
    init_db(DB_PATH)

    print(f"Importing {HOME_TEAM}...")
    print(f"  {scrape_and_store(HOME_TEAM, DB_PATH, 'eafc26-datahub:main')} players stored")
    print(f"Importing {AWAY_TEAM}...")
    print(f"  {scrape_and_store(AWAY_TEAM, DB_PATH, 'eafc26-datahub:main')} players stored")

    conn = connect(DB_PATH)
    season_id = get_or_create_season(conn, "2025-26")
    home_id = get_team_id_by_name(conn, HOME_TEAM)
    away_id = get_team_id_by_name(conn, AWAY_TEAM)
    match_id = create_match(conn, season_id, 1, home_id, away_id, match_dir, home_score=1, away_score=0)
    conn.close()

    print(f"\nRunning OCR pipeline against {match_dir} ...")
    run_match_dir(DB_PATH, match_dir, match_id, HOME_TEAM, AWAY_TEAM)

    print("\n--- What got read ---")
    conn = connect(DB_PATH)
    for row in conn.execute(
        """SELECT oc.capture_id, oc.capture_type, oc.raw_text, oc.match_confidence,
                  oc.ocr_confidence_avg, p.name AS matched_player
           FROM ocr_captures oc LEFT JOIN players p ON p.player_id = oc.player_id
           WHERE oc.match_id = ? ORDER BY oc.capture_id""",
        (match_id,),
    ):
        print(dict(row))

    print("\n--- All stat values (team_summary + player_summary) ---")
    for row in conn.execute(
        """SELECT oc.capture_id, oc.capture_type, oc.team_id, p.name AS player_name,
                  msv.stat_name, msv.stat_value, msv.ocr_confidence
           FROM ocr_captures oc
           JOIN match_stat_values msv ON msv.capture_id = oc.capture_id
           LEFT JOIN players p ON p.player_id = oc.player_id
           WHERE oc.match_id = ?
           ORDER BY oc.capture_id, msv.stat_name""",
        (match_id,),
    ):
        print(dict(row))

    print("\n--- Parsed match events (player, minute, goal/card) ---")
    rows = conn.execute(
        """SELECT me.event_id, p.name AS player, t.name AS team, me.minute, me.event_type
           FROM match_events me
           LEFT JOIN players p ON p.player_id = me.player_id
           LEFT JOIN teams t ON t.team_id = me.team_id
           WHERE me.match_id = ?""",
        (match_id,),
    ).fetchall()
    if rows:
        for row in rows:
            print(dict(row))
    else:
        print("(none parsed -- check the team_events warning printed above)")

    conn.close()
    print(f"\nDone. Run: streamlit run src/fifa_analytics/validate_app.py -- --db {DB_PATH}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_real_match.py <path_to_match_screenshot_folder>")
        sys.exit(1)
    main(sys.argv[1])
