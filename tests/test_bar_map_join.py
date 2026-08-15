"""Reading a cut against the bar map: the columns, and the two blocks taken over them.

Hand-written bar records in, dicts out — ``bars.py``'s own tests cover how a map is found, and
these cover what an edit reads against one. The two cases worth their own test are the ones a
whole-report run would hide: a cut that lands *before* a downbeat is a cut on the one, and a
grid the beat gate refused wholesale is exactly when this reading has to keep working.
"""

from __future__ import annotations

from typing import Any

from resolve_mcp.analysis import barmap

BARS: tuple[dict[str, Any], ...] = (
    {"bar": 1, "t": 0.0, "in_group": 1},
    {"bar": 2, "t": 2.0, "in_group": 2},
    {"bar": 3, "t": 4.0, "in_group": 3},
    {"bar": 4, "t": 6.0, "in_group": 4},
    {"bar": 5, "t": 8.0, "in_group": 1},
)
"""Five two-second bars, the shape ``detect_bars`` writes."""


def _cut(**columns: Any) -> dict[str, Any]:
    """A record carrying only what this join reads back."""
    return {"in_group": None, "bar_offset": None, **columns}


# --- the columns ------------------------------------------------------------------------------


def test_the_bar_lines_are_read_off_the_map_in_order() -> None:
    assert barmap.times(BARS) == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_no_map_is_no_lines_rather_than_an_error() -> None:
    assert barmap.times(None) == []


def test_a_cut_on_a_downbeat_lands_on_that_bar_with_no_offset() -> None:
    assert barmap.reading(BARS, barmap.times(BARS), 4.0) == {
        "map_bar": 3,
        "in_group": 3,
        "bar_offset": 0.0,
    }


def test_a_cut_just_before_a_downbeat_is_a_cut_on_the_one_read_early() -> None:
    read = barmap.reading(BARS, barmap.times(BARS), 3.98)
    assert read["map_bar"] == 3
    assert read["bar_offset"] == -0.02


def test_a_cut_just_after_a_downbeat_is_read_late_against_the_same_bar() -> None:
    read = barmap.reading(BARS, barmap.times(BARS), 4.02)
    assert read["map_bar"] == 3
    assert read["bar_offset"] == 0.02


def test_a_cut_past_the_end_of_the_map_is_read_against_its_last_bar() -> None:
    read = barmap.reading(BARS, barmap.times(BARS), 20.0)
    assert read["map_bar"] == 5
    assert read["bar_offset"] == 12.0


def test_no_map_leaves_all_three_columns_null_together() -> None:
    # Spelled out rather than taken from ``COLUMNS``: this is the record's contract, and an
    # assertion built from the same constant the implementation builds from would survive a
    # column being renamed on both sides at once.
    assert barmap.reading(None, [], 4.0) == {
        "map_bar": None,
        "in_group": None,
        "bar_offset": None,
    }


def test_the_place_in_the_group_is_the_maps_own() -> None:
    assert barmap.reading(BARS, barmap.times(BARS), 8.1)["in_group"] == 1


# --- the blocks -------------------------------------------------------------------------------


def test_the_group_histogram_and_offsets_are_taken_over_the_cuts_to_music() -> None:
    rows = [
        _cut(in_group=1, bar_offset=0.1),
        _cut(in_group=1, bar_offset=-0.3),
        _cut(in_group=3, bar_offset=0.0),
    ]
    read = barmap.summary(rows, rows[1:])
    assert read["bar_groups"] == {"1": 1, "3": 1}
    assert read["bar_offsets"] == {
        "measured": 2,
        "mean_abs": 0.15,
        "median_abs": 0.15,
        "max_abs": 0.3,
        "early": 1,
        "late": 0,
        "on": 1,
    }


def test_a_call_that_named_no_bar_map_reads_null_rather_than_empty() -> None:
    rows = [_cut(), _cut()]
    assert barmap.summary(rows, rows) == {"bar_groups": None, "bar_offsets": None}


def test_the_join_reads_no_gate_column_at_all() -> None:
    # A bar map exists precisely for the grids the #112 beat gate refuses wholesale (#180), so
    # this join must not consult that verdict — here, rows carrying ``in_grid`` false still
    # produce both blocks in full. That the *report* leaves the blocks ungated end to end is a
    # fact about ``correlate._summary``, and is pinned there by
    # ``test_the_group_histogram_is_not_gated_on_the_grid``.
    rows = [
        _cut(in_grid=False, in_group=2, bar_offset=0.05),
        _cut(in_grid=False, in_group=4, bar_offset=-0.05),
    ]
    read = barmap.summary(rows, rows)
    assert read["bar_groups"] == {"2": 1, "4": 1}
    assert read["bar_offsets"]["measured"] == 2


def test_an_opening_is_left_out_of_the_statistics_it_would_skew() -> None:
    rows = [_cut(in_group=1, bar_offset=9.9), _cut(in_group=2, bar_offset=0.1)]
    read = barmap.summary(rows, rows[1:])
    assert read["bar_groups"] == {"2": 1}
    assert read["bar_offsets"]["max_abs"] == 0.1
