"""Expected points (xPTS) tracking: converts each match's xG pair into
win/draw/loss probabilities via independent Poisson goal models, then into
expected points — the standard analytics approach for "did we deserve
that result?".

    P(score i-j) = Pois(i; xG_home) * Pois(j; xG_away)
    xPTS_home    = 3*P(home win) + 1*P(draw)

Aggregated over a season, xPTS vs actual points shows whether a team is
over- or under-performing its underlying play (spec §5). Results land in
the team_match_expected table (stubbed since Phase 1), one row per
(match, team), INSERT OR REPLACE so recomputes are idempotent.

Only reviewed team_summary captures feed this by default, same trust
boundary as the true-overall model; pass include_unreviewed=True (or
--include-unreviewed) to preview.

Usage:
    python -m fifa_analytics.analysis.xpts data/fifa.db [--include-unreviewed]
"""

import math
import sys
from collections import defaultdict

from fifa_analytics.db.models import connect

MAX_GOALS = 10  # per side; P(>10 goals) is negligible at any realistic xG


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def match_probabilities(xg_home: float, xg_away: float) -> tuple[float, float, float]:
    """Returns (P(home win), P(draw), P(away win))."""
    p_home = p_draw = p_away = 0.0
    for i in range(MAX_GOALS + 1):
        pi = _poisson_pmf(i, xg_home)
        for j in range(MAX_GOALS + 1):
            p = pi * _poisson_pmf(j, xg_away)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def expected_points(xg_for: float, xg_against: float) -> float:
    p_win, p_draw, _ = match_probabilities(xg_for, xg_against)
    return 3.0 * p_win + 1.0 * p_draw


def _actual_points(goals_for: int | None, goals_against: int | None) -> float | None:
    if goals_for is None or goals_against is None:
        return None
    if goals_for > goals_against:
        return 3.0
    if goals_for == goals_against:
        return 1.0
    return 0.0


def _load_match_xg(conn, include_unreviewed: bool) -> dict:
    """{match_id: {team_id: xg}} from team_summary captures."""
    reviewed_filter = "" if include_unreviewed else "AND oc.reviewed = 1"
    rows = conn.execute(
        f"""SELECT oc.match_id, oc.team_id, msv.stat_value AS xg
            FROM ocr_captures oc
            JOIN match_stat_values msv ON msv.capture_id = oc.capture_id
            WHERE oc.capture_type = 'team_summary'
              AND msv.stat_name = 'expected_goals'
              AND msv.stat_value IS NOT NULL
              {reviewed_filter}"""
    ).fetchall()
    per_match: dict[int, dict[int, float]] = defaultdict(dict)
    for row in rows:
        per_match[row["match_id"]][row["team_id"]] = float(row["xg"])
    return per_match


def compute_all(db_path: str, include_unreviewed: bool = False) -> int:
    """Writes team_match_expected rows for every match with xG captured for
    both teams. Returns rows written."""
    conn = connect(db_path)
    try:
        match_xg = _load_match_xg(conn, include_unreviewed)
        written = 0
        for match_id, team_xg in match_xg.items():
            match = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if match is None:
                continue
            home_id, away_id = match["home_team_id"], match["away_team_id"]
            if home_id not in team_xg or away_id not in team_xg:
                print(f"Match {match_id}: xG missing for one side — skipped.")
                continue

            for team_id, xg_for, xg_against, goals_for, goals_against in (
                (home_id, team_xg[home_id], team_xg[away_id], match["home_score"], match["away_score"]),
                (away_id, team_xg[away_id], team_xg[home_id], match["away_score"], match["home_score"]),
            ):
                conn.execute(
                    """INSERT OR REPLACE INTO team_match_expected
                       (match_id, team_id, expected_goals_for, expected_goals_against,
                        expected_points, actual_points)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        match_id,
                        team_id,
                        xg_for,
                        xg_against,
                        round(expected_points(xg_for, xg_against), 3),
                        _actual_points(goals_for, goals_against),
                    ),
                )
                written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def season_table(db_path: str) -> list[dict]:
    """Per-team season totals: xPTS vs actual points, xG for/against."""
    conn = connect(db_path)
    try:
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
        return [dict(row) for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m fifa_analytics.analysis.xpts <db_path> [--include-unreviewed]")
        sys.exit(1)
    count = compute_all(sys.argv[1], include_unreviewed="--include-unreviewed" in sys.argv)
    print(f"Wrote {count} team-match expected row(s).\n")
    for row in season_table(sys.argv[1]):
        over_under = (row["points"] or 0) - (row["xpts"] or 0)
        print(f"{row['team']:<28} P{row['matches']}  pts {row['points']}  xPTS {row['xpts']}  "
              f"({'+' if over_under >= 0 else ''}{over_under:.2f} vs expected)  "
              f"xG {row['xg_for']} : {row['xg_against']}")
