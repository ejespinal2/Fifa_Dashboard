"""Phase 5: the Streamlit dashboard (spec §8) — views over everything
Phases 1-4 compute (squad true overalls, progression, xPTS, best XI,
scouting), plus the management tabs: a match schedule/calendar with W/D/L
and per-competition records, manual player transfers, and a stats reset.

Run with:
    streamlit run src/fifa_analytics/dashboard/app.py -- --db data/fifa.db

Two spec §8 items are deliberately absent: the heatmap viewer (Phase 1's
capture scope doesn't include heatmap screenshots, so there's no data for
it) and the chat box (that's Phase 6's local-LLM assistant).

Write boundary: the analysis tabs (Squad/Progression/Season/Best XI/
Scouting) never write. The Schedule and Manage tabs write only on explicit
button clicks — creating/scoring/deleting fixtures, re-homing a player,
importing card data, resetting match stats. Per-stat OCR corrections still
live in validate_app.py, which stays the trust gate for model inputs. Data
logic lives in queries.py (reads) and db/models.py (writes) so it stays
testable without the UI.
"""

import argparse
import os
import re
from datetime import date as date_type
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from fifa_analytics.analysis.best_xi import best_formation, load_squad, pick_best_xi
from fifa_analytics.analysis.formations import FORMATIONS
from fifa_analytics.analysis.scouting import academy_prospects, identify_weak_slots, transfer_targets
from fifa_analytics.analysis.tactics import TACTIC_ADJUSTMENTS
from fifa_analytics.analysis.xpts import compute_all as compute_xpts
from fifa_analytics.dashboard import queries
from fifa_analytics.db.models import (
    connect,
    create_match,
    delete_match,
    get_or_create_season,
    get_or_create_team,
    get_setting,
    init_db,
    reset_match_data,
    set_player_team,
    set_setting,
    update_match_result,
)
from fifa_analytics.model.features import ATTRIBUTES
from fifa_analytics.model.true_overall import recompute_all

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


def profiles_dir() -> Path:
    return Path(os.environ.get("FIFA_PROFILES_DIR", "data/profiles"))


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "career"


def pick_career_db(default_db: str) -> str:
    """Sidebar career picker. Every career is its own database file under
    data/profiles/ (the --db file shows up as 'Default'), so several people
    — or several save files — share one install without sharing any data.
    A new career starts from a completely fresh file: every player at their
    real-world club, no matches, no history."""
    # A widget's session_state key can't be assigned after the widget is
    # instantiated in the same run — so career creation stashes the new
    # career under a pending key and applies it here, before the selectbox
    # exists on the next run.
    pending = st.session_state.pop("career_pending", None)
    if pending is not None:
        st.session_state["career_choice"] = pending

    profiles = sorted(profiles_dir().glob("*.db"))
    options: dict[str, str] = {}
    if os.path.exists(default_db) or not profiles:
        options[f"Default ({default_db})"] = default_db
    for p in profiles:
        options[p.stem.replace("_", " ").title()] = str(p)

    st.sidebar.subheader("Career")
    labels = list(options) + ["➕ New career…"]
    if st.session_state.get("career_choice") not in labels:
        st.session_state.pop("career_choice", None)  # e.g. profile file deleted
    choice = st.sidebar.selectbox("Playing as", labels, key="career_choice")

    if choice != "➕ New career…":
        return options[choice]

    name = st.sidebar.text_input("Career name (e.g. your gamertag or 'Man Utd 2nd save')", key="career_new_name")
    if st.sidebar.button("Create career", disabled=not name.strip()):
        profiles_dir().mkdir(parents=True, exist_ok=True)
        path = profiles_dir() / f"{_slugify(name)}.db"
        init_db(str(path))
        st.session_state["career_pending"] = path.stem.replace("_", " ").title()
        st.rerun()
    st.sidebar.caption("A new career is a fresh database — pick your club on the next screen.")
    st.stop()  # nothing to render until the career exists


@st.cache_data(ttl=3600, show_spinner=False)
def _club_list() -> list[str]:
    from fifa_analytics.cards import eafc26_datahub_importer as datahub

    return datahub.list_clubs()


