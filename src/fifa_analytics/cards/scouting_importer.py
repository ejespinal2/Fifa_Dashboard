"""Populates scouting_candidates from EAFC26-DataHub — the same dataset the
card importer uses for your own squads, just NOT filtered down to a
specific club. Per the spec (§6): filtered by position, age, and potential,
excluding anyone already on one of your own imported squads (no point
scouting your own players).

Refreshable snapshot, not user-owned data: each import wipes the previous
snapshot for that source label first (clear_scouting_candidates), so
re-running with different filters doesn't accumulate stale rows.

Usage:
    python -m fifa_analytics.cards.scouting_importer data/fifa.db "eafc26-datahub:scouting" \
        --max-age 23 --min-potential 80 --exclude-team "Manchester United"
"""

import argparse

from fifa_analytics.cards.eafc26_datahub_importer import (
    RAW_CSV_URL,
    _real_position,
    _to_int,
    load_rows,
)
from fifa_analytics.db.models import clear_scouting_candidates, connect, upsert_scouting_candidate


def import_scouting_candidates(
    db_path: str,
    source_label: str,
    exclude_club_names: list[str] | None = None,
    csv_source: str = RAW_CSV_URL,
    max_age: int | None = None,
    min_potential: int | None = None,
    position_filter: str | None = None,
) -> int:
    rows = load_rows(csv_source)
    exclude = {c.strip().lower() for c in (exclude_club_names or [])}

    conn = connect(db_path)
    try:
        clear_scouting_candidates(conn, source_label)
        count = 0
        for row in rows:
            club_name = (row.get("club_name") or "").strip()
            if club_name.lower() in exclude:
                continue

            age = _to_int(row.get("age"))
            potential = _to_int(row.get("potential"))
            if max_age is not None and (age is None or age > max_age):
                continue
            if min_potential is not None and (potential is None or potential < min_potential):
                continue

            position = _real_position(row)
            if position_filter is not None and position != position_filter.upper():
                continue

            upsert_scouting_candidate(
                conn,
                name=row["short_name"],
                club_name=club_name or None,
                source=source_label,
                position=position,
                age=age,
                current_overall=_to_int(row.get("overall")),
                potential=potential,
                base_pace=_to_int(row.get("pace")),
                base_shooting=_to_int(row.get("shooting")),
                base_passing=_to_int(row.get("passing")),
                base_dribbling=_to_int(row.get("dribbling")),
                base_defending=_to_int(row.get("defending")),
                base_physical=_to_int(row.get("physic")),
                estimated_wage=float(row["wage_eur"]) if row.get("wage_eur") else None,
            )
            count += 1
        return count
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    parser.add_argument("source_label")
    parser.add_argument("--exclude-team", action="append", default=[], dest="exclude_teams")
    parser.add_argument("--max-age", type=int, default=None)
    parser.add_argument("--min-potential", type=int, default=None)
    parser.add_argument("--position", default=None)
    parser.add_argument("--csv-source", default=RAW_CSV_URL)
    args = parser.parse_args()

    count = import_scouting_candidates(
        args.db_path,
        args.source_label,
        exclude_club_names=args.exclude_teams,
        csv_source=args.csv_source,
        max_age=args.max_age,
        min_potential=args.min_potential,
        position_filter=args.position,
    )
    print(f"Stored {count} scouting candidate(s).")
