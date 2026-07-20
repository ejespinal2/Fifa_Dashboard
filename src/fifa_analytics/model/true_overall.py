"""Phase 2: the "true overall" model — Bayesian blend of card attributes
(the prior) and accumulated match-performance evidence.

For each player and each of their matches (in match_id order), this
computes a running estimate per sub-attribute:

    w_data      = n_eff / (n_eff + PRIOR_STRENGTH)
    true_attr   = (1 - w_data) * base_attr + w_data * weighted_mean(perf)

where n_eff is the sum of minutes/90 across matches that produced evidence
for that attribute (see features.score_match_performance's evidence
gating), and weighted_mean weights each match's performance rating by its
minutes. Early in a season w_data is tiny, so one great or terrible match
barely moves the estimate off the card value; over a season the data side
takes over. This is the cold-start design from the project spec (§4.1).

Regens/academy players (base_attr is NULL) skip the prior entirely: their
true_attr is the pure performance mean, and their confidence_score reflects
only the accumulated evidence.

The composite true_overall is the position-weighted sum of the six
attributes (features.OVERALL_WEIGHTS), using the card value for attributes
that have no match evidence yet — and skipping weight-renormalization
gymnastics by treating "no evidence and no card value" (regen edge case) as
the performance-scale midpoint.

One row lands in true_overall_history per (player, match), so progression
can be charted over the season. Rows are INSERT OR REPLACE'd — recomputing
is idempotent and safe to run after every match or after correcting values
in the validation UI.

Only reviewed captures are used by default — the whole point of the
validation step is that unreviewed OCR output isn't trusted by the model.
Pass include_unreviewed=True (or --include-unreviewed on the CLI) to
preview against raw OCR output anyway.

The spec's ridge-regression upgrade path (§4.3) needs multiple seasons of
accumulated history to train against; this interpretable blend is v1 by
design, not a shortcut.
"""

import sys
from collections import defaultdict

from fifa_analytics.db.models import connect
from fifa_analytics.model.features import (
    ATTRIBUTES,
    OVERALL_WEIGHTS,
    PERF_RATING_FLOOR,
    PERF_RATING_SPAN,
    perf_score_to_rating,
    position_group,
    score_match_performance,
)

# n_eff (in full-90 match equivalents) at which performance evidence and the
# card prior carry equal weight. ~5 full matches = 50/50.
PRIOR_STRENGTH = 5.0


def _load_player_match_stats(conn, include_unreviewed: bool):
    """Returns {player_id: [(match_id, {stat_name: value}), ...]} ordered by
    match_id, plus a count of captures skipped for being unreviewed."""
    reviewed_filter = "" if include_unreviewed else "AND oc.reviewed = 1"
    rows = conn.execute(
        f"""SELECT oc.player_id, oc.match_id, msv.stat_name, msv.stat_value
            FROM ocr_captures oc
            JOIN match_stat_values msv ON msv.capture_id = oc.capture_id
            WHERE oc.capture_type IN ('player_summary', 'player_gk')
              AND oc.player_id IS NOT NULL
              {reviewed_filter}
            ORDER BY oc.match_id"""
    ).fetchall()

    skipped = 0
    if not include_unreviewed:
        skipped = conn.execute(
            """SELECT COUNT(DISTINCT capture_id) FROM ocr_captures
               WHERE capture_type IN ('player_summary', 'player_gk') AND player_id IS NOT NULL AND reviewed = 0"""
        ).fetchone()[0]

    per_player: dict[int, dict[int, dict]] = defaultdict(dict)
    for row in rows:
        per_player[row["player_id"]].setdefault(row["match_id"], {})[row["stat_name"]] = row["stat_value"]

    ordered = {
        player_id: sorted(matches.items())
        for player_id, matches in per_player.items()
    }
    return ordered, skipped