def first_run_wizard(db_path: str) -> None:
    """Shown while the database has no teams: pick your club from the real
    dataset, import its card data, optionally pull the scouting pool."""
    st.header("Set up your career")
    st.caption("Pick the club you're managing — its full roster imports from EAFC26-DataHub.")

    try:
        with st.spinner("Loading the club list..."):
            clubs = _club_list()
    except Exception as fetch_error:  # offline etc. — let them type it instead
        st.warning(f"Couldn't fetch the club list ({fetch_error}) — type the club name exactly instead.")
        clubs = []

    club = (
        st.selectbox("Your club", clubs, index=None, placeholder="Start typing to search...", key="wiz_club")
        if clubs
        else st.text_input("Your club (exact dataset spelling)", key="wiz_club_text").strip() or None
    )
    with_scouting = st.checkbox(
        "Also import the scouting pool (every player at every other club — powers the Scouting tab)",
        value=True, key="wiz_scouting",
    )
    if st.button("Start career", type="primary", disabled=not club):
        from fifa_analytics.cards import eafc26_datahub_importer as datahub
        from fifa_analytics.cards.scouting_importer import import_scouting_candidates

        try:
            with st.spinner(f"Importing {club}..."):
                stored = datahub.scrape_and_store(club, db_path, "eafc26-datahub:main")
        except ValueError as import_error:
            st.error(str(import_error))
            return
        conn = connect(db_path)
        set_setting(conn, "my_team_name", club)
        conn.close()
        if with_scouting:
            with st.spinner("Importing the scouting pool (~18,000 players)..."):
                import_scouting_candidates(db_path, "eafc26-datahub:main", exclude_club_names=[club])
        st.session_state["schedule_flash"] = f"Career started — {stored} player(s) imported for {club}."
        st.rerun()


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


def _flash(key: str) -> None:
    """Show-and-clear a success message stored before an st.rerun(), so
    writes can refresh every table above the button that triggered them
    without losing their confirmation."""
    message = st.session_state.pop(key, None)
    if message:
        st.success(message)


def _team_picker(conn, label: str, key: str) -> str | None:
    """Selectbox over existing teams with a 'New team…' escape hatch.
    Returns the chosen team NAME (None until one is picked/typed) — the
    caller creates it via get_or_create_team on its button click, so a
    half-typed name never writes a junk team row."""
    teams = queries.all_teams(conn)
    options = [t["name"] for t in teams] + ["➕ New team…"]
    choice = st.selectbox(label, options, key=key)
    if choice != "➕ New team…":
        return choice
    new_name = st.text_input("New team name", key=f"{key}_new").strip()
    return new_name or None


