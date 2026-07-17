"""Scrapes a squad's card overalls from sofifa.

Scope for Phase 1: your own squad only, from a single team page. League-wide
scouting scrapes (for scouting_candidates) are a Phase 4 concern.

sofifa sits behind Cloudflare's bot check, which blocks both plain `requests`
and `cloudscraper` (confirmed against a real run — sofifa's challenge level
is high enough neither gets through). Rather than escalate into a browser-
automation arms race, this accepts EITHER a live URL (tried via cloudscraper,
kept in case it works on a lower-protected page or Cloudflare's rules ease
up) OR a path to an HTML file you saved yourself:

    1. Open the team page in your normal browser (Cloudflare won't block a
       real human browsing session)
    2. Right-click -> Save Page As -> "Webpage, HTML only"
    3. Pass that file's path instead of the URL

sofifa's markup has changed over the years and isn't guaranteed to match the
selectors below right now — verify against a live fetch of your team's page
before trusting this, and adjust ROW_SELECTOR / column indices to match what
you actually see in the page source.
"""

import re
import sys
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup

from fifa_analytics.db.models import connect, upsert_player

# sofifa's team page renders one <table> of players. Each row historically
# has the player name in the first link, followed by columns for age,
# overall (OVR), potential (POT), and per-attribute ratings. Confirm this
# against the live page — column order shifts between sofifa layout updates.
ROW_SELECTOR = "table tbody tr"
NAME_SELECTOR = "td.col-name a[href*='/player/']"
POSITION_SELECTOR = "td.col-name span.pos"


def _parse_int(text: str) -> int | None:
    match = re.search(r"-?\d+", text or "")
    return int(match.group()) if match else None


def load_team_page(source: str) -> BeautifulSoup:
    """source is either an http(s) URL or a path to a saved HTML file."""
    if source.startswith("http://") or source.startswith("https://"):
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
        resp = scraper.get(source, timeout=30)
        resp.raise_for_status()
        html = resp.text
    else:
        html = Path(source).read_text(encoding="utf-8", errors="replace")
    return BeautifulSoup(html, "html.parser")


def parse_squad(soup: BeautifulSoup) -> list[dict]:
    players = []
    for row in soup.select(ROW_SELECTOR):
        name_el = row.select_one(NAME_SELECTOR)
        if not name_el:
            continue

        cols = row.find_all("td")
        col_text = [c.get_text(strip=True) for c in cols]

        pos_el = row.select_one(POSITION_SELECTOR)

        players.append(
            {
                "name": name_el.get_text(strip=True),
                "position": pos_el.get_text(strip=True) if pos_el else None,
                # Column indices below are a best guess (age, OVR, POT) —
                # print `col_text` for one row and fix these against what
                # sofifa actually renders before trusting the output.
                "age": _parse_int(col_text[2]) if len(col_text) > 2 else None,
                "base_overall": _parse_int(col_text[3]) if len(col_text) > 3 else None,
                "potential": _parse_int(col_text[4]) if len(col_text) > 4 else None,
            }
        )
    return players


def scrape_and_store(source: str, db_path: str, source_label: str) -> int:
    soup = load_team_page(source)
    players = parse_squad(soup)

    conn = connect(db_path)
    try:
        for p in players:
            if p["base_overall"] is None:
                continue
            upsert_player(
                conn,
                name=p["name"],
                position=p["position"] or "UNK",
                base_overall=p["base_overall"],
                age=p["age"],
                potential=p["potential"],
                source=source_label,
            )
    finally:
        conn.close()
    return len(players)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python -m fifa_analytics.cards.sofifa_scraper <team_url_or_saved_html_path> <db_path> <source_label>")
        sys.exit(1)
    count = scrape_and_store(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"Stored {count} players.")
