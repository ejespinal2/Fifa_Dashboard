from fifa_analytics.ocr.extract import group_fragments_into_lines


def _fragment(text, y_top, y_bottom, x_left, confidence=0.9):
    return {"text": text, "confidence": confidence, "y_top": y_top, "y_bottom": y_bottom, "x_left": x_left}


def test_fragments_on_same_height_join_left_to_right():
    lines = group_fragments_into_lines([
        _fragment("37'", 10, 30, 200),
        _fragment("B. Fernandes", 12, 28, 50),
    ])
    assert len(lines) == 1
    assert lines[0]["text"] == "B. Fernandes 37'"


def test_fragments_at_different_heights_become_separate_ordered_lines():
    lines = group_fragments_into_lines([
        _fragment("Casemiro 55", 100, 120, 50),
        _fragment("B. Fernandes 37", 10, 30, 50),
        _fragment("Rashford 78", 200, 220, 50),
    ])
    assert [l["text"] for l in lines] == ["B. Fernandes 37", "Casemiro 55", "Rashford 78"]
    # each line keeps its own vertical band for per-row icon lookup
    assert lines[0]["y_top"] == 10 and lines[0]["y_bottom"] == 30
    assert lines[2]["y_top"] == 200 and lines[2]["y_bottom"] == 220


def test_confidence_averages_within_a_line():
    lines = group_fragments_into_lines([
        _fragment("A", 0, 10, 0, confidence=1.0),
        _fragment("B", 2, 8, 20, confidence=0.5),
    ])
    assert lines[0]["confidence"] == 0.75


def test_empty_input():
    assert group_fragments_into_lines([]) == []
