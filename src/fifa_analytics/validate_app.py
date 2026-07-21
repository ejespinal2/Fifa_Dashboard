"""Streamlit validation UI — the point of Phase 1 that keeps OCR mistakes
from silently poisoning the true-overall model. Shows each unreviewed
capture's screenshot next to its parsed values, editable, before confirming.

Run with: streamlit run src/fifa_analytics/validate_app.py -- --db data/fifa.db
"""

import argparse
import sqlite3
from datetime import datetime, timezone

import streamlit as st

from fifa_analytics.db.models import (
    connect,
    load_match_events,
    mark_reviewed,
    players_for_teams,
    replace_match_events,
)

# Every event_type classify_event_icon or the structural sub-detector can
# produce, plus "unknown" (icon classification gave up) -- the review UI
# lets a reviewer retype any of these, not just the ones OCR happened to
# get right.
EVENT_TYPES = [
    "goal", "penalty_goal", "missed_penalty", "yellow_card", "red_card",
    "sub_on", "sub_off", "unknown",
]


def get_db_path() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/fifa.db")
    args, _ = parser.parse_known_args()
    return args.db


def get_conn(db_path: str) -> sqlite3.Connection:
    # Not st.cache_resource: sqlite connections are bound to their creating
    # thread and Streamlit reruns land on arbitrary threads — a cached
    # connection eventually raises "SQLite objects created in a thread can
    # only be used in that same thread". Per-rerun connections are cheap.
    return connect(db_path)


def load_captures(
    conn: sqlite3.Connection,
    match_id: int | None = None,
    capture_type: str | None = None,
    include_reviewed: bool = False,
):
    """The review queue, filterable by match/capture type. Confirmed
    captures are excluded by default (as before) -- include_reviewed=True
    is how a mistake noticed AFTER confirming (the real case that came up:
    a team_events row read wrong, discovered only after Recompute had
    already run) gets fixed, since confirming used to hide a capture from
    this page forever with no way back."""
    conditions = []
    params: list = []
    if not include_reviewed:
        conditions.append("reviewed = 0")
    if match_id is not None:
        conditions.append("match_id = ?")
        params.append(match_id)
    if capture_type is not None:
        conditions.append("capture_type = ?")
        params.append(capture_type)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return conn.execute(
        f"""SELECT capture_id, match_id, capture_type, player_id, team_id,
                   screenshot_path, ocr_confidence_avg, raw_text, match_confidence, reviewed
            FROM ocr_captures
            {where}
            ORDER BY reviewed ASC, ocr_confidence_avg ASC NULLS FIRST""",
        params,
    ).fetchall()


