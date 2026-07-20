from fifa_analytics.ocr.classify_screen import decide


def test_player_header_wins():
    assert decide("PLAYER PERFORMANCE", ["anything"]) == "player_summary"
    assert decide("Player Performance | Summary", []) == "player_summary"


def test_possession_threat_screen_is_unsupported():
    # exactly what the user's Possession-tab screenshots contain
    lines = ["Threat", "00:00", "32% Overall Possession", "45:00", "OVERALL"]
    assert decide("MAN UTD 1 : 0 LEVERKUSEN", lines) == "unsupported"


def test_stat_labels_mean_team_summary():
    lines = ["32 Possession % 68", "10 Shots 12", "1.8 Expected Goals 0.9", "400 Passes 620"]
    assert decide("MAN UTD 1 : 0 LEVERKUSEN", lines) == "team_summary"


def test_player_minute_rows_mean_team_events():
    lines = ["Events", "B. Fernandes 37'", "Casemiro 55"]
    assert decide("MAN UTD 1 : 0 LEVERKUSEN", lines) == "team_events"


def test_garbage_is_unsupported_not_misrouted():
    assert decide("MAN UTD 1 : 0 LEVERKUSEN", ["random words", "no structure"]) == "unsupported"


def test_spine_layout_event_rows_classify_as_events():
    # real Events-tab lines: minute mid-line, names either side
    lines = ["J. Cardoso 65' P. Dorgu", "65' D. Spence", "HT"]
    assert decide("ATLETICO DE MADRID 0 : 2 MAN UTD", lines) == "team_events"
