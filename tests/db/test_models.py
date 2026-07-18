import sqlite3

from fifa_analytics.db.models import connect, init_db, upsert_scouting_candidate


def test_init_db_is_idempotent(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    init_db(path)  # re-running against an already-current db must not error


def test_init_db_migrates_stale_scouting_candidates_table(tmp_path):
    """A database created before Phase 4 widened scouting_candidates (added
    name/club_name/sub-attributes, dropped fit_score) gets stuck on the old
    columns forever, since CREATE TABLE IF NOT EXISTS is a no-op once the
    table exists -- init_db must detect and migrate it rather than leaving
    every later scouting_importer run to crash on a missing column."""
    path = str(tmp_path / "stale.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE scouting_candidates (
            candidate_id INTEGER PRIMARY KEY,
            position TEXT, age INTEGER, current_overall INTEGER,
            potential INTEGER, fit_score REAL
        )"""
    )
    conn.commit()
    conn.close()

    init_db(path)

    conn = connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(scouting_candidates)")}
    assert "name" in columns and "club_name" in columns and "fit_score" not in columns
    # and the migrated table actually works
    upsert_scouting_candidate(
        conn, "Test Player", "Test FC", "test", "CB", 22, 70, 80,
        base_pace=70, base_shooting=40, base_passing=60,
        base_dribbling=55, base_defending=75, base_physical=70, estimated_wage=None,
    )
    conn.close()
