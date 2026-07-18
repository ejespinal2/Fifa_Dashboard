"""Phase 5: the Streamlit dashboard (spec §8) — read-only views over
everything Phases 1-4 compute: squad true overalls, per-player progression,
xPTS vs actual points, the best-XI generator, and scouting/transfer
recommendations.

Run with:
    streamlit run src/fifa_analytics/dashboard/app.py -- --db data/fifa.db

Two spec §8 items are deliberately absent: the heatmap viewer (Phase 1's
capture scope doesn't include heatmap screenshots, so there's no data for
it) and the chat box (that's Phase 6's local-LLM assistant).

This app never writes to the database — OCR corrections happen in
validate_app.py, model recomputes via the model/analysis CLIs. Data logic
lives in queries.py so it stays testable without the UI.
"""

import argparse
import os

import altair as alt
import pandas as pd
import streamlit as st

from fifa_analytics.analysis.best_xi import best_formation, load_squad, pick_best_xi
from fifa_analytics.analysis.formations import FORMATIONS
from fifa_analytics.analysis.scouting import academy_prospects, identify_weak_slots, transfer_targets
from fifa_analytics.analysis.tactics import TACTIC_ADJUSTMENTS
from fifa_analytics.dashboard import queries
from fifa_analytics.db.models import connect
from fifa_analytics.model.features import ATTRIBUTES

# Categorical palette (6 slots, fixed order, CVD-validated). The six
# attributes take the six slots in ATTRIBUTES order so pace is always blue,
# physical always orange, across every chart and session.
PALETTE = ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a", "#eb6834"]
ATTRIBUTE_COLORS = dict(zip(ATTRIBUTES, PALETTE))
OVER_COLOR, UNDER_COLOR = "#2a78d6", "#eb6834"  # xPTS over/underperformance poles


def get_db_path() -> str:
    if os.environ.get("FIFA_DASH_DB"):  # test hook: AppTest can't pass CLI args
        return os.environ["FIFA_DASH_DB"]
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/fifa.db")
    args, _ = parser.parse_known_args()
    return args.db


def get_conn(db_path: str):
    # Deliberately NOT st.cache_resource: Streamlit runs each script rerun
    # on an arbitrary thread, and sqlite connections are bound to the thread
    # that created them — a cached connection blows up with
    # "SQLite objects created in a thread can only be used in that same
    # thread" as soon as a rerun lands elsewhere. Opening per run is cheap.
    return connect(db_path)


def squad_tab(conn, team_id: int) -> None:
    squad = queries.squad_overview(conn, team_id)
    if not squad:
        st.info("No players on this team yet.")
        return
    modeled = sum(1 for p in squad if p["true_overall"] is not None)
    st.caption(
        f"{len(squad)} players; {modeled} with modeled true overalls "
        "(the rest show card ratings only until they have reviewed match history)."
    )
    df = pd.DataFrame(squad)[
        ["name", "position", "base_overall", "true_overall", "delta", "confidence_score", "matches_modeled"]
    ].rename(columns={"base_overall": "card", "true_overall": "true", "confidence_score": "confidence"})
    st.dataframe(df, use_container_width=True, hide_index=True)


