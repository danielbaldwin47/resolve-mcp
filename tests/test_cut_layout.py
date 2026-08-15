"""Pure-function tests for the cut layout: where every entry lands.

No Resolve, no fakes, and — unlike the rules — no sources table, no clip facts and no
audio block either. The layout answers ``segments`` and ``overlays`` alone, and these
documents carry nothing else, which is the split in #218 stated as a test: anything the
layout needed from the rest of the file would show up here as a KeyError.
"""

from __future__ import annotations

from typing import Any

from resolve_mcp.cut.layout import (
    FIRST_OVERLAY_TRACK,
    entry_duration,
    gaps,
    is_gap,
    overlay_positions,
    overlay_track,
    placements,
    positions,
    shots,
    total_frames,
)


def a_layout() -> dict[str, Any]:
    """Two shots of 200 frames with one overlay anchored 24 frames into the first."""
    return {
        "segments": [
            {"id": "s001", "source": "gtr_close", "in": 1000, "out": 1200},
            {"id": "s002", "source": "keys_wide", "in": 2000, "out": 2200},
        ],
        "overlays": [
            {
                "id": "b03",
                "source": "broll_pan",
                "in": 1200,
                "out": 1300,
                "over": {"segment": "s001", "offset": 24},
            }
        ],
    }


# --- one entry at a time ------------------------------------------------------------------


def test_a_gap_is_the_entry_that_carries_black_rather_than_a_source() -> None:
    assert is_gap({"id": "g001", "gap": 58}) is True
    assert is_gap({"id": "s001", "source": "gtr_close", "in": 0, "out": 10}) is False


def test_a_gaps_duration_is_the_black_itself_and_a_shots_is_its_range() -> None:
    """The one asymmetry in the array: black states its length, a shot implies it."""
    assert entry_duration({"id": "g001", "gap": 58}) == 58
    assert entry_duration({"id": "s001", "source": "gtr_close", "in": 1000, "out": 1200}) == 200


def test_an_overlay_without_a_track_rides_the_first_layer_above_the_cut() -> None:
    assert overlay_track({"id": "b03"}) == FIRST_OVERLAY_TRACK
    assert overlay_track({"id": "b03", "track": 4}) == 4


# --- the document, split by what places a clip --------------------------------------------


def test_shots_and_gaps_partition_the_segments_array() -> None:
    """Every rule about media reads ``shots``, so black is skipped by all of them at once."""
    doc = a_layout()
    doc["segments"].insert(1, {"id": "g001", "gap": 58})

    assert [entry["id"] for entry in shots(doc)] == ["s001", "s002"]
    assert [entry["id"] for entry in gaps(doc)] == ["g001"]


# --- where the entries land ---------------------------------------------------------------


def test_positions_lay_the_segments_out_end_to_end_from_zero() -> None:
    assert positions(a_layout()) == {"s001": (0, 200), "s002": (200, 200)}


def test_a_gap_occupies_record_time_and_moves_everything_after_it() -> None:
    """Black places no clip, but it is a duration in the array, so the sum has to carry it."""
    doc = a_layout()
    doc["segments"].insert(1, {"id": "g001", "gap": 58})

    assert positions(doc) == {"s001": (0, 200), "g001": (200, 58), "s002": (258, 200)}
    assert total_frames(doc) == 458


def test_total_frames_is_the_v1_span_black_included() -> None:
    assert total_frames(a_layout()) == 400


def test_placements_turn_the_cuts_own_offsets_into_absolute_record_frames() -> None:
    """The one place a cut's offsets become timeline frames — a build and a swap share it."""
    assert placements(a_layout(), 3600) == {"s001": (3600, 200), "s002": (3800, 200)}


# --- overlay placement: the numbers E9 judges and the build places against ----------------


def test_overlay_positions_resolve_each_anchor_to_an_absolute_span() -> None:
    """One function answers where an overlay goes, so the rule and the build cannot differ."""
    doc = a_layout()
    doc["overlays"].append(
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1400,
            "out": 1440,
            "over": {"segment": "s002", "offset": 50},
        }
    )

    assert overlay_positions(doc) == {"b03": (24, 100), "b04": (250, 40)}


def test_overlay_positions_skip_an_anchor_that_does_not_exist() -> None:
    """E9 has already refused such a document; there is no position to invent for it."""
    doc = a_layout()
    doc["overlays"][0]["over"]["segment"] = "s999"

    assert overlay_positions(doc) == {}
