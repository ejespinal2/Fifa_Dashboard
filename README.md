# FIFA Career Mode Analytics

A free, locally-run system that turns EA FC Career Mode post-match screenshots into
a "true overall" model for your squad — sub-attributes that evolve match by match
based on actual performance vs. card ratings — plus team analysis and squad
recommendations. See the full spec for the long-term vision; this repo currently
implements **Phase 1: data foundation** only.

## Phase 1 scope

Capturing every stat tab for every player every match isn't sustainable, so Phase 1
deliberately captures just 3 screenshot types per match:

- **Player Performance → Summary tab**, one screenshot per player who featured,
  for *both* teams (typically ~11-16 per side)
- **Team match screen → Summary tab**, one screenshot — the scrolled-up view
  (Possession %..Yellow Cards). It shows both teams' numbers side by side, so
  one screenshot produces two captures internally, one per team.
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

1. Card overalls come from [EAFC26-DataHub](https://github.com/ismailoksuz/EAFC26-DataHub),
   a public GitHub repo redistributing an open Kaggle dataset (18,000+ players,
   110+ attributes) as a static CSV — no API, no rate limits, no Cloudflare, no
   terms-of-service questions (this superseded two earlier attempts to use
   sofifa directly, both of which hit either a Cloudflare block or a partner-
   program requirement that a private project doesn't cleanly fit). Verified
   working end-to-end against the live file:
   ```bash
   python -m fifa_analytics.cards.eafc26_datahub_importer "Manchester United" data/fifa.db "eafc26-datahub:main"
   ```
   Defaults to fetching the latest CSV straight from
   `raw.githubusercontent.com`; pass a 4th argument (a local file path or a
   URL pinned to a specific commit) if you'd rather not depend on the file
   changing under you on a future commit.
2. Import card data for **both** teams in the match before processing it (the
   player-matching step below needs both rosters already in the database):
   ```bash
   python -m fifa_analytics.cards.eafc26_datahub_importer "Manchester United" data/fifa.db "eafc26-datahub:main"
   python -m fifa_analytics.cards.eafc26_datahub_importer "Bayer 04 Leverkusen" data/fifa.db "eafc26-datahub:main"
   ```
3. Organize each match's screenshots under
   `data/screenshots/season_XX/matchweek_YY/match_ZZZZ/`:
   - `team_summary.png`
   - `team_events.png`
   - `player_summary_*.png` — one per player who featured, either team.
     Filenames don't need to encode who's in them or which team — each one's
     player is identified by OCR'ing the on-screen name and matching it
     against the two teams' rosters (`ocr/player_match.py`), not by filename.
4. **Before running OCR on real data**, calibrate the crop regions against your
   actual screenshots — the coordinates in `ocr/regions.py` are visual estimates,
   not pixel-measured:
   ```bash
   python -m fifa_analytics.ocr.calibrate player_summary path/to/a/real/screenshot.png
   python -m fifa_analytics.ocr.calibrate team_summary path/to/a/real/screenshot.png
   python -m fifa_analytics.ocr.calibrate team_events path/to/a/real/screenshot.png
   ```
   Each writes a `*_calibration.png` with the boxes overlaid — open it and adjust
   the fractional coordinates in `regions.py` until they line up.
5. Run the pipeline against a match folder:
   ```python
   from fifa_analytics.ocr.pipeline import run_match_dir
   run_match_dir("data/fifa.db", "data/screenshots/season_01/matchweek_03/match_0042",
                 match_id, "Manchester United", "Bayer 04 Leverkusen")
   ```
   Any player_summary screenshot whose name can't be matched to either roster
   still gets stored (stats intact, `player_id` left blank) rather than
   dropped — it'll show up in the validation step below for manual assignment.
6. Review and correct OCR output before it's trusted:
   ```bash
   streamlit run src/fifa_analytics/validate_app.py -- --db data/fifa.db
   ```
   This is also where unmatched player_summary captures get manually assigned
   to the correct roster player before being marked reviewed.

## Known gaps going into real use

- **Team Events layout is unconfirmed for matches with 2+ events** — only one
  single-event sample screenshot was available while building this. The pipeline
  currently just OCR-dumps the whole events band as raw text (see
  `ocr/regions.py`) rather than parsing structured (player, minute, event_type)
  rows. Send a screenshot from a match with multiple goals/cards to nail this down.
- **No xA (expected assists) field exists on any captured screen** — if
  expected-assist over/underperformance matters to the model, it isn't coming from
  OCR and would need another source or to be dropped.
- **A player transferred in your save won't be found under their new club** —
  confirmed with a real test: EAFC26-DataHub reflects each player's actual
  real-world club, not your Career Mode save's transfers. A player you've
  signed shows up in the dataset under their old (real-world) club, so
  `players_for_teams` — which only looks at the two teams already imported for
  this match — won't find them, and their screenshot lands in validate_app.py
  as unmatched. Currently there's no automatic fallback for this; if it comes
  up a lot, the fix would be searching the full dataset by name when the
  narrow roster search misses, then re-homing that player to the in-game team
  instead of their stale listed club.

## Database

`src/fifa_analytics/db/schema.sql` is the source of truth. Match stats are stored
in a normalized long format (`match_stat_values`: one row per `stat_name`/
`stat_value` pair per capture) rather than one wide table per screen type, since
the field set varies by tab/position and this way a layout change doesn't force a
migration.

## Tech stack

Python 3.11+, SQLite, OpenCV, EasyOCR, Streamlit, `requests` against the
EAFC26-DataHub CSV for card data. All free/local — see the project spec for
the full roadmap (true-overall modeling, team analysis, scouting engine,
local LLM assistant).
