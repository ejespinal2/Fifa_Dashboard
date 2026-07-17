from fifa_analytics.ocr.player_match import match_player, normalize, surname

CANDIDATES = [
    {"player_id": 1, "name": "Bruno Fernandes", "team_id": 11},
    {"player_id": 2, "name": "B. Mbeumo", "team_id": 11},
    {"player_id": 3, "name": "A. Tchouameni", "team_id": 11},
    {"player_id": 4, "name": "K. Thuram", "team_id": 12},
]


def test_normalize_strips_accents_and_case():
    assert normalize("Aurélien Tchouaméni") == "aurelien tchouameni"


def test_surname_is_last_token():
    assert surname("Aurélien Tchouaméni") == "tchouameni"
    assert surname("B. Mbeumo") == "mbeumo"


def test_exact_match():
    result = match_player("Bruno Fernandes", CANDIDATES)
    assert result.player_id == 1
    assert result.confidence == "exact"


def test_surname_match_for_full_name_ocr_vs_abbreviated_card_name():
    # OCR reads the in-game full name; card source has it abbreviated
    result = match_player("Aurelien Tchouameni", CANDIDATES)
    assert result.player_id == 3
    assert result.confidence == "surname"


def test_no_match_returns_none():
    result = match_player("Some Rando", CANDIDATES)
    assert result.player_id is None
    assert result.confidence == "none"


def test_ambiguous_surname_falls_through_to_fuzzy():
    ambiguous = CANDIDATES + [{"player_id": 5, "name": "J. Mbeumo", "team_id": 12}]
    # A full name that doesn't exactly match any candidate string, but shares
    # an ambiguous surname between two candidates -- exercises the fuzzy
    # fallback rather than the exact-match or surname-uniqueness shortcuts.
    result = match_player("Bryan Mbeumo", ambiguous)
    assert result.confidence in ("fuzzy", "none")
    if result.player_id is not None:
        assert result.player_id in (2, 5)