def schedule_tab(conn, team_id: int, db_path: str) -> None:
    _flash("schedule_flash")

    record = queries.team_record(conn, team_id)
    if record:
        st.subheader("Record")
        st.dataframe(pd.DataFrame(record), use_container_width=True, hide_index=True)
    else:
        st.caption("No completed matches yet — the W/D/L record appears once fixtures have scores.")

    st.subheader("Fixtures")
    fixtures = queries.schedule(conn)
    if fixtures:
        df = pd.DataFrame(fixtures)[
            ["date", "competition", "home_team", "home_score", "away_score", "away_team", "captures", "screenshot_dir"]
        ]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No fixtures yet — add the first one below.")

    st.divider()
    st.subheader("Add a fixture")
    my_team_name = conn.execute("SELECT name FROM teams WHERE team_id = ?", (team_id,)).fetchone()["name"]
    col_date, col_comp, col_venue = st.columns(3)
    match_date = col_date.date_input("Date", value=date_type.today(), key="fx_date")
    existing_comps = sorted(
        {f["competition"] for f in fixtures if f["competition"]} | {"Premier League", "FA Cup", "Carabao Cup", "Champions League", "Friendly"}
    )
    comp_choice = col_comp.selectbox("Competition", existing_comps + ["➕ Other…"], key="fx_comp")
    competition = col_comp.text_input("Competition name", key="fx_comp_new").strip() if comp_choice == "➕ Other…" else comp_choice
    venue = col_venue.radio("Venue", ["Home", "Away"], horizontal=True, key="fx_venue")
    opponent_name = _team_picker(conn, f"Opponent (you are {my_team_name})", key="fx_opponent")

    base_dir = get_setting(conn, "screenshot_base_dir") or "data/screenshots"
    folder_name = f"{match_date.isoformat()}_{_slugify(opponent_name)}" if opponent_name else match_date.isoformat()
    default_dir = os.path.join(base_dir, folder_name)
    screenshot_dir = st.text_input(
        "Screenshot folder for this match (drop this day's images there, then hit Process below)",
        value=default_dir,
        # keying on the default makes the suggestion follow the date/opponent
        # pickers; a hand-typed path sticks until either of those changes
        key=f"fx_dir_{folder_name}",
    )
    st.caption("Change the base folder under Manage → Settings — subfolder-per-match under one parent folder works great.")
    if st.button("Create fixture", type="primary", disabled=opponent_name is None or opponent_name == my_team_name):
        opponent_id = get_or_create_team(conn, opponent_name)
        season_id = get_or_create_season(conn, "2025-26")
        matchweek = conn.execute("SELECT COUNT(*) + 1 FROM matches WHERE season_id = ?", (season_id,)).fetchone()[0]
        home_id, away_id = (team_id, opponent_id) if venue == "Home" else (opponent_id, team_id)
        create_match(
            conn, season_id, matchweek, home_id, away_id, screenshot_dir,
            date=match_date.isoformat(), competition=competition or None,
        )
        st.session_state["schedule_flash"] = f"Fixture created for {match_date.isoformat()}."
        st.rerun()

    if not fixtures:
        return

    st.divider()
    st.subheader("Record result / process screenshots")
    labels = {
        f"{f['date'] or 'undated'}  {f['home_team']} vs {f['away_team']}"
        f"  ({f['competition'] or 'no comp'}, id {f['match_id']})": f
        for f in fixtures
    }
    fixture = labels[st.selectbox("Fixture", list(labels), key="fx_edit")]

    col_home, col_away, col_save = st.columns([1, 1, 2])
    home_score = col_home.number_input(f"{fixture['home_team']} goals", min_value=0, step=1,
                                       value=fixture["home_score"] or 0, key="fx_hs")
    away_score = col_away.number_input(f"{fixture['away_team']} goals", min_value=0, step=1,
                                       value=fixture["away_score"] or 0, key="fx_as")
    col_save.write("")
    if col_save.button("Save result"):
        update_match_result(conn, fixture["match_id"], int(home_score), int(away_score))
        st.session_state["schedule_flash"] = "Result saved — the record above updates from it."
        st.rerun()

    st.subheader("Match facts")
    score = (
        f"{fixture['home_team']} {fixture['home_score']} : {fixture['away_score']} {fixture['away_team']}"
        if fixture["home_score"] is not None and fixture["away_score"] is not None
        else f"{fixture['home_team']} vs {fixture['away_team']} — no result recorded yet"
    )
    st.markdown(f"**{score}**" + (f"  ·  {fixture['competition']}" if fixture["competition"] else "")
                + (f"  ·  {fixture['date']}" if fixture["date"] else ""))

    event_icons = {"goal": "⚽", "missed_penalty": "❌⚽", "yellow_card": "🟨",
                   "red_card": "🟥", "substitution": "🔁", "unknown": "❔"}
    events = queries.match_events_list(conn, fixture["match_id"])
    if events:
        st.dataframe(
            pd.DataFrame(
                {
                    "minute": e["minute"],
                    "event": f"{event_icons.get(e['event_type'], '❔')} {e['event_type']}",
                    "player": e["player"] or "(unmatched)",
                    "team": e["team"] or "",
                }
                for e in events
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("No parsed events for this fixture yet — process its events screenshots below.")

    stats = queries.match_team_stats(conn, fixture["match_id"])
    if stats:
        with st.expander("Team stats (from the team summary screenshot)"):
            st.dataframe(
                pd.DataFrame(stats).rename(
                    columns={"home": fixture["home_team"], "away": fixture["away_team"]}
                ),
                use_container_width=True, hide_index=True,
            )

    st.caption(
        f"{fixture['captures']} capture(s) processed for this fixture so far. "
        f"Screenshots are read from `{fixture['screenshot_dir']}`."
    )
    col_process, col_recompute, col_delete = st.columns(3)
    if col_process.button("Process screenshots (OCR)"):
        if not os.path.isdir(fixture["screenshot_dir"]):
            st.error(f"Folder not found: {fixture['screenshot_dir']} — create it and drop this match's images in.")
        else:
            with st.spinner("Running OCR — the first run downloads/loads the EasyOCR model and takes a while..."):
                from fifa_analytics.ocr.pipeline import run_match_dir  # heavy import, only on click

                run_match_dir(db_path, fixture["screenshot_dir"], fixture["match_id"],
                              fixture["home_team"], fixture["away_team"])
            st.session_state["schedule_flash"] = (
                "Screenshots processed. Review them in the validation UI "
                "(`streamlit run src/fifa_analytics/validate_app.py -- --db "
                f"{db_path}`), then click 'Recompute model + xPTS'."
            )
            st.rerun()
    if col_recompute.button("Recompute model + xPTS"):
        history_rows = recompute_all(db_path)
        xpts_rows = compute_xpts(db_path)
        st.session_state["schedule_flash"] = (
            f"Recomputed from reviewed data: {history_rows} true-overall row(s), {xpts_rows} xPTS row(s)."
        )
        st.rerun()
    if col_delete.button("Delete fixture", type="secondary"):
        delete_match(conn, fixture["match_id"])
        st.session_state["schedule_flash"] = "Fixture deleted (including any captures/stats attached to it)."
        st.rerun()


def manage_tab(conn, db_path: str) -> None:
    _flash("manage_flash")

    st.subheader("Settings")
    current_base = get_setting(conn, "screenshot_base_dir") or "data/screenshots"
    new_base = st.text_input(
        "Base screenshots folder (each match gets its own subfolder inside it — "
        r"e.g. C:\Users\you\FIFA_Screenshots)",
        value=current_base, key="set_base_dir",
    )
    if st.button("Save settings", disabled=new_base.strip() == current_base):
        set_setting(conn, "screenshot_base_dir", new_base.strip())
        st.session_state["manage_flash"] = "Settings saved."
        st.rerun()

    st.divider()
    st.subheader("Player search & transfer")
    st.caption(
        "Re-home a player whose move you know about but haven't captured yet "
        "— the OCR pipeline does this automatically when it sees them in a "
        "screenshot, this is for getting ahead of it."
    )
    search = st.text_input("Search players by name (any team, regens included)", key="pl_search")
    if search.strip():
        found = queries.search_players(conn, search.strip())
        if not found:
            st.caption("No players match.")
        else:
            st.dataframe(pd.DataFrame(found), use_container_width=True, hide_index=True)
            options = {
                f"{p['name']} ({p['team'] or 'no team'}, {p['position'] or '?'}, {p['base_overall'] or 'regen'})": p
                for p in found
            }
            player = options[st.selectbox("Player to move", list(options), key="pl_pick")]
            destination_name = _team_picker(conn, "New team", key="pl_dest")
            if st.button("Transfer player", type="primary", disabled=destination_name is None):
                destination_id = get_or_create_team(conn, destination_name)
                set_player_team(conn, player["player_id"], destination_id)
                st.session_state["manage_flash"] = f"{player['name']} moved to {destination_name}."
                st.rerun()

    st.divider()
    st.subheader("Import a team's card data")
    st.caption(
        "Pulls a club's full roster from EAFC26-DataHub — do this for a new "
        "opponent before processing their screenshots so players match by name."
    )
    club = st.text_input("Club name exactly as the dataset spells it (e.g. 'Bayer 04 Leverkusen')", key="imp_club")
    if st.button("Import card data", disabled=not club.strip()):
        from fifa_analytics.cards.eafc26_datahub_importer import scrape_and_store  # network only on click

        with st.spinner("Fetching the dataset..."):
            stored = scrape_and_store(club.strip(), db_path, "eafc26-datahub:main")
        if stored:
            st.session_state["manage_flash"] = f"{stored} player(s) imported for {club.strip()}."
            st.rerun()
        else:
            st.error(f"No players found for {club.strip()!r} — check the exact club spelling in the dataset.")

    st.divider()
    st.subheader("Danger zone: reset match stats")
    counts = {
        "matches": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
        "captures": conn.execute("SELECT COUNT(*) FROM ocr_captures").fetchone()[0],
        "stat values": conn.execute("SELECT COUNT(*) FROM match_stat_values").fetchone()[0],
        "model history rows": conn.execute("SELECT COUNT(*) FROM true_overall_history").fetchone()[0],
    }
    st.caption(
        f"Deletes ALL match data — {counts['matches']} match(es), {counts['captures']} capture(s), "
        f"{counts['stat values']} stat value(s), {counts['model history rows']} model history row(s), "
        "plus events/xPTS/seasons. Teams, players (card data), and the scouting pool are kept. "
        "This cannot be undone."
    )
    confirmation = st.text_input("Type RESET to confirm", key="reset_confirm")
    if st.button("Reset match stats", type="primary", disabled=confirmation != "RESET"):
        deleted = reset_match_data(conn)
        total = sum(deleted.values())
        st.session_state["manage_flash"] = f"Reset done — {total} row(s) deleted: " + ", ".join(
            f"{table} {n}" for table, n in deleted.items() if n
        )
        st.rerun()


def assistant_tab(conn, team_id: int) -> None:
    from fifa_analytics.assistant import llm
    from fifa_analytics.assistant.context import build_messages

    st.caption(
        "Grounded in this career's data: every answer is computed from your "
        "true overalls, best-XI solver, matchup and transfer engines — the "
        "model reasons over real numbers, it can't invent them."
    )
    ollama_up = llm.is_available()
    if ollama_up:
        models = llm.list_models() or [llm.DEFAULT_MODEL]
        model = st.selectbox("Ollama model", models, key="asst_model")
    else:
        st.info(llm.SETUP_HELP)
        st.caption(
            "Until then, asking a question below still computes and shows "
            "the relevant data pack — just without the written answer."
        )
        model = None

    history = st.session_state.setdefault("asst_history", [])
    for turn in history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    question = st.chat_input(
        "e.g. 'Pick my strongest XI on true overalls', 'How should I set up "
        "against Arsenal?', 'Who should I sell?', 'What does xPTS mean?'"
    )
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)
    messages, pack = build_messages(question, conn, team_id, history)

    with st.chat_message("assistant"):
        with st.expander("Data used for this answer"):
            st.json({k: v for k, v in pack.items() if k != "explainers"})
        if ollama_up:
            try:
                with st.spinner(f"Asking {model}..."):
                    answer = llm.chat(messages, model=model)
            except ConnectionError as gone:
                answer = str(gone)
            st.markdown(answer)
        else:
            answer = (
                "(Ollama isn't running — the data pack above is what I'd "
                "answer from. Install Ollama to get written advice.)"
            )
            st.markdown(answer)

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})


