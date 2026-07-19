"""Read-only data access for the Phase 5 dashboard.

Plain functions returning lists of dicts, no streamlit imports — so every
view's data logic is unit-testable without spinning up the UI. The
dashboard never writes: OCR corrections belong in validate_app.py, model
recomputes in the model/analysis CLIs.
"""

import sqlite3


def teams_with_players(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT t.team_id, t.name, COUNT(p.player_id) AS player_count
           FROM teams t JOIN players p ON p.team_id = t.team_id
           GROUP BY t.team_id ORDER BY t.name"""
    ).fetchall()
    return [dict(row) for row in rows]


def squad_overview(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    """One row per player: card overall vs latest modeled true overall
    (NULL until they have reviewed match history), delta, and how much
    evidence the model has seen."""
    rows = conn.execute(
        """SELECT p.player_id, p.name, p.position, p.base_overall,
                  latest.true_overall, latest.confidence_score,
                  (SELECT COUNT(*) FROM true_overall_history h2
                   WHERE h2.player_id = p.player_id) AS matches_modeled
           FROM players p
           LEFT JOIN (
               SELECT toh.player_id, toh.true_overall, toh.confidence_score
               FROM true_overall_history toh
               WHERE toh.match_id = (SELECT MAX(h.match_id) FROM true_overall_history h
                                     WHERE h.player_id = toh.player_id)
           ) latest ON latest.player_id = p.player_id
           WHERE p.team_id = ?
           ORDER BY COALESCE(latest.true_overall, p.base_overall) DESC""",
        (team_id,),
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["delta"] = (
            round(d["true_overall"] - d["base_overall"], 1)
            if d["true_overall"] is not None and d["base_overall"] is not None
            else None
        )
        out.append(d)
    return out


def player_progression(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    """Long format for the multi-player progression chart: one row per
    (player, modeled match), ordered within each player by match_id with a
    running match number as the x-axis (matchweek alone would collide once
    there's more than one season)."""
    rows = conn.execute(
        """SELECT p.name AS player, m.matchweek, toh.match_id, toh.true_overall
           FROM true_overall_history toh
           JOIN players p ON p.player_id = toh.player_id
           JOIN matches m ON m.match_id = toh.match_id
           WHERE p.team_id = ?
           ORDER BY p.name, toh.match_id""",
        (team_id,),
    ).fetchall()
    out, counter = [], {}
    for row in rows:
        d = dict(row)
        counter[d["player"]] = counter.get(d["player"], 0) + 1
        d["match_number"] = counter[d["player"]]
        out.append(d)
    return out


ATTRIBUTE_COLUMNS = {
    "pace": "true_pace",
    "shooting": "true_shooting",
    "passing": "true_passing",
    "dribbling": "true_dribbling",
    "defending": "true_defending",
    "physical": "true_physical",
}


def attribute_progression(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    """Long format for one player's six-attribute chart: one row per
    (match, attribute) that the model actually scored (evidence-gated
    attributes stay None on quiet matches and are skipped, not zeroed)."""
    rows = conn.execute(
        """SELECT toh.*, m.matchweek FROM true_overall_history toh
           JOIN matches m ON m.match_id = toh.match_id
           WHERE toh.player_id = ? ORDER BY toh.match_id""",
        (player_id,),
    ).fetchall()
    out = []
    for i, row in enumerate(rows, start=1):
        for attribute, column in ATTRIBUTE_COLUMNS.items():
            if row[column] is not None:
                out.append(
                    {"match_number": i, "matchweek": row["matchweek"],
                     "attribute": attribute, "value": row[column]}
                )
    return out


def season_xpts_table(conn: sqlite3.Connection) -> list[dict]:
    """Per-team season totals from team_match_expected, with the
    over/underperformance delta precomputed."""
    rows = conn.execute(
        """SELECT t.name AS team, COUNT(*) AS matches,
                  ROUND(SUM(tme.expected_points), 2) AS xpts,
                  SUM(tme.actual_points) AS points,
                  ROUND(SUM(tme.expected_goals_for), 2) AS xg_for,
                  ROUND(SUM(tme.expected_goals_against), 2) AS xg_against
           FROM team_match_expected tme
           JOIN teams t ON t.team_id = tme.team_id
           GROUP BY tme.team_id
           ORDER BY SUM(tme.actual_points) DESC"""
    ).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["delta"] = round((d["points"] or 0) - (d["xpts"] or 0), 2)
        out.append(d)
    return out


def team_match_xpts(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    """Per-match xPTS vs actual for one team, opponent named."""
    rows = conn.execute(
        """SELECT m.matchweek, tme.match_id,
                  opp.name AS opponent,
                  tme.expected_goals_for AS xg_for,
                  tme.expected_goals_against AS xg_against,
                  tme.expected_points AS xpts, tme.actual_points AS points
           FROM team_match_expected tme
           JOIN matches m ON m.match_id = tme.match_id
           JOIN teams opp ON opp.team_id = CASE
               WHEN m.home_team_id = tme.team_id THEN m.away_team_id
               ELSE m.home_team_id END
           WHERE tme.team_id = ?
           ORDER BY tme.match_id""",
        (team_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def scouting_pool_size(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM scouting_candidates").fetchone()[0]


def all_teams(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT team_id, name FROM teams ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def schedule(conn: sqlite3.Connection) -> list[dict]:
    """Every fixture, dated ones first (newest at top), undated at the
    bottom — with how many screenshot captures have landed against each, so
    the schedule shows which matches still need their images processed."""
    rows = conn.execute(
        """SELECT m.match_id, m.date, m.competition, m.matchweek,
                  th.name AS home_team, ta.name AS away_team,
                  m.home_score, m.away_score, m.screenshot_dir,
                  (SELECT COUNT(*) FROM ocr_captures oc WHERE oc.match_id = m.match_id) AS captures
           FROM matches m
           JOIN teams th ON th.team_id = m.home_team_id
           JOIN teams ta ON ta.team_id = m.away_team_id
           ORDER BY m.date IS NULL, m.date DESC, m.match_id DESC"""
    ).fetchall()
    return [dict(row) for row in rows]


def team_record(conn: sqlite3.Connection, team_id: int) -> list[dict]:
    """W/D/L + goals for/against for one team, one row per competition plus
    an 'All competitions' total row first. Only matches with both scores
    recorded count."""
    rows = conn.execute(
        """SELECT COALESCE(m.competition, '(no competition)') AS competition,
                  CASE WHEN m.home_team_id = ? THEN m.home_score ELSE m.away_score END AS gf,
                  CASE WHEN m.home_team_id = ? THEN m.away_score ELSE m.home_score END AS ga
           FROM matches m
           WHERE (m.home_team_id = ? OR m.away_team_id = ?)
             AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL""",
        (team_id, team_id, team_id, team_id),
    ).fetchall()
    if not rows:
        return []

    def tally(subset):
        wins = sum(1 for r in subset if r["gf"] > r["ga"])
        draws = sum(1 for r in subset if r["gf"] == r["ga"])
        losses = sum(1 for r in subset if r["gf"] < r["ga"])
        return {
            "played": len(subset), "W": wins, "D": draws, "L": losses,
            "GF": sum(r["gf"] for r in subset), "GA": sum(r["ga"] for r in subset),
            "points": 3 * wins + draws,
        }

    competitions = sorted({r["competition"] for r in rows})
    out = [{"competition": "All competitions", **tally(rows)}]
    for comp in competitions:
        out.append({"competition": comp, **tally([r for r in rows if r["competition"] == comp])})
    return out


def match_events_list(conn: sqlite3.Connection, match_id: int) -> list[dict]:
    """Every parsed event for one match, minute order — the match-facts
    timeline (goals, cards, subs, missed penalties)."""
    rows = conn.execute(
        """SELECT me.minute, me.event_type,
                  p.name AS player, t.name AS team
           FROM match_events me
           LEFT JOIN players p ON p.player_id = me.player_id
           LEFT JOIN teams t ON t.team_id = me.team_id
           WHERE me.match_id = ?
           ORDER BY me.minute IS NULL, me.minute""",
        (match_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def match_team_stats(conn: sqlite3.Connection, match_id: int) -> list[dict]:
    """The match's team_summary stats side by side: one row per stat with
    home and away values (either side None if its capture is missing)."""
    match = conn.execute(
        "SELECT home_team_id, away_team_id FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    if match is None:
        return []
    rows = conn.execute(
        """SELECT msv.stat_name, oc.team_id, msv.stat_value
           FROM ocr_captures oc
           JOIN match_stat_values msv ON msv.capture_id = oc.capture_id
           WHERE oc.match_id = ? AND oc.capture_type = 'team_summary'""",
        (match_id,),
    ).fetchall()
    by_stat: dict[str, dict] = {}
    for row in rows:
        entry = by_stat.setdefault(row["stat_name"], {"stat": row["stat_name"], "home": None, "away": None})
        if row["team_id"] == match["home_team_id"]:
            entry["home"] = row["stat_value"]
        elif row["team_id"] == match["away_team_id"]:
            entry["away"] = row["stat_value"]
    return sorted(by_stat.values(), key=lambda e: e["stat"])


def search_players(conn: sqlite3.Connection, text: str, limit: int = 25) -> list[dict]:
    """Case-insensitive substring search across ALL players in the
    database (every imported roster + regens), current team named."""
    rows = conn.execute(
        """SELECT p.player_id, p.name, p.position, p.base_overall, p.age,
                  t.name AS team
           FROM players p LEFT JOIN teams t ON t.team_id = p.team_id
           WHERE p.name LIKE ? ORDER BY p.base_overall DESC NULLS LAST LIMIT ?""",
        (f"%{text}%", limit),
    ).fetchall()
    return [dict(row) for row in rows]