def progression_tab(conn, team_id: int) -> None:
    rows = queries.player_progression(conn, team_id)
    if not rows:
        st.info(
            "No modeled match history yet — run the OCR pipeline on a match, "
            "review it in the validation UI, then "
            "`python -m fifa_analytics.model.true_overall data/fifa.db`."
        )
        return

    df = pd.DataFrame(rows)
    players = sorted(df["player"].unique())
    default = players[: min(3, len(players))]
    selected = st.multiselect(
        "Players (up to 6 — one palette slot each, never recycled)",
        players, default=default, max_selections=len(PALETTE),
    )
    if selected:
        chosen = df[df["player"].isin(selected)]
        order = sorted(selected)  # stable alphabetical slot assignment
        chart = (
            alt.Chart(chosen)
            .mark_line(point=True, strokeWidth=2)
            .encode(
                x=alt.X("match_number:O", title="modeled match #"),
                # ratings live in a narrow 45-99 band; a zero baseline would
                # flatten every line into an unreadable strip at the top
                y=alt.Y("true_overall:Q", title="true overall", scale=alt.Scale(zero=False)),
                color=alt.Color(
                    "player:N",
                    scale=alt.Scale(domain=order, range=PALETTE[: len(order)]),
                    legend=alt.Legend(orient="bottom", title=None),
                ),
                tooltip=["player", "matchweek", alt.Tooltip("true_overall", format=".1f")],
            )
        )
        st.altair_chart(chart, use_container_width=True)
        with st.expander("Data table"):
            wide = chosen.pivot_table(index="match_number", columns="player", values="true_overall").sort_index()
            st.dataframe(wide[order], use_container_width=True)

    st.divider()
    st.subheader("Attribute detail")
    player_name = st.selectbox("Player", players)
    player_row = conn.execute(
        "SELECT player_id FROM players WHERE team_id = ? AND name = ?", (team_id, player_name)
    ).fetchone()
    attr_rows = queries.attribute_progression(conn, player_row["player_id"]) if player_row else []
    if not attr_rows:
        st.info("No attribute history for this player yet.")
        return
    attr_long = pd.DataFrame(attr_rows)
    present = [a for a in ATTRIBUTES if a in set(attr_long["attribute"])]  # fixed order, fixed colors
    attr_chart = (
        alt.Chart(attr_long)
        .mark_line(point=True, strokeWidth=2)
        .encode(
            x=alt.X("match_number:O", title="modeled match #"),
            y=alt.Y("value:Q", title="true attribute", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "attribute:N",
                scale=alt.Scale(domain=present, range=[ATTRIBUTE_COLORS[a] for a in present]),
                legend=alt.Legend(orient="bottom", title=None),
            ),
            tooltip=["attribute", "matchweek", alt.Tooltip("value", format=".1f")],
        )
    )
    st.altair_chart(attr_chart, use_container_width=True)
    st.caption(
        "Gaps are evidence gating, not missing data — an attribute the player "
        "produced no actions for that match isn't scored."
    )
    with st.expander("Data table"):
        attr_df = attr_long.pivot_table(index="match_number", columns="attribute", values="value")
        st.dataframe(attr_df[present], use_container_width=True)


def season_tab(conn, team_id: int) -> None:
    table = queries.season_xpts_table(conn)
    if not table:
        st.info(
            "No xPTS rows yet — capture a match's team summary (it carries both "
            "teams' xG), review it, then `python -m fifa_analytics.analysis.xpts data/fifa.db`."
        )
        return
    df = pd.DataFrame(table)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Points vs expected (over/underperformance)")
    delta_chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4, size=18)
        .encode(
            x=alt.X("delta:Q", title="points − xPTS"),
            y=alt.Y("team:N", sort="-x", title=None),
            color=alt.condition(alt.datum.delta >= 0, alt.value(OVER_COLOR), alt.value(UNDER_COLOR)),
            tooltip=["team", "matches", "points", "xpts", "delta"],
        )
    )
    st.altair_chart(delta_chart, use_container_width=True)

    st.subheader("Match by match (selected team)")
    matches = queries.team_match_xpts(conn, team_id)
    if matches:
        st.dataframe(pd.DataFrame(matches), use_container_width=True, hide_index=True)
    else:
        st.caption("No xPTS rows for the selected team yet.")


def best_xi_tab(conn, team_id: int) -> None:
    squad = load_squad(conn, team_id)
    if len(squad) < 11:
        st.warning(f"Need at least 11 rated players to pick an XI — this team has {len(squad)}.")
        return
    choice = st.selectbox("Formation", ["best of all"] + list(FORMATIONS))
    if choice == "best of all":
        formation, assignments, total = best_formation(squad)
        st.caption(f"Best across {len(FORMATIONS)} formations: **{formation}**")
    else:
        formation = choice
        assignments, total = pick_best_xi(squad, formation)
    st.metric(f"{formation} total effective rating", total, delta=f"avg {total / len(assignments):.1f}")
    df = pd.DataFrame(
        {
            "slot": a.slot_group,
            "player": a.player.name,
            "natural group": a.player.group,
            "rating": a.player.rating,
            "source": "model" if a.player.rating_source == "true" else "card",
            "effective": a.effective_rating,
        }
        for a in assignments
    )
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("`source: card` = no reviewed match history yet, card overall used as-is.")


