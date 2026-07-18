"""Best starting XI: optimal assignment of squad players to formation
slots, maximizing total effective rating (player rating x positional fit).

Solved exactly with the Hungarian algorithm
(scipy.optimize.linear_sum_assignment) — with a ~26-player squad and 11
slots this is instant, and beats greedy assignment whenever two good
players compete for the same natural slot.

Player ratings prefer the model's latest true overall
(true_overall_history, which only ever contains reviewed-data results
unless you previewed with --include-unreviewed) and fall back to the card's
base_overall for players without match history yet. Regens with neither
are skipped with a note.

Usage:
    python -m fifa_analytics.analysis.best_xi data/fifa.db "Manchester United"
    python -m fifa_analytics.analysis.best_xi data/fifa.db "Manchester United" 4-2-3-1
"""

import sys
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from fifa_analytics.analysis.formations import FORMATIONS, familiarity
from fifa_analytics.db.models import connect, get_team_id_by_name
from fifa_analytics.model.features import position_group


@dataclass
class SquadPlayer:
    player_id: int
    name: str
    group: str
    rating: float
    rating_source: str  # "true" | "card"


@dataclass
class Assignment:
    slot_group: str
    player: SquadPlayer
    effective_rating: float


def load_squad(conn, team_id: int) -> list[SquadPlayer]:
    """Latest true overall per player where available, else card overall."""
    rows = conn.execute(
        """SELECT p.player_id, p.name, p.position, p.base_overall,
                  (SELECT toh.true_overall FROM true_overall_history toh
                   WHERE toh.player_id = p.player_id
                   ORDER BY toh.match_id DESC LIMIT 1) AS latest_true
           FROM players p WHERE p.team_id = ?""",
        (team_id,),
    ).fetchall()

    squad = []
    for row in rows:
        if row["latest_true"] is not None:
            rating, source = float(row["latest_true"]), "true"
        elif row["base_overall"] is not None:
            rating, source = float(row["base_overall"]), "card"
        else:
            print(f"Skipping {row['name']}: no true-overall history and no card rating yet.")
            continue
        squad.append(SquadPlayer(row["player_id"], row["name"], position_group(row["position"]), rating, source))
    return squad


def pick_best_xi(squad: list[SquadPlayer], formation: str) -> tuple[list[Assignment], float]:
    slots = FORMATIONS[formation]
    if len(squad) < len(slots):
        raise ValueError(f"Need at least {len(slots)} rated players, have {len(squad)}.")

    effective = np.array(
        [[player.rating * familiarity(slot, player.group) for player in squad] for slot in slots]
    )
    slot_idx, player_idx = linear_sum_assignment(-effective)

    assignments = [
        Assignment(slots[s], squad[p], round(float(effective[s, p]), 2))
        for s, p in zip(slot_idx, player_idx)
    ]
    total = round(float(effective[slot_idx, player_idx].sum()), 2)
    return assignments, total


def best_formation(squad: list[SquadPlayer]) -> tuple[str, list[Assignment], float]:
    best = None
    for formation in FORMATIONS:
        assignments, total = pick_best_xi(squad, formation)
        if best is None or total > best[2]:
            best = (formation, assignments, total)
    return best


def print_xi(formation: str, assignments: list[Assignment], total: float) -> None:
    print(f"\n{formation}  (total effective rating: {total}, avg {total / len(assignments):.1f})")
    for a in assignments:
        out_of_position = "" if a.slot_group == a.player.group else f"  [natural: {a.player.group}]"
        source_note = "" if a.player.rating_source == "true" else " (card)"
        print(f"  {a.slot_group:>3}  {a.player.name:<28} {a.player.rating:>5.1f}{source_note} -> {a.effective_rating}{out_of_position}")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print('Usage: python -m fifa_analytics.analysis.best_xi <db_path> "<team name>" [formation]')
        print(f"Formations: {', '.join(FORMATIONS)}")
        sys.exit(1)

    db_path, team_name = sys.argv[1], sys.argv[2]
    conn = connect(db_path)
    try:
        team_id = get_team_id_by_name(conn, team_name)
        if team_id is None:
            print(f"Team {team_name!r} not found — import its card data first.")
            sys.exit(1)
        squad = load_squad(conn, team_id)
    finally:
        conn.close()

    if len(sys.argv) == 4:
        formation = sys.argv[3]
        if formation not in FORMATIONS:
            print(f"Unknown formation {formation!r}. Options: {', '.join(FORMATIONS)}")
            sys.exit(1)
        print_xi(formation, *pick_best_xi(squad, formation))
    else:
        name, assignments, total = best_formation(squad)
        print(f"Best formation across {len(FORMATIONS)} options:")
        print_xi(name, assignments, total)
