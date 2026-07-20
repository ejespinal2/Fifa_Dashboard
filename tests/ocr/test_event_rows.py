"""parse_event_rows against fragment layouts replicating the real
Atlético 0:2 Man Utd Events-tab screenshots: minute circles on a center
spine, home names left / away names right, double-sub rows, hanging
outgoing-player lines, HT markers. Coordinates are fractions of the event
band, measured from the 2000x1125 originals."""

from fifa_analytics.ocr.event_parse import parse_event_rows, parse_minute


def _fragment(text, x_left, x_right, y_top, y_bottom):
    return {"text": text, "confidence": 0.9,
            "x_left": x_left, "x_right": x_right, "y_top": y_top, "y_bottom": y_bottom}


def test_parse_minute_forms():
    assert parse_minute("65'") == 65
    assert parse_minute("65") == 65
    assert parse_minute("45+2'") == 45
    assert parse_minute("90,") is None
    assert parse_minute("HT") is None
    assert parse_minute("B. Fernandes") is None


def test_double_sub_row_produces_one_event_per_side():
    # screenshot 1, 65': J. Cardoso on for Koke (home) | P. Dorgu on for
    # M. Rashford (away), with both outgoing names on the hanging line below
    fragments = [
        _fragment("J. Cardoso", 0.30, 0.40, 0.10, 0.14),
        _fragment("65'", 0.485, 0.515, 0.10, 0.14),
        _fragment("P. Dorgu", 0.62, 0.70, 0.10, 0.14),
        _fragment("Koke", 0.41, 0.45, 0.155, 0.185),
        _fragment("M. Rashford", 0.55, 0.63, 0.155, 0.185),
    ]
    rows = parse_event_rows(fragments)
    assert len(rows) == 2
    home, away = (r for r in rows if r["side"] == "home"), (r for r in rows if r["side"] == "away")
    home, away = next(home), next(away)
    assert (home["name"], home["minute"], home["sub_off_name"]) == ("J. Cardoso", 65, "Koke")
    assert (away["name"], away["minute"], away["sub_off_name"]) == ("P. Dorgu", 65, "M. Rashford")


def test_single_side_rows_and_ht_marker():
    # screenshot 2: 41' goal (away), HT marker, 45' sub with hanging name
    fragments = [
        _fragment("B. Šeško", 0.62, 0.70, 0.05, 0.09),
        _fragment("41'", 0.485, 0.515, 0.05, 0.09),
        _fragment("HT", 0.49, 0.51, 0.25, 0.29),
        _fragment("45'", 0.485, 0.515, 0.45, 0.49),
        _fragment("Amad", 0.62, 0.66, 0.45, 0.49),
        _fragment("B. Mbeumo", 0.56, 0.64, 0.50, 0.53),
    ]
    rows = parse_event_rows(fragments)
    assert len(rows) == 2
    goal, sub = rows
    assert (goal["side"], goal["name"], goal["minute"], goal["sub_off_name"]) == ("away", "B. Šeško", 41, None)
    assert (sub["side"], sub["name"], sub["minute"], sub["sub_off_name"]) == ("away", "Amad", 45, "B. Mbeumo")


def test_hanging_name_not_adopted_across_sides_or_distance():
    fragments = [
        _fragment("75'", 0.485, 0.515, 0.10, 0.14),
        _fragment("K. Thuram", 0.62, 0.70, 0.10, 0.14),
        # a home-side line below must NOT become the away sub's off-player
        _fragment("Á. Baena", 0.40, 0.46, 0.155, 0.185),
        # a far-away line must not be adopted either
        _fragment("Loose Text", 0.60, 0.68, 0.60, 0.64),
    ]
    rows = parse_event_rows(fragments)
    assert len(rows) == 1
    assert rows[0]["sub_off_name"] is None


def test_shirt_number_in_name_zone_is_not_a_minute():
    # a number far from the spine (e.g. OCR catching a squad number next to
    # the name) must not create an event row
    fragments = [
        _fragment("8", 0.75, 0.77, 0.10, 0.14),
        _fragment("Bruno", 0.80, 0.86, 0.10, 0.14),
    ]
    assert parse_event_rows(fragments) == []
