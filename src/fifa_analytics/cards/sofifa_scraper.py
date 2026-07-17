"""Pulls a squad's card overalls from SoFIFA's public JSON API (api.sofifa.net).

api.sofifa.net (documented at https://sofifa.com/document) returns a team's
full squad — including each player's six core sub-attributes
(pac/sho/pas/dri/def/phy) — in one call, in principle without needing an API
token. In practice, direct requests.get() calls get a 403 even with
browser-like headers (confirmed on a real run), so this also accepts a path
to a JSON file you saved yourself:

    1. Open https://api.sofifa.net/team/<your_team_id> in your normal browser
       (a real browser session isn't blocked the way an automated request is)
    2. Save the page (Ctrl+S -> it'll save as .json, or select-all/copy the
       raw JSON text into a .json file)
    3. Pass that file's path instead of the numeric team ID

Per sofifa's stated API terms: non-commercial use only, and don't build an
app that relies *entirely* on their API without your own database behind it
(this repo's SQLite schema covers that).

Find your team's numeric ID from its sofifa team page URL, e.g.
https://sofifa.com/team/11/manchester-united/ -> team_id=11
"""

import json
import sys
from pathlib import Path

import requests

from fifa_analytics.db.models import connect, upsert_player

API_BASE = "https://api.sofifa.net"

# The bare python-requests User-Agent gets blocked by a lot of APIs (even
# ones meant to be publicly callable) — send headers that look like sofifa's
# own website calling its own API, since Referer/Origin checks are common
# for "public" APIs that are really just the site's own frontend backend.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://sofifa.com/",
    "Origin": "https://sofifa.com",
}

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
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]


def load_team_from_file(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    # Accept either the raw {"data": {...}} envelope or just the inner object,
    # in case a browser's "save as" or a copy-paste dropped the wrapper.
    return payload.get("data", payload)


def player_display_name(player: dict) -> str:
    return player.get("commonName") or f"{player['firstName']} {player['lastName']}"


def scrape_and_store(source: str, db_path: str, source_label: str, roster: str | None = None) -> int:
    """source is either a numeric team ID (live fetch) or a path to a saved
    JSON file (see module docstring for why the fallback exists)."""
    if source.isdigit():
        team = fetch_team(int(source), roster)
    else:
        team = load_team_from_file(source)
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
        print("Usage: python -m fifa_analytics.cards.sofifa_scraper <team_id_or_saved_json_path> <db_path> <source_label> [roster]")
        sys.exit(1)
    roster = sys.argv[4] if len(sys.argv) == 5 else None
    count = scrape_and_store(sys.argv[1], sys.argv[2], sys.argv[3], roster)
    print(f"Stored {count} players.")
