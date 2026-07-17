"""Streamlit validation UI — the point of Phase 1 that keeps OCR mistakes
from silently poisoning the true-overall model. Shows each unreviewed
capture's screenshot next to its parsed values, editable, before confirming.

Run with: streamlit run src/fifa_analytics/validate_app.py -- --db data/fifa.db
"""

import argparse
import sqlite3
from datetime import datetime, timezone

import streamlit as st

from fifa_analytics.db.models import connect, mark_reviewed


def get_db_path() -> str:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/fifa.db")
    args, _ = parser.parse_known_args()
    return args.db


@st.cache_resource
def get_conn(db_path: str) -> sqlite3.Connection:
    return connect(db_path)


def load_unreviewed_captures(conn: sqlite3.Connection):
    return conn.execute(
        """SELECT capture_id, match_id, capture_type, player_id, team_id,
                  screenshot_path, ocr_confidence_avg, raw_text
           FROM ocr_captures
           WHERE reviewed = 0
           ORDER BY ocr_confidence_avg ASC NULLS FIRST"""
    ).fetchall()


def load_stat_values(conn: sqlite3.Connection, capture_id: int):
    return conn.execute(
        """SELECT stat_name, stat_value, ocr_confidence
           FROM match_stat_values WHERE capture_id = ?""",
        (capture_id,),
    ).fetchall()


def main():
    st.set_page_config(page_title="FIFA Analytics — OCR Validation", layout="wide")
    st.title("OCR Validation Queue")

    conn = get_conn(get_db_path())
    captures = load_unreviewed_captures(conn)

    if not captures:
        st.success("Nothing left to review.")
        return

    st.caption(f"{len(captures)} capture(s) awaiting review, lowest confidence first.")

    capture = captures[0]
    st.subheader(
        f"{capture['capture_type']} — match {capture['match_id']} "
        f"(confidence: {capture['ocr_confidence_avg']:.2f})"
        if capture["ocr_confidence_avg"] is not None
        else f"{capture['capture_type']} — match {capture['match_id']}"
    )

    col_img, col_fields = st.columns([1, 1])

    with col_img:
        st.image(capture["screenshot_path"], use_container_width=True)

    with col_fields:
        if capture["capture_type"] == "team_events":
            st.text_area("Raw OCR text (not yet parsed into structured events)", capture["raw_text"] or "", height=150)
        else:
            rows = load_stat_values(conn, capture["capture_id"])
            edited = {}
            for row in rows:
                low_conf = row["ocr_confidence"] is not None and row["ocr_confidence"] < 0.7
                label = f"{row['stat_name']}" + (" ⚠️ low confidence" if low_conf else "")
                edited[row["stat_name"]] = st.number_input(
                    label, value=row["stat_value"] if row["stat_value"] is not None else 0.0, key=f"{capture['capture_id']}_{row['stat_name']}"
                )

        if st.button("Confirm and mark reviewed", type="primary"):
            if capture["capture_type"] != "team_events":
                for stat_name, value in edited.items():
                    conn.execute(
                        "UPDATE match_stat_values SET stat_value = ? WHERE capture_id = ? AND stat_name = ?",
                        (value, capture["capture_id"], stat_name),
                    )
                conn.commit()
            mark_reviewed(conn, capture["capture_id"], datetime.now(timezone.utc).isoformat())
            st.rerun()


if __name__ == "__main__":
    main()
