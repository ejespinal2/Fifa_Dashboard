from fifa_analytics.cards.eafc26_datahub_importer import find_by_exact_name

ROWS = [
    {"short_name": "Bruno Fernandes"},
    {"short_name": "B. Mbeumo"},
    {"short_name": "A. Tchouaméni"},
    {"short_name": "K. Thuram"},
]


def test_exact_match():
    assert find_by_exact_name(ROWS, "Bruno Fernandes")["short_name"] == "Bruno Fernandes"


def test_surname_and_initial_match_covers_abbreviated_vs_full_first_name():
    # in-game shows the full first name; the dataset abbreviates it with an accent
    assert find_by_exact_name(ROWS, "Aurelien Tchouameni")["short_name"] == "A. Tchouaméni"


def test_no_match_returns_none():
    assert find_by_exact_name(ROWS, "Nobody Real") is None


def test_ambiguous_exact_match_returns_none():
    ambiguous = ROWS + [{"short_name": "Bruno Fernandes"}]
    assert find_by_exact_name(ambiguous, "Bruno Fernandes") is None


def test_ambiguous_surname_and_initial_returns_none():
    # Two different short_name strings ("K. Thuram" already in ROWS, plus this
    # one) that both share surname "thuram" and initial "k" -- neither exactly
    # matches the query, so this falls to tier 2, where it's genuinely
    # ambiguous which one is meant.
    ambiguous = ROWS + [{"short_name": "K. A. Thuram"}]
    assert find_by_exact_name(ambiguous, "Kevin Thuram") is None
