from fifa_analytics.ocr import regions


def test_even_rows_splits_full_height():
    box = (0.1, 0.2, 0.9, 0.8)
    rows = regions.even_rows(box, 4)
    assert len(rows) == 4
    assert rows[0][1] == box[1]
    assert rows[-1][3] == box[3]
    # rows are contiguous, no gaps or overlaps
    for a, b in zip(rows, rows[1:]):
        assert a[3] == b[1]


def test_player_summary_stat_order_matches_region_row_count():
    assert regions.PLAYER_SUMMARY_REGIONS["stat_list_row_count"] == len(regions.PLAYER_SUMMARY_STAT_ORDER)


def test_team_summary_stat_order_matches_stat_list_row_count():
    rows = regions.even_rows(regions.TEAM_SUMMARY_REGIONS["stat_list_box"], len(regions.TEAM_SUMMARY_STAT_ORDER))
    assert len(rows) == len(regions.TEAM_SUMMARY_STAT_ORDER)