def load_matches(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT m.match_id, ht.name AS home_name, at.name AS away_name
           FROM matches m
           JOIN teams ht ON ht.team_id = m.home_team_id
           JOIN teams at ON at.team_id = m.away_team_id
           ORDER BY m.match_id DESC"""
    ).fetchall()


def load_stat_values(conn: sqlite3.Connection, capture_id: int):
    return conn.execute(
        """SELECT stat_name, stat_value, ocr_confidence
           FROM match_stat_values WHERE capture_id = ?""",
        (capture_id,),
    ).fetchall()


def load_team_name(conn: sqlite3.Connection, team_id: int) -> str | None:
    row = conn.execute("SELECT name FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return row["name"] if row else None


def load_match_team_ids(conn: sqlite3.Connection, match_id: int) -> tuple[int, int]:
    row = conn.execute(
        "SELECT home_team_id, away_team_id FROM matches WHERE match_id = ?", (match_id,)
    ).fetchone()
    return row["home_team_id"], row["away_team_id"]


def assign_player(conn: sqlite3.Connection, capture_id: int, player_id: int, team_id: int) -> None:
    conn.execute(
        "UPDATE ocr_captures SET player_id = ?, team_id = ? WHERE capture_id = ?",
        (player_id, team_id, capture_id),
    )
    conn.commit()


def main():
    st.set_page_config(page_title="FIFA Analytics — OCR Validation", layout="wide")
    st.title("OCR Validation Queue")

    conn = get_conn(get_db_path())

    matches = load_matches(conn)
    match_options = {"All matches": None}
    for m in matches:
        match_options[f"#{m['match_id']}: {m['home_name']} vs {m['away_name']}"] = m["match_id"]

    with st.sidebar:
        st.header("Filter")
        match_choice = st.selectbox("Match", list(match_options.keys()))
        type_choice = st.selectbox(
            "Capture type", ["All", "team_summary", "team_events", "player_summary", "player_gk"]
        )
        include_reviewed = st.checkbox(
            "Include already-reviewed captures",
            value=False,
            help="Confirming hides a capture from this queue by default. Check this to go back "
                 "and fix something you already confirmed but later noticed was wrong.",
        )

    captures = load_captures(
        conn,
        match_id=match_options[match_choice],
        capture_type=None if type_choice == "All" else type_choice,
        include_reviewed=include_reviewed,
    )

    if not captures:
        st.success("Nothing matches this filter.")
        return

    st.caption(f"{len(captures)} capture(s) match this filter, lowest confidence first.")

    capture = captures[0]
    # team_summary in particular produces two near-identical-looking
    # captures per screenshot (one per team's column) -- without the team
    # name here there's no way to tell which one you're looking at.
    team_name = load_team_name(conn, capture["team_id"]) if capture["team_id"] is not None else None
    title = f"{capture['capture_type']}" + (f" — {team_name}" if team_name else "") + f" — match {capture['match_id']}"
    st.subheader(
        f"{title} (confidence: {capture['ocr_confidence_avg']:.2f})"
        if capture["ocr_confidence_avg"] is not None
        else title
    )
    if capture["reviewed"]:
        st.info("Already confirmed once — re-confirming will overwrite it with any edits below.")

    col_img, col_fields = st.columns([1, 1])

    with col_img:
        st.image(capture["screenshot_path"], use_container_width=True)

    with col_fields:
        selected_player_id = None

        if capture["capture_type"] in ("player_summary", "player_gk") and capture["player_id"] is None:
            st.warning(
                f"Couldn't auto-match a player for this screenshot. OCR read the name as: "
                f"**{capture['raw_text'] or '(nothing read)'}**"
            )
            home_id, away_id = load_match_team_ids(conn, capture["match_id"])
            candidates = players_for_teams(conn, [home_id, away_id])
            options = {f"{c['name']} (player_id={c['player_id']})": c for c in candidates}
            choice = st.selectbox("Assign the correct player", ["-- select --"] + list(options.keys()))
            if choice != "-- select --":
                selected_player_id = options[choice]["player_id"]
                st.caption(f"Will assign to {choice} on confirm.")
        elif capture["capture_type"] in ("player_summary", "player_gk"):
            note = {
                "reassigned": "⚠️ Auto-reassigned from another club (a transferred player found elsewhere in the card dataset) — worth a double-check.",
                "new_player": "⚠️ No card data found anywhere — created as a brand-new player (likely a Career Mode academy graduate). Verify the name spelling.",
                "fuzzy": "⚠️ Matched by approximate name similarity, not an exact one — worth a double-check.",
            }.get(capture["match_confidence"])
            st.caption(f"OCR read name as: {capture['raw_text']}")
            if note:
                st.info(note)

        if capture["capture_type"] == "team_events":
            st.text_area("Raw OCR text", capture["raw_text"] or "", height=100)

            home_id, away_id = load_match_team_ids(conn, capture["match_id"])
            roster = players_for_teams(conn, [home_id, away_id])
            name_by_player_id = {c["player_id"]: c["name"] for c in roster}
            roster_lookup = {c["name"]: (c["player_id"], c["team_id"]) for c in roster}
            roster_names = sorted(roster_lookup)
            delete_marker = "-- delete this row --"
            skip_marker = "-- select --"

            st.caption(
                "Fix whatever OCR got wrong before confirming: retype an 'unknown' "
                "icon, correct a misread minute, pick delete to drop a row, or add "
                "one below for an event OCR missed entirely."
            )

            events = load_match_events(conn, capture["capture_id"])
            row_inputs = []
            for row in events:
                event_key = f"{capture['capture_id']}_event_{row['event_id']}"
                default_name = name_by_player_id.get(row["player_id"], delete_marker)
                options = [delete_marker] + roster_names
                p_col, m_col, t_col = st.columns([2, 1, 2])
                player_choice = p_col.selectbox(
                    "player", options,
                    index=options.index(default_name) if default_name in options else 0,
                    key=f"{event_key}_player",
                )
                minute = m_col.number_input(
                    "minute", min_value=0, max_value=120,
                    value=row["minute"] if row["minute"] is not None else 0,
                    key=f"{event_key}_minute",
                )
                default_type = row["event_type"] if row["event_type"] in EVENT_TYPES else "unknown"
                event_type = t_col.selectbox(
                    "event_type", EVENT_TYPES, index=EVENT_TYPES.index(default_type), key=f"{event_key}_type",
                )
                row_inputs.append((player_choice, minute, event_type, delete_marker))

            extra_key = f"{capture['capture_id']}_extra_event_rows"
            extra_rows = st.session_state.get(extra_key, 0)
            for i in range(extra_rows):
                new_key = f"{capture['capture_id']}_new_event_{i}"
                p_col, m_col, t_col = st.columns([2, 1, 2])
                player_choice = p_col.selectbox("player", [skip_marker] + roster_names, key=f"{new_key}_player")
                minute = m_col.number_input("minute", min_value=0, max_value=120, value=0, key=f"{new_key}_minute")
                event_type = t_col.selectbox("event_type", EVENT_TYPES, key=f"{new_key}_type")
                row_inputs.append((player_choice, minute, event_type, skip_marker))

            if st.button("+ Add another event", key=f"{capture['capture_id']}_add_event"):
                st.session_state[extra_key] = extra_rows + 1
                st.rerun()
        else:
            rows = load_stat_values(conn, capture["capture_id"])
            edited = {}
            for row in rows:
                low_conf = row["ocr_confidence"] is not None and row["ocr_confidence"] < 0.7
                label = f"{row['stat_name']}" + (" ⚠️ low confidence" if low_conf else "")
                edited[row["stat_name"]] = st.number_input(
                    label, value=row["stat_value"] if row["stat_value"] is not None else 0.0, key=f"{capture['capture_id']}_{row['stat_name']}"
                )

        confirm_blocked = (
            capture["capture_type"] in ("player_summary", "player_gk")
            and capture["player_id"] is None
            and selected_player_id is None
        )
        if st.button("Confirm and mark reviewed", type="primary", disabled=confirm_blocked):
            if selected_player_id is not None:
                home_id, away_id = load_match_team_ids(conn, capture["match_id"])
                candidates = players_for_teams(conn, [home_id, away_id])
                team_id = next(c["team_id"] for c in candidates if c["player_id"] == selected_player_id)
                assign_player(conn, capture["capture_id"], selected_player_id, team_id)
            if capture["capture_type"] == "team_events":
                new_rows = []
                for player_choice, minute, event_type, skip_value in row_inputs:
                    if player_choice == skip_value:  # deleted, or an unfilled add-row -- drop it
                        continue
                    player_id, team_id = roster_lookup[player_choice]
                    new_rows.append(
                        {"player_id": player_id, "team_id": team_id, "minute": int(minute), "event_type": event_type}
                    )
                replace_match_events(conn, capture["capture_id"], capture["match_id"], new_rows)
            else:
                for stat_name, value in edited.items():
                    conn.execute(
                        "UPDATE match_stat_values SET stat_value = ? WHERE capture_id = ? AND stat_name = ?",
                        (value, capture["capture_id"], stat_name),
                    )
                conn.commit()
            mark_reviewed(conn, capture["capture_id"], datetime.now(timezone.utc).isoformat())
            st.rerun()
        if confirm_blocked:
            st.caption("Assign a player above before this can be confirmed.")


if __name__ == "__main__":
    main()
