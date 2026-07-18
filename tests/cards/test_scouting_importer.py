import csv
import tempfile
from pathlib import Path

import pytest

from fifa_analytics.cards.scouting_importer import import_scouting_candidates
from fifa_analytics.db.models import all_scouting_candidates, connect, init_db

FIELDNAMES = [
    "short_name", "club_name", "club_position", "player_positions", "age",
    "overall", "potential", "pace", "shooting", "passing", "dribbling",
    "defending", "physic", "wage_eur",
]

ROWS = [
    # A young, high-potential winger at a club we're NOT excluding -- should
    # pass every filter.
    {"short_name": "Y. Wonderkid", "club_name": "Some Club", "club_position": "RW",
     "player_positions": "RW, ST", "age": "18", "overall": "72", "potential": "89",
     "pace": "88", "shooting": "70", "passing": "68", "dribbling": "82",
     "defending": "30", "physic": "65", "wage_eur": "15000"},
    # A player at OUR club -- must be excluded regardless of stats.
    {"short_name": "Own Player", "club_name": "Manchester United", "club_position": "CM",
     "player_positions": "CM", "age": "24", "overall": "80", "potential": "82",
     "pace": "70", "shooting": "65", "passing": "80", "dribbling": "75",
     "defending": "60", "physic": "70", "wage_eur": "50000"},
    # Too old for a max_age=23 filter.
    {"short_name": "Old Veteran", "club_name": "Other Club", "club_position": "CB",
     "player_positions": "CB", "age": "34", "overall": "83", "potential": "83",
     "pace": "60", "shooting": "35", "passing": "60", "dribbling": "55",
     "defending": "85", "physic": "80", "wage_eur": "40000"},
    # Potential too low for a min_potential=80 filter.
    {"short_name": "Journeyman", "club_name": "Other Club", "club_position": "CDM",
     "player_positions": "CDM", "age": "22", "overall": "68", "potential": "70",
     "pace": "65", "shooting": "50", "passing": "65", "dribbling": "60",
     "defending": "68", "physic": "70", "wage_eur": "8000"},
]


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "players.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ROWS)
    return str(path)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


def test_excludes_own_club_and_applies_age_and_potential_filters(csv_path, db_path):
    count = import_scouting_candidates(
        db_path, "test-source", exclude_club_names=["Manchester United"],
        csv_source=csv_path, max_age=23, min_potential=80,
    )
    assert count == 1  # only Y. Wonderkid clears every bar

    conn = connect(db_path)
    names = {c["name"] for c in all_scouting_candidates(conn)}
    conn.close()
    assert names == {"Y. Wonderkid"}


def test_no_filters_still_excludes_own_club(csv_path, db_path):
    count = import_scouting_candidates(db_path, "test-source", exclude_club_names=["Manchester United"], csv_source=csv_path)
    assert count == 3  # everyone except Own Player

    conn = connect(db_path)
    names = {c["name"] for c in all_scouting_candidates(conn)}
    conn.close()
    assert "Own Player" not in names


def test_reimport_clears_previous_snapshot(csv_path, db_path):
    import_scouting_candidates(db_path, "test-source", csv_source=csv_path)
    import_scouting_candidates(db_path, "test-source", csv_source=csv_path, max_age=20)

    conn = connect(db_path)
    candidates = all_scouting_candidates(conn)
    conn.close()
    # The stricter second import should have REPLACED the first, not added to it
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Y. Wonderkid"


def test_stored_candidate_has_sub_attributes(csv_path, db_path):
    import_scouting_candidates(db_path, "test-source", csv_source=csv_path, max_age=20)
    conn = connect(db_path)
    candidate = all_scouting_candidates(conn)[0]
    conn.close()
    assert candidate["base_pace"] == 88
    assert candidate["current_overall"] == 72
    assert candidate["potential"] == 89


def test_list_clubs_returns_sorted_distinct_names(csv_path):
    from fifa_analytics.cards.eafc26_datahub_importer import list_clubs

    assert list_clubs(csv_path) == ["Manchester United", "Other Club", "Some Club"]
