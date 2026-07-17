"""Loads a squad's card overalls from ismailoksuz/EAFC26-DataHub on GitHub.

This replaces the earlier sofifa-based approaches entirely. sofifa's own
site sits behind Cloudflare and its documented API 403s even with browser-
like headers from this session's network — and even if that were fixed,
sofifa's API terms describe a partner program (public website + logo)
that a private hobby project doesn't cleanly fit.

EAFC26-DataHub instead redistributes an open Kaggle dataset
(https://www.kaggle.com/datasets/rovnez/fc-26-fifa-26-player-data) as a
static CSV in a public GitHub repo — no API, no rate limits, no ToS
gymnastics. It's fetched directly from raw.githubusercontent.com by
default; pass a local path instead if you'd rather pin a downloaded copy
for reproducibility (the repo's data could change on a future commit).

The CSV already uses human-readable position labels (e.g. "LCM", "RW",
"SUB") matching the game's own abbreviations, so no position-code mapping
is needed here (unlike the sofifa API's numeric position1 field).
"""

import csv
import io
import sys

import requests

from fifa_analytics.db.models import connect, get_or_create_team, upsert_player

RAW_CSV_URL = "https://raw.githubusercontent.com/ismailoksuz/EAFC26-DataHub/main/data/players.csv"


def _to_int(value: str) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def load_rows(csv_source: str) -> list[dict]:
    """csv_source is either an http(s) URL or a local file path."""
    if csv_source.startswith("http://") or csv_source.startswith("https://"):
        resp = requests.get(csv_source, timeout=60)
        resp.raise_for_status()
        text = resp.text
    else:
        with open(csv_source, encoding="utf-8") as f:
            text = f.read()
    return list(csv.DictReader(io.StringIO(text)))


def players_for_club(rows: list[dict], club_name: str) -> list[dict]:
    matches = [r for r in rows if r.get("club_name", "").strip().lower() == club_name.strip().lower()]
    if not matches:
        available = sorted({r["club_name"] for r in rows if club_name.lower() in r.get("club_name", "").lower()})
        hint = f" Similar club names found: {available}" if available else ""
        raise ValueError(f"No players found for club_name={club_name!r}.{hint}")
    return matches


def scrape_and_store(club_name: str, db_path: str, source_label: str, csv_source: str = RAW_CSV_URL) -> int:
    rows = load_rows(csv_source)
    players = players_for_club(rows, club_name)

    conn = connect(db_path)
    try:
        # Use the CSV's own club_name for every row rather than the possibly
        # differently-cased club_name argument, so the team row this batch
        # links to matches exactly what players_for_club actually matched on.
        team_id = get_or_create_team(conn, players[0]["club_name"], players[0].get("league_name"))
        for p in players:
            upsert_player(
                conn,
                name=p["short_name"],
                team_id=team_id,
                position=p.get("club_position") or "UNK",
                base_overall=_to_int(p["overall"]),
                base_pace=_to_int(p.get("pace")),
                base_shooting=_to_int(p.get("shooting")),
                base_passing=_to_int(p.get("passing")),
                base_dribbling=_to_int(p.get("dribbling")),
                base_defending=_to_int(p.get("defending")),
                base_physical=_to_int(p.get("physic")),
                age=_to_int(p.get("age")),
                potential=_to_int(p.get("potential")),
                jersey_number=_to_int(p.get("club_jersey_number")),
                source=source_label,
            )
    finally:
        conn.close()
    return len(players)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print("Usage: python -m fifa_analytics.cards.eafc26_datahub_importer <club_name> <db_path> <source_label> [csv_path_or_url]")
        sys.exit(1)
    csv_source = sys.argv[4] if len(sys.argv) == 5 else RAW_CSV_URL
    count = scrape_and_store(sys.argv[1], sys.argv[2], sys.argv[3], csv_source)
    print(f"Stored {count} players.")
