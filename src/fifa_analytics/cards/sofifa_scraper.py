"""Pulls a squad's card overalls from SoFIFA's public JSON API (api.sofifa.net).

This replaced an earlier HTML-scraping approach entirely — sofifa.com's own
web pages sit behind Cloudflare, but api.sofifa.net is a documented, public,
token-free API for exactly this data (see https://sofifa.com/document). One
call to /team/{id} returns the full squad, including each player's six core
sub-attributes (pac/sho/pas/dri/def/phy) directly — no per-player calls, no
CSS-selector guessing, no attribute-column mapping.

Per sofifa's stated API terms: non-commercial use only, and don't build an
app that relies *entirely* on their API without your own database behind it
(this repo's SQLite schema covers that).

Find your team's numeric ID from its sofifa team page URL, e.g.
https://sofifa.com/team/11/manchester-united/ -> team_id=11
"""

import sys

import requests

from fifa_analytics.db.models import connect, upsert_player

API_BASE = "https://api.sofifa.net"

# From the "Positions" table in sofifa's API docs (the position1 field on each player)
POSITION_CODE_MAP = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB", 7: "LB",
    8: "LWB", 9: "RDM", 10: "CDM", 11: "LDM", 12: "RM", 13: "RCM", 14: "CM",
    15: "LCM", 16: "LM", 17: "RAM", 18: "CAM", 19: "LAM", 20: "RF", 21: "CF",
    22: "LF", 23: "RW", 24: "RS", 25: "ST", 26: "LS", 27: "LW", 28: "SUB", 29: "RES",
}


def fetch_team(team_id: int, roster: str | None = None) -> dict:
    """roster pins a specific historical data snapshot (sofifa's roster ID,
    e.g. "260013") — omit it to get the latest available data for that team.
    """
    url = f"{API_BASE}/team/{team_id}/{roster}" if roster else f"{API_BASE}/team/{team_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def player_display_name(player: dict) -> str:
    return player.get("commonName") or f"{player['firstName']} {player['lastName']}"


def scrape_and_store(team_id: int, db_path: str, source_label: str, roster: str | None = None) -> int:
    team = fetch_team(team_id, roster)
    players = team.get("players", [])

    conn = connect(db_path)
    try:
        for p in players:
            upsert_player(
                conn,
                name=player_display_name(p),
                position=POSITION_CODE_MAP.get(p.get("position1"), "UNK"),
                base_overall=p["overallRating"],
                base_pace=p.get("pac"),
                base_shooting=p.get("sho"),
                base_passing=p.get("pas"),
                base_dribbling=p.get("dri"),
                base_defending=p.get("def"),
                base_physical=p.get("phy"),
                age=p.get("age"),
                potential=p.get("potential"),
                jersey_number=p.get("jerseyNumber"),
                source=source_label,
            )
    finally:
        conn.close()
    return len(players)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print("Usage: python -m fifa_analytics.cards.sofifa_scraper <team_id> <db_path> <source_label> [roster]")
        sys.exit(1)
    team_id = int(sys.argv[1])
    roster = sys.argv[4] if len(sys.argv) == 5 else None
    count = scrape_and_store(team_id, sys.argv[2], sys.argv[3], roster)
    print(f"Stored {count} players.")
