# FIFA Career Mode Analytics

A free, locally-run system that turns EA FC Career Mode post-match screenshots into
a "true overall" model for your squad — sub-attributes that evolve match by match
based on actual performance vs. card ratings — plus team analysis and squad
recommendations. See the full spec for the long-term vision; this repo currently
implements **Phase 1: data foundation** only.

## Phase 1 scope

Capturing every stat tab for every player every match isn't sustainable, so Phase 1
deliberately captures just 3 screenshot types per match:

- **Player Performance → Summary tab**, one screenshot per player
- **Team match screen → Summary tab**, 2 screenshots (the stat list scrolls — see below)
- **Team match screen → Events tab**, 1 screenshot

The deeper tabs (Possession/Shooting/Passing/Defending breakdowns, per-player and
per-team) are a later enrichment once this pipeline is proven end-to-end, not a
day-one requirement.

## Setup

```bash
pip install -e .
python -c "from fifa_analytics.db.models import init_db; init_db('data/fifa.db')"
```

## Capturing a match

1. sofifa sits behind Cloudflare and blocks automated fetches (confirmed —
   both plain `requests` and `cloudscraper` get a 403). Rather than fight
   that: open your team's sofifa page in a normal browser, **Save Page As →
   Webpage, HTML only**, and point the scraper at that saved file (a live
   URL still works too, in case Cloudflare's rules ease up later):
   ```bash
   python -m fifa_analytics.cards.sofifa_scraper path/to/saved_team_page.html data/fifa.db "sofifa:2026"
   ```
2. Organize each match's screenshots under
   `data/screenshots/season_XX/matchweek_YY/match_ZZZZ/` following the naming
   convention in `ocr/pipeline.py`'s docstring:
   - `team_summary_page1.png`, `team_summary_page2.png`
   - `team_events.png`
   - `player_summary_<slug>.png` per player (slug = lowercase name, spaces → `_`)
3. **Before running OCR on real data**, calibrate the crop regions against your
   actual screenshots — the coordinates in `ocr/regions.py` are visual estimates,
   not pixel-measured:
   ```bash
   python -m fifa_analytics.ocr.calibrate player_summary path/to/a/real/screenshot.png
   python -m fifa_analytics.ocr.calibrate team_summary path/to/a/real/screenshot.png 1
   python -m fifa_analytics.ocr.calibrate team_events path/to/a/real/screenshot.png
   ```
   Each writes a `*_calibration.png` with the boxes overlaid — open it and adjust
   the fractional coordinates in `regions.py` until they line up.
4. Run the pipeline against a match folder (see `pipeline.run_match_dir` — needs a
   `match_id` from `db.models.create_match` and a slug→player_id mapping built from
   your roster).
5. Review and correct OCR output before it's trusted:
   ```bash
   streamlit run src/fifa_analytics/validate_app.py -- --db data/fifa.db
   ```

## Known gaps going into real use

- **Team Events layout is unconfirmed for matches with 2+ events** — only one
  single-event sample screenshot was available while building this. The pipeline
  currently just OCR-dumps the whole events band as raw text (see
  `ocr/regions.py`) rather than parsing structured (player, minute, event_type)
  rows. Send a screenshot from a match with multiple goals/cards to nail this down.
- **sofifa's markup may not match `cards/sofifa_scraper.py`'s selectors** — verify
  against a live page before trusting scraped values.
- **No xA (expected assists) field exists on any captured screen** — if
  expected-assist over/underperformance matters to the model, it isn't coming from
  OCR and would need another source or to be dropped.

## Database

`src/fifa_analytics/db/schema.sql` is the source of truth. Match stats are stored
in a normalized long format (`match_stat_values`: one row per `stat_name`/
`stat_value` pair per capture) rather than one wide table per screen type, since
the field set varies by tab/position and this way a layout change doesn't force a
migration.

## Tech stack

Python 3.11+, SQLite, OpenCV, EasyOCR, Streamlit, `requests`/`beautifulsoup4` for
the card scraper. All free/local — see the project spec for the full roadmap
(true-overall modeling, team analysis, scouting engine, local LLM assistant).