def main():
    st.set_page_config(page_title="FIFA Career Mode Analytics", layout="wide")
    st.title("FIFA Career Mode Analytics")

    db_path = pick_career_db(get_db_path())
    init_db(db_path)  # idempotent; applies schema migrations on upgrade
    conn = get_conn(db_path)
    teams = queries.teams_with_players(conn)
    if not teams:
        first_run_wizard(db_path)
        return

    my_team_name = get_setting(conn, "my_team_name")
    labels = {f"{t['name']} ({t['player_count']} players)": t["team_id"] for t in teams}
    default_index = next(
        (i for i, t in enumerate(teams) if t["name"] == my_team_name), 0
    )
    selectbox_label = "Viewing team" if my_team_name else "Team"
    team_key = st.sidebar.selectbox(selectbox_label, list(labels), index=default_index)
    team_id = labels[team_key]
    if my_team_name:
        st.sidebar.caption(f"Your club: **{my_team_name}**")
    else:
        selected_name = team_key.rsplit(" (", 1)[0]
        if st.sidebar.button(f"Make {selected_name} my club"):
            set_setting(conn, "my_team_name", selected_name)
            st.rerun()
    st.sidebar.caption(
        "Analysis tabs read reviewed data only (whatever the model/xPTS "
        "recompute last saw). Schedule and Manage write, but only when you "
        "click their buttons."
    )

    tab_squad, tab_progress, tab_season, tab_xi, tab_scout, tab_schedule, tab_manage, tab_assistant = st.tabs(
        ["Squad", "Progression", "Season (xPTS)", "Best XI", "Scouting", "Schedule", "Manage", "Assistant"]
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
    with tab_schedule:
        schedule_tab(conn, team_id, db_path)
    with tab_manage:
        manage_tab(conn, db_path)
    with tab_assistant:
        assistant_tab(conn, team_id)


if __name__ == "__main__":
    main()
