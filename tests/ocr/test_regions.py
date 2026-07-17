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


def test_team_summary_pages_have_distinct_stat_orders():
    # both pages legitimately repeat some stat names (the scrollable list
    # overlaps at the boundary) but shouldn't be identical
    assert regions.TEAM_SUMMARY_PAGE_1_STAT_ORDER != regions.TEAM_SUMMARY_PAGE_2_STAT_ORDER
