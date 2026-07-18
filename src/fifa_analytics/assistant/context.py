"""Grounding layer for the assistant: every answer is built from a context
pack computed by the same deterministic engines the dashboard tabs use —
the LLM reasons over real numbers, it never generates SQL and never
invents data. Pure functions, testable without streamlit or Ollama.

Routing is keyword-based on purpose: it works identically with any local
model (no reliance on flaky tool-calling in small models) and is fully
inspectable — the dashboard shows the exact pack sent with each answer."""

import json

from fifa_analytics.analysis.best_xi import best_formation, load_squad
from fifa_analytics.analysis.matchup import compare_teams
from fifa_analytics.analysis.scouting import academy_prospects, surplus_players, transfer_targets
from fifa_analytics.assistant.knowledge import relevant_explainers
from fifa_analytics.dashboard.queries import (
    all_teams,
    schedule,
    season_xpts_table,
    squad_overview,
    team_record,
)

SQUAD_TOPICS = ("squad", "xi", "lineup", "line-up", "formation", "pick", "select", "start", "bench", "team sheet")
TRANSFER_TOPICS = ("transfer", "sign", "buy", "sell", "scout", "target", "academy", "prospect", "loan", "surplus", "offload")
SEASON_TOPICS = ("xpts", "season", "points", "record", "table", "results", "form", "xg")


GENERIC_NAME_WORDS = {"club", "united", "city", "real", "athletic", "sporting", "borussia", "inter"}


def find_opponent(question: str, conn, my_team_id: int) -> int | None:
    """A team name mentioned in the question (other than mine): full-name
    match wins outright, else the team whose distinctive name words (e.g.
    'leverkusen', not 'united') appear most. None when nothing matches."""
    lowered = question.lower()
    best_id, best_score = None, 0
    for team in all_teams(conn):
        if team["team_id"] == my_team_id:
            continue
        name = team["name"].lower()
        if name in lowered:
            return team["team_id"]
        distinctive = [
            w for w in name.replace("-", " ").split()
            if len(w) >= 4 and w not in GENERIC_NAME_WORDS
        ]
        score = sum(1 for w in distinctive if w in lowered)
        if score > best_score:
            best_id, best_score = team["team_id"], score
    return best_id


def _squad_section(conn, team_id: int) -> dict:
    players = squad_overview(conn, team_id)
    formation, assignments, total = best_formation(load_squad(conn, team_id))
    return {
        "squad": [
            {
                "player": p["name"],
                "position": p["position"],
                "card_overall": p["base_overall"],
                "true_overall": p["true_overall"],
                "true_minus_card": p["delta"],
                "model_confidence": p["confidence_score"],
                "matches_modeled": p["matches_modeled"],
            }
            for p in players
        ],
        "best_xi": {
            "formation": formation,
            "total_effective": total,
            "xi": [
                {
                    "slot": a.slot_group,
                    "player": a.player.name,
                    "rating": a.player.rating,
                    "rating_source": "true_overall" if a.player.rating_source == "true" else "card_only",
                    "effective": a.effective_rating,
                }
                for a in assignments
            ],
        },
    }


def _transfer_section(conn, team_id: int) -> dict:
    formation, _, _ = best_formation(load_squad(conn, team_id))
    return {
        "transfer_targets_in": transfer_targets(conn, team_id, formation, top_n=3),
        "academy_prospects": academy_prospects(conn, top_n=5),
        "surplus_transfers_out": surplus_players(conn, team_id, formation),
    }


def _season_section(conn, team_id: int) -> dict:
    return {
        "record_by_competition": team_record(conn, team_id),
        "xpts_table": season_xpts_table(conn),
        "recent_fixtures": schedule(conn)[:5],
    }


def build_context(question: str, conn, my_team_id: int) -> dict:
    """The full grounding pack for one question: routed data sections plus
    the matching model explainers. Small on purpose — local models answer
    best over short, relevant context."""
    lowered = question.lower()
    my_team = conn.execute("SELECT name FROM teams WHERE team_id = ?", (my_team_id,)).fetchone()["name"]
    pack: dict = {"my_team": my_team, "sections": {}, "notes": []}

    opponent_id = find_opponent(question, conn, my_team_id)
    wants_squad = any(t in lowered for t in SQUAD_TOPICS)
    wants_transfers = any(t in lowered for t in TRANSFER_TOPICS)
    wants_season = any(t in lowered for t in SEASON_TOPICS)

    if opponent_id is not None:
        try:
            pack["sections"]["matchup"] = compare_teams(conn, my_team_id, opponent_id)
        except ValueError as too_few:
            pack["notes"].append(str(too_few))
    if wants_squad or not pack["sections"]:
        try:
            pack["sections"]["my_squad"] = _squad_section(conn, my_team_id)
        except ValueError as too_few:
            pack["notes"].append(str(too_few))
    if wants_transfers:
        try:
            pack["sections"]["transfers"] = _transfer_section(conn, my_team_id)
        except ValueError as too_few:
            pack["notes"].append(str(too_few))
    if wants_season:
        pack["sections"]["season"] = _season_section(conn, my_team_id)

    pack["explainers"] = relevant_explainers(question)
    return pack


SYSTEM_PROMPT = """\
You are the analytics assistant inside a FIFA Career Mode analytics system.
You help the manager pick squads, plan transfers, and understand the models.

Hard rules:
- Ground every claim in the CONTEXT DATA below. Never invent players,
  ratings, or stats. If the data you'd need isn't in the context, say so.
- Prefer true overalls over card overalls and say which you're using;
  flag rating_source card_only and low model_confidence as caveats.
- Be concrete: name players, slots, and numbers. Rank options.
- Keep answers short and structured. You advise; the manager decides.
"""


def build_messages(question: str, conn, my_team_id: int, history: list[dict] | None = None) -> tuple[list[dict], dict]:
    """(messages for the LLM, the context pack used) — the pack is returned
    so the UI can show exactly what the model was given."""
    pack = build_context(question, conn, my_team_id)
    explainers = "\n\n".join(pack["explainers"].values())
    data = json.dumps({k: v for k, v in pack.items() if k != "explainers"}, indent=1, default=str)
    system = f"{SYSTEM_PROMPT}\nMODEL DOCUMENTATION:\n{explainers}\n\nCONTEXT DATA:\n{data}"

    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-6:]:  # short memory: last 3 exchanges
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})
    return messages, pack
