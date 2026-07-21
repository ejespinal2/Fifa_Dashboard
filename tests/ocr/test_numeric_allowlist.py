"""read_field must restrict EasyOCR to digits/punctuation only. Real-match
testing showed confusions like 100/reading it wrong, 0/O, 1/I, and 3/8 --
all consistent with EasyOCR scoring the full alphabet (letters included)
as candidates for a field that's actually pure numeric. Narrowing the
character set for a known-numeric crop is a real accuracy fix (fewer
visually-similar candidates to confuse a digit with), not a pixel-level
guess. read_text itself must stay unrestricted by default -- it's also
used for names and headers, where an allowlist would corrupt reading."""

import numpy as np

from fifa_analytics.ocr import extract
from fifa_analytics.ocr.extract import (
    NUMERIC_ALLOWLIST,
    read_field,
    read_leftmost_number,
    read_text,
)


class _FakeReader:
    def __init__(self):
        self.calls = []

    def readtext(self, crop, detail=1, paragraph=False, **kwargs):
        self.calls.append(kwargs)
        return [([[0, 0], [1, 0], [1, 1], [0, 1]], "42", 0.95)]


def test_read_field_restricts_to_numeric_allowlist(monkeypatch):
    fake = _FakeReader()
    monkeypatch.setattr(extract, "_reader", lambda: fake)
    read_field(object())
    assert fake.calls == [{"allowlist": NUMERIC_ALLOWLIST}]


def test_read_text_without_an_allowlist_arg_stays_unrestricted(monkeypatch):
    fake = _FakeReader()
    monkeypatch.setattr(extract, "_reader", lambda: fake)
    read_text(object())
    assert fake.calls == [{}]


def test_read_text_accepts_an_explicit_allowlist(monkeypatch):
    fake = _FakeReader()
    monkeypatch.setattr(extract, "_reader", lambda: fake)
    read_text(object(), allowlist="0123456789")
    assert fake.calls == [{"allowlist": "0123456789"}]


def _box(x_left, x_right):
    return [[x_left, 0], [x_right, 0], [x_right, 10], [x_left, 10]]


class _TwoNumberReader:
    """Mimics a value-span crop holding the player value on the left and the
    team value on the right, returned in arbitrary order."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def readtext(self, crop, detail=1, paragraph=False, **kwargs):
        self.calls.append(kwargs)
        return self.results


def test_read_leftmost_number_returns_the_player_value_not_the_team_value(monkeypatch):
    # player 100 (left), team 94 (right) -- the real "100 shows as 0" case,
    # now read correctly by taking the left number of the two-column span
    fake = _TwoNumberReader([(_box(60, 90), "94", 0.9), (_box(10, 45), "100", 0.95)])
    monkeypatch.setattr(extract, "_reader", lambda: fake)
    value, conf = read_leftmost_number(np.zeros((10, 100), dtype=np.uint8))
    assert value == 100.0
    assert conf == 0.95
    assert fake.calls == [{"allowlist": NUMERIC_ALLOWLIST}]


def test_read_leftmost_number_empty_when_no_numbers(monkeypatch):
    fake = _TwoNumberReader([])
    monkeypatch.setattr(extract, "_reader", lambda: fake)
    assert read_leftmost_number(np.zeros((10, 100), dtype=np.uint8)) == (None, 0.0)
