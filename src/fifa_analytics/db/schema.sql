-- FIFA Career Mode Analytics — Phase 1 schema
-- Normalized "long format" for match stats: EA's on-screen fields vary by tab
-- and by position (GK-only fields), and this repo only captures Player Summary
-- + Team Summary + Team Events for now. A fixed wide table would need a
-- migration every time a captured field set changes; stat_name/stat_value
-- does not.

CREATE TABLE IF NOT EXISTS teams (
    team_id   INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    league    TEXT
);

CREATE TABLE IF NOT EXISTS players (
    player_id       INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    team_id         INTEGER REFERENCES teams(team_id),   -- current club, from the card-data import
    position        TEXT NOT NULL,          -- GK/CB/FB/DM/CM/AM/W/ST
    jersey_number   INTEGER,
    -- Nullable: a Career Mode academy graduate/regen has no card-data source
    -- at all, so this starts NULL and gets backfilled once the true-overall
    -- model (Phase 2) or a manual entry has something to put here.
    base_overall    INTEGER,
    base_pace       INTEGER,
    base_shooting   INTEGER,
    base_passing    INTEGER,
    base_dribbling  INTEGER,
    base_defending  INTEGER,
    base_physical   INTEGER,
    age             INTEGER,
    potential       INTEGER,
    source          TEXT,                   -- e.g. 'eafc26-datahub:main'
    UNIQUE(name, team_id, source)
);

CREATE TABLE IF NOT EXISTS seasons (
    season_id   INTEGER PRIMARY KEY,
    year_label  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS matches (
    match_id       INTEGER PRIMARY KEY,
    season_id      INTEGER NOT NULL REFERENCES seasons(season_id),
    matchweek      INTEGER NOT NULL,
    home_team_id   INTEGER NOT NULL REFERENCES teams(team_id),
    away_team_id   INTEGER NOT NULL REFERENCES teams(team_id),
    home_score     INTEGER,
    away_score     INTEGER,
    date           TEXT,                     -- ISO yyyy-mm-dd; the calendar/schedule view keys off this
    competition    TEXT,                     -- 'Premier League', 'FA Cup', ... for per-competition W/D/L records
    screenshot_dir TEXT NOT NULL             -- data/screenshots/... path, for traceability back to source images
);

-- One row per screenshot actually captured (a "capture"). Numeric stats and
-- validation state hang off this, not off the match/player pair directly,
-- since the same match+player can in principle be recaptured (e.g. a bad OCR
-- read redone from a fresh screenshot).
CREATE TABLE IF NOT EXISTS ocr_captures (
    capture_id          INTEGER PRIMARY KEY,
    match_id            INTEGER NOT NULL REFERENCES matches(match_id),
    capture_type        TEXT NOT NULL CHECK (capture_type IN ('player_summary', 'team_summary', 'team_events')),
    player_id           INTEGER REFERENCES players(player_id),   -- set for player_summary once matched, NULL otherwise
    team_id             INTEGER REFERENCES teams(team_id),       -- set for team_summary and (once its header is matched) player_summary; NULL for team_events
    screenshot_path     TEXT NOT NULL,
    ocr_confidence_avg  REAL,
    raw_text            TEXT,               -- unparsed OCR dump; only used for team_events (see ocr/regions.py)
    -- player_summary only: how player_id was resolved -- 'exact'/'surname'/
    -- 'fuzzy' (roster match), 'reassigned' (re-homed from another club in
    -- the full dataset), 'new_player' (no card data anywhere, bare record
    -- created), or 'unresolved_team' (needs manual assignment). See
    -- ocr/pipeline.py's module docstring for the full fallback chain.
    match_confidence    TEXT,
    reviewed            INTEGER NOT NULL DEFAULT 0,
    reviewed_at         TEXT
);

-- Every stat visible on a Player Summary or Team Summary screen, one row per
-- field. E.g. (capture_id=1, stat_name='goals', stat_value=1).
CREATE TABLE IF NOT EXISTS match_stat_values (
    capture_id     INTEGER NOT NULL REFERENCES ocr_captures(capture_id),
    stat_name      TEXT NOT NULL,
    stat_value     REAL,
    ocr_confidence REAL,
    PRIMARY KEY (capture_id, stat_name)
);

-- Parsed from the team_events capture: one row per goal/assist/card event.
CREATE TABLE IF NOT EXISTS match_events (
    event_id    INTEGER PRIMARY KEY,
    match_id    INTEGER NOT NULL REFERENCES matches(match_id),
    capture_id  INTEGER NOT NULL REFERENCES ocr_captures(capture_id),
    team_id     INTEGER REFERENCES teams(team_id),
    player_id   INTEGER REFERENCES players(player_id),
    minute      INTEGER,
    event_type  TEXT NOT NULL              -- 'goal' | 'assist' | 'yellow_card' | 'red_card'
);

-- Stubbed for later phases — created now, left empty, so Phase 2+ don't need
-- a migration to start writing to them.
CREATE TABLE IF NOT EXISTS true_overall_history (
    player_id        INTEGER NOT NULL REFERENCES players(player_id),
    match_id         INTEGER NOT NULL REFERENCES matches(match_id),
    true_overall     REAL,
    true_pace        REAL,
    true_shooting    REAL,
    true_passing     REAL,
    true_dribbling   REAL,
    true_defending   REAL,
    true_physical    REAL,
    confidence_score REAL,
    PRIMARY KEY (player_id, match_id)
);

CREATE TABLE IF NOT EXISTS team_match_expected (
    match_id             INTEGER NOT NULL REFERENCES matches(match_id),
    team_id              INTEGER NOT NULL REFERENCES teams(team_id),
    expected_goals_for   REAL,
    expected_goals_against REAL,
    expected_points      REAL,
    actual_points        REAL,
    PRIMARY KEY (match_id, team_id)
);

-- Refreshable snapshot of the external card dataset, minus whoever's
-- already on your own imported squads. fit_score is NOT stored here: it's
-- always relative to your CURRENT squad's weaknesses and chosen tactic, so
-- a persisted value would go stale the moment either changes -- see
-- analysis/scouting.py, which computes it on demand from the columns below.
CREATE TABLE IF NOT EXISTS scouting_candidates (
    candidate_id     INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    club_name        TEXT,                  -- their current real-world club, per the source
    source           TEXT,
    position         TEXT,                  -- preferred position, same convention as players.position
    age              INTEGER,
    current_overall  INTEGER,
    potential        INTEGER,
    base_pace        INTEGER,
    base_shooting    INTEGER,
    base_passing     INTEGER,
    base_dribbling   INTEGER,
    base_defending   INTEGER,
    base_physical    INTEGER,
    estimated_wage   REAL,
    UNIQUE(name, club_name, source)
);

-- Per-career app settings (my club, screenshot base folder, ...). One row
-- per key; the dashboard is the only writer.
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