def _base_attributes(player_row) -> dict:
    return {
        "pace": player_row["base_pace"],
        "shooting": player_row["base_shooting"],
        "passing": player_row["base_passing"],
        "dribbling": player_row["base_dribbling"],
        "defending": player_row["base_defending"],
        "physical": player_row["base_physical"],
    }


def compute_player_history(player_row, match_stats: list) -> list[dict]:
    """match_stats: [(match_id, {stat_name: value}), ...] in order. Returns
    one dict per match with the running true attributes after that match.
    """
    base = _base_attributes(player_row)
    weights = OVERALL_WEIGHTS[position_group(player_row["position"])]

    # Running evidence per attribute: n_eff and minutes-weighted perf sum.
    n_eff = {attr: 0.0 for attr in ATTRIBUTES}
    perf_sum = {attr: 0.0 for attr in ATTRIBUTES}

    history = []
    for match_id, stats in match_stats:
        minutes = stats.get("minutes_played_vs_team_avg") or 0.0
        match_weight = min(minutes / 90.0, 1.0)
        for attr, score in score_match_performance(stats).items():
            n_eff[attr] += match_weight
            perf_sum[attr] += match_weight * perf_score_to_rating(score)

        true_attrs = {}
        for attr in ATTRIBUTES:
            if n_eff[attr] > 0:
                perf_mean = perf_sum[attr] / n_eff[attr]
                w_data = n_eff[attr] / (n_eff[attr] + PRIOR_STRENGTH)
                if base[attr] is not None:
                    true_attrs[attr] = (1 - w_data) * base[attr] + w_data * perf_mean
                else:
                    true_attrs[attr] = perf_mean
            else:
                # No evidence yet: hold the card value; regen with neither
                # gets the performance-scale midpoint as a placeholder.
                true_attrs[attr] = base[attr] if base[attr] is not None else (PERF_RATING_FLOOR + PERF_RATING_SPAN / 2)

        overall = sum(weights[attr] * true_attrs[attr] for attr in ATTRIBUTES)
        total_evidence = sum(n_eff.values()) / len(ATTRIBUTES)
        confidence = total_evidence / (total_evidence + PRIOR_STRENGTH)

        history.append(
            {
                "match_id": match_id,
                "true_overall": round(overall, 2),
                **{f"true_{attr}": round(true_attrs[attr], 2) for attr in ATTRIBUTES},
                "confidence_score": round(confidence, 4),
            }
        )
    return history


def recompute_all(db_path: str, include_unreviewed: bool = False) -> int:
    """Recomputes true_overall_history for every player with usable
    captures. Returns the number of (player, match) rows written."""
    conn = connect(db_path)
    try:
        per_player, skipped = _load_player_match_stats(conn, include_unreviewed)
        if skipped:
            print(
                f"Note: {skipped} unreviewed player_summary capture(s) were EXCLUDED — "
                f"review them in validate_app.py first, or pass include_unreviewed=True to preview."
            )

        written = 0
        for player_id, match_stats in per_player.items():
            player_row = conn.execute(
                "SELECT * FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if player_row is None:
                continue
            for entry in compute_player_history(player_row, match_stats):
                conn.execute(
                    """INSERT OR REPLACE INTO true_overall_history
                       (player_id, match_id, true_overall, true_pace, true_shooting,
                        true_passing, true_dribbling, true_defending, true_physical,
                        confidence_score)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        player_id,
                        entry["match_id"],
                        entry["true_overall"],
                        entry["true_pace"],
                        entry["true_shooting"],
                        entry["true_passing"],
                        entry["true_dribbling"],
                        entry["true_defending"],
                        entry["true_physical"],
                        entry["confidence_score"],
                    ),
                )
                written += 1
        conn.commit()
        return written
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m fifa_analytics.model.true_overall <db_path> [--include-unreviewed]")
        sys.exit(1)
    count = recompute_all(sys.argv[1], include_unreviewed="--include-unreviewed" in sys.argv)
    print(f"Wrote {count} true-overall history row(s).")
