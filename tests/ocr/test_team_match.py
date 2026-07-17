from fifa_analytics.ocr.team_match import match_team_header

HOME_ID, AWAY_ID = 1, 2
HOME_NAME, AWAY_NAME = "Manchester United", "Bayer 04 Leverkusen"


def test_abbreviated_home_name_matches_home():
    result = match_team_header("MAN UTD", HOME_ID, HOME_NAME, AWAY_ID, AWAY_NAME)
    assert result.team_id == HOME_ID
    assert result.confidence == "matched"


def test_full_away_name_matches_away():
    result = match_team_header("BAYER LEVERKUSEN", HOME_ID, HOME_NAME, AWAY_ID, AWAY_NAME)
    assert result.team_id == AWAY_ID


def test_single_distinctive_word_matches_away():
    result = match_team_header("LEVERKUSEN", HOME_ID, HOME_NAME, AWAY_ID, AWAY_NAME)
    assert result.team_id == AWAY_ID


def test_similarly_named_clubs_still_disambiguate():
    result = match_team_header("MAN CITY", HOME_ID, "Manchester United", AWAY_ID, "Manchester City")
    assert result.team_id == AWAY_ID


def test_empty_ocr_text_is_unresolved():
    result = match_team_header("", HOME_ID, HOME_NAME, AWAY_ID, AWAY_NAME)
    assert result.team_id is None
    assert result.confidence == "none"
