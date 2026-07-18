"""Phase 4: transfer/loan/academy recommendation engine (spec §6).

Two deliberately different bars, matching the spec's distinction:

- **Transfer targets**: scored against your current best-XI's weakest slot
  in each position group, using tactical fit (analysis/tactics.py). Only
  candidates who'd actually be an upgrade over the current incumbent are
  surfaced — the bar is "better than what you have now."
- **Academy/loan prospects**: no requirement to beat anyone. The bar is
  growth room (potential well above current overall, at a young age) plus a
  floor check that they're not so far below replacement level that they'd
  actively hurt squad depth if thrown in — "won't hurt the team," not
  "ready to start."

Both run against scouting_candidates (cards/scouting_importer.py) — the
full external dataset minus whoever's already on your imported squads.
"""

from dataclasses import dataclass

from fifa_analytics.analysis.best_xi import load_squad, pick_best_xi
from fifa_analytics.analysis.formations import familiarity
from fifa_analytics.analysis.tactics import attribute_composite
from fifa_analytics.db.models import all_scouting_candidates
from fifa_analytics.model.features import position_group

# A candidate below this current_overall isn't offered as an academy/loan
# prospect even with great potential — signing a 45-rated 17-year-old adds
# a body to the depth chart that can't actually help if called upon, which
# fails the "won't hurt the team" bar regardless of trajectory.
ACADEMY_FLOOR_OVERALL = 60

# A slot/group counts as "plausible" for a transfer target only above this
# familiarity score (see formations.familiarity) -- e.g. a CB is never
# suggested to fill a striker gap, no matter how good their stats.
MIN_PLAUSIBLE_FAMILIARITY = 0.7


@dataclass
class WeakSlot:
    slot_group: str
    current_player: str
    current_rating: float


def identify_weak_slots(conn, team_id: int, formation: str) -> list[WeakSlot]:
    """Your current best-XI's slot assignments, weakest first."""
    squad = load_squad(conn, team_id)
    assignments, _ = pick_best_xi(squad, formation)
    slots = [WeakSlot(a.slot_group, a.player.name, a.effective_rating) for a in assignments]
    return sorted(slots, key=lambda s: s.current_rating)


def _candidate_attrs(candidate) -> dict:
    return {
        "pace": candidate["base_pace"],
        "shooting": candidate["base_shooting"],
        "passing": candidate["base_passing"],
        "dribbling": candidate["base_dribbling"],
        "defending": candidate["base_defending"],
        "physical": candidate["base_physical"],
    }


def transfer_targets(conn, team_id: int, formation: str, tactic: str = "balanced", top_n: int = 5) -> dict:
    """{slot_group: [candidate dicts, sorted by fit, upgrades only]} — one
    entry per DISTINCT weak position group (not per individual slot, so a
    formation with two CB slots doesn't repeat the same search).
    """
    weak_slots = identify_weak_slots(conn, team_id, formation)
    candidates = all_scouting_candidates(conn)

    results: dict[str, list[dict]] = {}
    seen_groups = set()
    for slot in weak_slots:
        if slot.slot_group in seen_groups:
            continue
        seen_groups.add(slot.slot_group)

        scored = []
        for c in candidates:
            fit = familiarity(slot.slot_group, position_group(c["position"]))
            if fit < MIN_PLAUSIBLE_FAMILIARITY:
                continue
            composite = attribute_composite(_candidate_attrs(c), slot.slot_group, tactic)
            if composite is None:
                continue
            effective = composite * fit
            if effective <= slot.current_rating:
                continue
            scored.append((c, effective))

        scored.sort(key=lambda pair: -pair[1])
        results[slot.slot_group] = [
            {
                "name": c["name"],
                "club": c["club_name"],
                "age": c["age"],
                "current_overall": c["current_overall"],
                "effective_rating": round(effective, 2),
                "upgrade_over": slot.current_player,
                "upgrade_over_rating": slot.current_rating,
            }
            for c, effective in scored[:top_n]
        ]
    return results


def academy_prospects(conn, min_potential_gap: int = 8, max_age: int = 21, top_n: int = 10) -> list[dict]:
    candidates = all_scouting_candidates(conn)
    scored = []
    for c in candidates:
        if c["age"] is None or c["age"] > max_age:
            continue
        if c["potential"] is None or c["current_overall"] is None:
            continue
        if c["current_overall"] < ACADEMY_FLOOR_OVERALL:
            continue
        gap = c["potential"] - c["current_overall"]
        if gap < min_potential_gap:
            continue
        scored.append((c, gap))

    scored.sort(key=lambda pair: (-pair[1], -pair[0]["potential"]))
    return [
        {
            "name": c["name"],
            "club": c["club_name"],
            "age": c["age"],
            "position": c["position"],
            "current_overall": c["current_overall"],
            "potential": c["potential"],
            "growth_room": gap,
        }
        for c, gap in scored[:top_n]
    ]
