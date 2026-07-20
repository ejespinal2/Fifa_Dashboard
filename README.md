# FIFA Career Mode Analytics

A free, locally-run system that turns EA FC Career Mode post-match screenshots into
a "true overall" model for your squad — sub-attributes that evolve match by match
based on actual performance vs. card ratings — plus team analysis and squad
recommendations. See the full spec for the long-term vision; this repo currently
implements **Phase 1 (data foundation)**, **Phase 2 (true-overall model, v1)**,
**Phase 3 (team analysis: best XI + xPTS)**, **Phase 4 (scouting/transfer/
academy engine)**, **Phase 5 (the interactive dashboard)**, and **Phase 6
(the local AI assistant)** — the full spec roadmap.

## Phase 6: the AI assistant

The dashboard's **Assistant** tab is a chat over your career's data, powered
by [Ollama](https://ollama.com) — a free, fully local LLM runtime. Nothing
leaves your machine: no account, no API key, no cloud. One-time setup:

1. Install Ollama from https://ollama.com/download
2. `ollama pull llama3.2` (~2GB; `llama3.2:1b` is lighter, bigger models
   like `qwen2.5:14b` give better advice if your machine can run them)

What it's for, matching how it's grounded:

- **Squad picking on true overalls** — "pick my strongest XI", "should X
  start over Y?" The context pack includes your squad's true-vs-card
  overalls, confidence, and the best-XI solver's output.
- **Opponent-aware setup** — name any imported team ("how should I set up
  against Leverkusen?") and the pack adds the matchup engine's comparison:
  both best XIs, unit-level deltas (GK/defense/midfield/attack), their top
  threats, your weakest slots.
- **Transfers in and out** — "who should I sign/sell/loan?" pulls the
  Phase 4 transfer targets and academy prospects plus the surplus engine
  (bench players 5+ points below their group's starter: older = sale
  candidate, younger = loan/develop).
- **Understanding the models** — "what does xPTS mean?", "how is true
  overall calculated?" answered from a hand-written knowledge base
  (`assistant/knowledge.py`) that documents what the code actually does.

**Grounding design** (`assistant/context.py`): the LLM never generates SQL
and never free-recalls player data. Each question is keyword-routed to the
relevant deterministic engines, their output is serialized into the system
prompt as a context pack, and the model reasons over those numbers only —
the exact pack is shown under "Data used for this answer" in the chat.
Keyword routing (rather than LLM tool-calling) is deliberate: it behaves
identically across every Ollama model, including small ones. Without
Ollama running, the tab still computes and shows the data pack — you lose
the prose, not the numbers.

## Phase 5: the dashboard

```bash
streamlit run src/fifa_analytics/dashboard/app.py -- --db data/fifa.db
```

**Careers (multi-user / multi-save).** The sidebar's *Career* picker makes
the whole system repeatable: every career is its own database file under
`data/profiles/` (gitignored), so several people — or several save files —
share one install without sharing any data. *New career…* creates a fresh
file and runs the first-run wizard: pick your club from the real dataset's
club list (type-to-search), its full roster imports automatically, and
optionally the scouting pool too. A fresh career means every player is at
their real-world club by definition — that's the clean "reset everything
for a new person" path, and it never touches anyone else's career. The
`--db` file appears in the picker as "Default" for pre-profiles databases.
No deployment needed for multiple people: each person clones the repo and
runs locally (private, free), or shares one machine via careers.

Read-only Streamlit views over everything Phases 1-4 compute, one tab each
(pick a team in the sidebar; every view follows it):

- **Squad** — card overall vs latest modeled true overall per player, with
  the delta, model confidence, and how many matches of evidence it rests on.
- **Progression** — true-overall lines for up to 6 players at once, plus a
  per-player six-attribute detail chart. Gaps in an attribute line are
  evidence gating (no shots that match = no shooting score), not lost data.
- **Season (xPTS)** — the season table with an over/underperformance chart
  (points − xPTS), and a match-by-match breakdown for the selected team.
- **Best XI** — the Phase 3 solver behind a formation picker, including
  "best of all" across every known formation.
- **Scouting** — Phase 4 behind formation/tactic pickers: weakest slots,
  upgrade-only transfer targets per position group, and academy prospects
  with age/growth-room sliders.
- **Schedule** — the calendar/management view: add fixtures by date with a
  competition and venue, record results, and see your W/D/L record overall
  and per competition (points, GF/GA). Each fixture carries a screenshot
  folder — drop that day's images there and click *Process screenshots* to
  run the OCR pipeline on them from inside the dashboard, then review in
  `validate_app.py` and click *Recompute model + xPTS*. Selecting a fixture
  shows its **match facts**: the score line, the parsed event timeline
  (⚽ goals, ✅⚽/❌⚽ converted/missed penalties, 🟨/🟥 cards, 🔺/🔻 subs on/off,
  minute by minute, player and team named), and the team-summary stats
  side by side. Fat-fingered fixtures can be deleted along with anything
  attached to them.
- **How long OCR takes, and what NOT to do while it runs.** Figure roughly
  half a minute per player screenshot on a typical laptop CPU (each one is
  ~20 separate OCR reads, not one) — a full match's ~40 images is realistically
  10-25 minutes, plus a one-time minute or two on the very first run ever
  (EasyOCR downloads its model). Progress prints to the terminal you
  launched Streamlit from: `[player_summary 7/22] SHARE_...jpeg: exact
  (14.2s)`. **Do not click Process screenshots again while it's running** —
  the button doesn't visibly disable during the long blocking call, so a
  second click (or a page reload after the browser's connection times out
  on a long request) looks like the only way to unstick it, but it actually
  starts a full second pass in parallel: double every OCR call, and two
  writers racing the same database. A per-fixture lock file
  (`data/.ocr_locks/`, gitignored) now rejects a second concurrent run for
  the same fixture with a clear message instead of silently double-running
  — but the fix is not clicking twice, not a safety net to lean on. Two
  things that help the real wait: a CUDA GPU is auto-detected and used if
  present (3-5x faster; `FIFA_OCR_GPU=0` forces CPU if a detected GPU
  misbehaves), and re-running Process after adding a couple of forgotten
  screenshots only OCRs the new ones — everything already processed is
  hash-skipped instantly.
- **Screenshot filenames are optional.** Reserved names (`team_summary`,
  `team_events`, `team_events_2`…, `player_summary_*`, `player_gk_*`) are
  routed by name, exactly as always — and any *other* image in the folder
  is classified by content (`ocr/classify_screen.py`): the Player
  Performance screen by its header (its Goalkeeping tab by its save-stat
  labels), the team Summary by its stat labels, the Events tab by its
  minute-spine rows. Unparseable screens (e.g. the Possession tab's Threat
  timeline) are skipped with a printed note. If auto-classification ever
  gets one wrong, renaming the file to a reserved name overrides it.
- **Duplicate protection, three layers.** A per-fixture processing lock
  (above) stops two concurrent `run_match_dir` runs from ever racing each
  other. Independently, every image is content-hashed per match: the
  identical file dropped twice under different names is skipped outright,
  checked *before* the (much pricier) auto-classification step so a
  duplicate download costs nothing. And a player who already has a capture
  of a given type for a match is never given a second one from a
  *different* screenshot — that would silently double their stats in the
  model. All three print exactly what happened and why.
- **Goalkeepers get their own tab.** Capture the keeper's Player
  Performance → **Goalkeeping** tab alongside their Summary tab. Its
  save stats (shots against/on target, saves, save success rate, penalty
  saves, punch/rush/claim work) feed the model's GK scoring: shot-stopping
  evidence flows into `defending` and box-command into `physical` — the
  two attributes GK overalls lean on — with the tab's own Goalkeeper
  Rating blended in like the match rating. The Summary tab is still
  needed (it carries minutes played, which weights all evidence).
- **Manage** — player search across every roster (regens included) with a
  transfer control, for moves you know happened in your save but haven't
  captured yet (the OCR pipeline re-homes players automatically when it
  *sees* them; this gets ahead of it). Also: one-click card-data import for
  a new opponent, a settings section (the base screenshots folder every
  fixture's suggested subfolder lives under — point it anywhere, e.g.
  `C:\Users\you\FIFA_Screenshots`), and a danger zone that resets all
  match data (matches, captures, stats, model history, xPTS, seasons)
  while keeping teams, players, and the scouting pool — gated behind
  typing `RESET`. For a full from-scratch restart (players back at their
  original clubs), start a new career instead — a re-import can't restore
  rosters in place, since `upsert_player` keys on (name, source, team) and
  a within-save transferee would just duplicate.

Design decisions worth knowing:

- **Write boundary.** The five analysis tabs never write. Schedule and
  Manage write only on explicit button clicks, through the same helpers in
  `db/models.py` the CLIs use. Per-stat OCR corrections deliberately stay
  in `validate_app.py`, which remains the trust gate for model inputs —
  the analysis tabs still only reflect reviewed data.
- The app runs `init_db` at startup (idempotent), so schema migrations —
  like the `matches.competition` column the schedule needs — apply
  automatically when you pull an update.
- Data access lives in `dashboard/queries.py` (plain functions, no
  streamlit) so every view's logic is unit-testable; `dashboard/app.py` is
  UI only, smoke-tested end-to-end with streamlit's `AppTest` against both
  an empty and a populated database.
- Database connections are opened per rerun, *not* cached with
  `st.cache_resource` — sqlite connections are bound to their creating
  thread and Streamlit reruns land on arbitrary threads, so a cached
  connection eventually crashes every view (found live; `validate_app.py`
  had the same latent bug and got the same fix).
- Two spec §8 items are deliberately absent: the heatmap viewer (Phase 1's
  capture scope has no heatmap screenshots, so there's nothing to show) and
  the chat box (that's Phase 6's local-LLM assistant).

## Phase 4: scouting, transfer targets, and academy prospects

A pool of external candidates — the full EAFC26-DataHub player list minus
whoever's already on your imported squads — scored two different ways,
matching the spec's §6 distinction between "who should I sign" and "who's
worth developing":

```bash
python -m fifa_analytics.cards.scouting_importer data/fifa.db "eafc26-datahub:main" \
    --exclude-team "Manchester United" --exclude-team "Bayer 04 Leverkusen"
```

Re-running replaces the previous snapshot for that `source` label rather than
accumulating duplicates (`clear_scouting_candidates` before each import).

- **Transfer targets** (`analysis/scouting.py: transfer_targets`) — find your
  current best XI's weakest slot in each position group (reusing Phase 3's
  `best_xi.pick_best_xi`), then search the candidate pool for anyone who
  clears two bars: **plausible for the position** (familiarity ≥ 0.7 against
  the slot — a CB is never suggested for a striker gap, regardless of stats)
  and **an actual upgrade** (tactic-weighted composite × familiarity beats the
  current incumbent's effective rating). One result set per *distinct* weak
  group, not per slot, so a formation with two CB slots doesn't repeat the
  search.
- **Tactical fit** (`analysis/tactics.py`) — the position-based attribute
  weights from Phase 2 (`model/features.OVERALL_WEIGHTS`) get nudged toward
  what a chosen tactic demands (`possession` values passing/dribbling over
  physicality, `direct_play` the reverse, etc.) before scoring a candidate,
  then renormalized back to summing to 1. This is the piece the spec's Phase 3
  called for but Phase 3's best-XI solver didn't need yet — it only became
  necessary once transfer scoring had to answer "a good CB for *my* system,"
  not just "a good CB."
- **Academy/loan prospects** (`analysis/scouting.py: academy_prospects`) — a
  deliberately different, more lenient bar: no requirement to beat anyone.
  Just growth room (`potential - current_overall >= min_potential_gap`, a
  young age ceiling) plus a floor (`current_overall >= 60`) so a prospect
  with a great trajectory but nothing today doesn't get suggested as a body
  that could actually hurt the team if thrown in. `fit_score` isn't persisted
  on `scouting_candidates` — it depends on your current squad and chosen
  tactic, both of which change over time, so it's computed fresh on every
  query instead of going stale in the table.

```python
from fifa_analytics.db.models import connect
from fifa_analytics.analysis.scouting import identify_weak_slots, transfer_targets, academy_prospects

conn = connect("data/fifa.db")
identify_weak_slots(conn, team_id, "4-3-3")                       # weakest first
transfer_targets(conn, team_id, "4-3-3", tactic="possession")     # {group: [candidates]}
academy_prospects(conn, min_potential_gap=8, max_age=21)
```

Verified end-to-end against the live EAFC26-DataHub CSV (Man Utd's real
current-form roster vs. the ~18,000-player external pool, both clubs
excluded from their own candidate search): transfer targets correctly
surfaced real upgrades at other real clubs for each weak slot (e.g. a
current-form winger and striker beaten by better options elsewhere) while
respecting position familiarity, and academy prospects returned young,
high-potential, floor-clearing players sorted by growth room. Yes, this also
works for opponent teams — nothing in `transfer_targets`/`academy_prospects`
is Man-Utd-specific, so importing an opposing team's roster and passing
*their* `team_id` scouts for them the same way; the candidate pool naturally
excludes whichever club(s) you pass to `--exclude-team` at import time.

## Phase 3: team analysis

**Best starting XI** — optimal assignment of your squad to a formation's 11
slots, maximizing total (rating × positional fit), solved exactly with the
Hungarian algorithm rather than greedily:

```bash
python -m fifa_analytics.analysis.best_xi data/fifa.db "Manchester United"            # best across all formations
python -m fifa_analytics.analysis.best_xi data/fifa.db "Manchester United" 4-2-3-1    # a specific one
```

Ratings prefer the model's latest true overall, falling back to card
overalls for players without match history. Positional-fit multipliers and
the formation list live in `analysis/formations.py` — add a formation by
adding one line. Out-of-position picks are marked in the output.

**xPTS tracking** — each match's captured xG pair becomes win/draw/loss
probabilities via independent Poisson goal models, then expected points,
stored per (match, team) in `team_match_expected`:

```bash
python -m fifa_analytics.analysis.xpts data/fifa.db
```

prints the season table: points vs xPTS (over/under-performance) and total
xG for/against. Same trust boundary as the model: reviewed captures only by
default, `--include-unreviewed` to preview.

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

If you've had a database since before Phase 4, re-run that `init_db` line
after pulling — `scouting_candidates` grew several columns in Phase 4, and
since schema.sql uses `CREATE TABLE IF NOT EXISTS`, a table that already
existed in the old shape stays that way forever without an explicit
recreate. `init_db` now detects and migrates this automatically (dropping
and recreating just that one table — it's a disposable, source-refreshed
snapshot, never hand-edited data like your players/matches).

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

- **Team Events: any number of events, any number of scrolled screenshots,
  layout calibrated against real multi-event captures.** The Events tab
  lays events out on a center spine — minute circles in the middle, the
  HOME team's events extending left and the AWAY team's right (matching
  the header order), so the side a name sits on IS its team. Every row
  becomes a structured `match_events` entry with its icon classified from
  that side's icon zone at the row's own height: `goal` (white ball),
  `missed_penalty` (ball with an ✗) and `penalty_goal` (ball with a ✓ —
  a converted penalty): both are all white in the real UI, so a wide
  white blob marks a penalty icon and the glyph's top corners tell ✗
  (both filled) from ✓ (top-left empty); a red-X color variant also
  reads as missed, `yellow_card`/`red_card` (EA's color-coding —
  still unverified against a real card capture), and substitutions, which
  store TWO events: `sub_on` (the entering player, named at the minute)
  and `sub_off` (the outgoing player, from the hanging line below). 'HT'
  markers and scroll arrows are skipped. Capture a long list in scrolled
  sections (`team_events.png`, `team_events_2.png`, ... or unrenamed —
  auto-classification catches them); overlapping rows dedupe on (player,
  minute, type). Raw text is always kept per capture regardless.
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
- **Goalkeepers never surface as transfer targets.** The EAFC26-DataHub CSV
  doesn't populate the six outfield sub-attributes (pace/shooting/passing/
  dribbling/defending/physical) for GK cards — they're blank for every
  keeper in the dataset, so `attribute_composite` (which requires all six)
  always returns `None` and no keeper ever scores. Confirmed against the live
  CSV: real keepers like Ederson and Maignan appear in the pool with all six
  attributes `None`. Fixing this needs GK-specific attributes (diving/
  handling/kicking/reflexes) added to the schema and scoring model — a
  bigger change than Phase 4's scope, noted here rather than silently
  producing an empty GK result.

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