def scouting_tab(conn, team_id: int) -> None:
    pool = queries.scouting_pool_size(conn)
    if pool == 0:
        st.info(
            "No scouting pool imported yet. Run:\n\n"
            "```\npython -m fifa_analytics.cards.scouting_importer data/fifa.db "
            '"eafc26-datahub:main" --exclude-team "Your Club"\n```'
        )
        return
    st.caption(f"{pool:,} candidates in the scouting pool.")

    squad = load_squad(conn, team_id)
    if len(squad) < 11:
        st.warning("Transfer targets need a full XI to compare against — import this team's card data first.")
    else:
        col_formation, col_tactic = st.columns(2)
        formation = col_formation.selectbox("Formation", list(FORMATIONS))
        tactic = col_tactic.selectbox("Tactic", list(TACTIC_ADJUSTMENTS))

        st.subheader("Current XI, weakest slots first")
        weak = identify_weak_slots(conn, team_id, formation)
        st.dataframe(
            pd.DataFrame({"slot": w.slot_group, "player": w.current_player, "effective": w.current_rating} for w in weak),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Transfer targets (upgrades only)")
        st.caption(
            "Candidates must fit the position (familiarity-gated) and beat the "
            "current starter's effective rating under the chosen tactic. "
            "Goalkeepers never appear — the card dataset carries no GK sub-attributes (see README)."
        )
        targets = transfer_targets(conn, team_id, formation, tactic=tactic, top_n=5)
        any_shown = False
        for group, candidates in targets.items():
            if not candidates:
                continue
            any_shown = True
            st.markdown(f"**{group}**")
            st.dataframe(pd.DataFrame(candidates), use_container_width=True, hide_index=True)
        if not any_shown:
            st.caption("No candidate in the pool upgrades any weak slot — your XI already outrates the market here.")

    st.subheader("Academy / loan prospects")
    col_age, col_gap = st.columns(2)
    max_age = col_age.slider("Max age", 16, 24, 21)
    min_gap = col_gap.slider("Min growth room (potential − current)", 4, 20, 8)
    prospects = academy_prospects(conn, min_potential_gap=min_gap, max_age=max_age, top_n=15)
    if prospects:
        st.dataframe(pd.DataFrame(prospects), use_container_width=True, hide_index=True)
    else:
        st.caption("Nobody in the pool clears these filters.")


def main():
    st.set_page_config(page_title="FIFA Career Mode Analytics", layout="wide")
    st.title("FIFA Career Mode Analytics")

    conn = get_conn(get_db_path())
    teams = queries.teams_with_players(conn)
    if not teams:
        st.info(
            "No teams in the database yet. Import card data first:\n\n"
            "```\npython -m fifa_analytics.cards.eafc26_datahub_importer "
            '"Manchester United" data/fifa.db "eafc26-datahub:main"\n```'
        )
        return

    labels = {f"{t['name']} ({t['player_count']} players)": t["team_id"] for t in teams}
    team_id = labels[st.sidebar.selectbox("Team", list(labels))]
    st.sidebar.caption(
        "Views read reviewed data only (whatever the model/xPTS CLIs last "
        "computed). Unreviewed OCR never leaks in here."
    )

    tab_squad, tab_progress, tab_season, tab_xi, tab_scout = st.tabs(
        ["Squad", "Progression", "Season (xPTS)", "Best XI", "Scouting"]
    )
    with tab_squad:
        squad_tab(conn, team_id)
    with tab_progress:
        progression_tab(conn, team_id)
    with tab_season:
        season_tab(conn, team_id)
    with tab_xi:
        best_xi_tab(conn, team_id)
    with tab_scout:
        scouting_tab(conn, team_id)


if __name__ == "__main__":
    main()
