# FIFA Career Mode Analytics

A free, locally-run system that turns EA FC Career Mode post-match screenshots into
a "true overall" model for your squad — sub-attributes that evolve match by match
based on actual performance vs. card ratings — plus team analysis and squad
recommendations. See the full spec for the long-term vision; this repo currently
implements **Phase 1 (data foundation)** and **Phase 2 (true-overall model, v1)**.

## Phase 2: the true-overall model

`python -m fifa_analytics.model.true_overall data/fifa.db` recomputes every
player's per-match true-attribute history into `true_overall_history` (safe to
re-run any time — rows are replaced, not duplicated). How it works:

- **Per-match performance scores** (`model/features.py`): each of the six
  sub-attributes gets a 0-1 score from that match's captured stats, but only
  when there's *evidence* — a striker who took no shots produces no shooting
  score that match, rather than a bad one. Volume stats are normalized per-90
  so substitute appearances aren't judged as half-performances, and the
  in-game match rating blends into every scored attribute as EA's holistic
  read on the performance.
- **Bayesian blending** (`model/true_overall.py`): true attribute =
  card value (prior) blended with accumulated performance, weighted by
  minutes of evidence — `w = n_eff / (n_eff + 5)` where n_eff is full-match
  equivalents. One match barely moves a rating; a season of matches dominates
  it. This is the spec's §4.1 cold-start design.
- **Position-weighted overall**: a CB's composite weights defending/physical
  heavily, a winger's weights pace/dribbling (`OVERALL_WEIGHTS`), mirroring
  how EA weights card overalls by position.
- **Regens** (academy players with no card data) get pure performance
  estimates with low confidence instead of a prior.
- Only **reviewed** captures feed the model by default — unreviewed OCR is
  excluded with a printed note (`--include-unreviewed` to preview anyway).

The spec's ridge-regression upgrade (§4.3) needs multi-season history to
train against; this interpretable blend is the intended v1, not a shortcut.

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
     Filenames don't need to encode who's in them or which team — each
     screenshot identifies itself, in 3 steps (see `ocr/pipeline.py`'s module
     docstring for the full chain):
     1. OCR the header team name/crest, match it against the match's two
        team names (`ocr/team_match.py`) — tells us which roster to check.
     2. Match the OCR'd player name against that team's already-imported
        roster (`ocr/player_match.py`) — the common case.
     3. If no roster match: search the *entire* card dataset by name. A hit
        means this player was transferred within your save (the dataset
        still lists them under their old real-world club) — they get
        automatically re-imported under the correct in-game team, so their
        *next* appearance matches directly in step 2. A miss means nobody's
        ever heard of them (a Career Mode academy graduate) — a bare player
        row is created with just the name and team; card attributes stay
        blank until a later phase or manual entry fills them in.
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
   The only case that still needs a human is when even the *team* can't be
   told apart (step 1 above fails, e.g. a badly garbled header read) — that
   screenshot still gets stored (stats intact, `player_id`/`team_id` left
   blank) rather than dropped.
6. Review and correct OCR output before it's trusted:
   ```bash
   streamlit run src/fifa_analytics/validate_app.py -- --db data/fifa.db
   ```
   This is also where fully-unresolved captures get manually assigned to the
   correct player. Captures that *were* auto-resolved but via the fuzzy,
   reassigned, or brand-new-player paths still show up with a flagged note
   ("worth a double-check") rather than looking identical to a confident
   exact match.

## Known gaps going into real use

- **Team Events: goals vs. cards are distinguished, but only for one event
  per screenshot, and card colors are unverified.** `ocr/event_parse.py`
  parses the player name + minute out of the raw OCR text, matches the
  player against both rosters to get their team, and classifies the
  event icon by color (`goal` = achromatic ball icon — confirmed against a
  real screenshot; `yellow_card`/`red_card` = EA's standard color-coding —
  plausible but not yet checked against an actual card-event screenshot).
  A structured row lands in `match_events` when all of that resolves; the
  raw text is always kept regardless. What's still unconfirmed is whether a
  match with 2+ events shows them as multiple rows in the same screenshot
  (in which case only the first would currently be parsed) or one at a time
  via the toggle controls visible in the screenshot — send a multi-event
  screenshot to settle this.
- **No xA (expected assists) field exists on any captured screen** — if
  expected-assist over/underperformance matters to the model, it isn't coming from
  OCR and would need another source or to be dropped.
- **The `team_header` crop region (used to tell which team a player_summary
  screenshot belongs to) is unverified against a real screenshot** — like the
  other regions before their first calibration pass, this one's a visual
  estimate. If matching keeps landing on `unresolved_team`, this is the first
  place to check with `ocr/calibrate.py`.
- **Transferred players are handled, with one caveat**: a player re-homed via
  the full-dataset fallback (see above) is matched on an exact name, or a
  surname+first-initial match that's unique across the whole ~18,000-player
  dataset — deliberately conservative, since a wrong guess silently attaches
  someone else's attributes. A transferred player with a very common
  name+initial combination could still fail both tiers and fall through to
  `new_player` (a bare record, no card stats) instead of `reassigned`. Verified
  end-to-end with a real case (a player transferred to Man Utd in a save,
  correctly found under their actual real-world club and re-homed with their
  real attributes intact).

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
