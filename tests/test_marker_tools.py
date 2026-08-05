"""The marker tools — the review loop's transport — against the fake Resolve seam.

The one thing worth stating twice: Resolve keys timeline markers by a frame *relative to
the timeline start*, while every other timeline reading here is in absolute record frames.
These tests use a timeline that starts at frame 100 throughout, so a reading that confused
the two clocks cannot pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.tools.timeline import list_markers, set_markers

from .conftest import Attach
from .fakes import FakeTimeline, FakeTimelineItem, FakeTrack, studio

REVIEW_NOTES: dict[Any, dict[str, Any]] = {
    20.0: {
        "color": "Red",
        "name": "cut early",
        "note": "come off the wide two bars sooner",
        "duration": 1.0,
        "customData": "round-1",
    },
    140.0: {
        "color": "Blue",
        "name": "Sunset Boulevard",
        "note": "song starts here",
        "duration": 30.0,
        "customData": "",
    },
}


def a_reviewed_cut(
    name: str = "sunset-set v3",
    markers: dict[Any, dict[str, Any]] | None = None,
) -> FakeTimeline:
    """A built cut starting at frame 100, carrying the director's GUI review notes."""
    return FakeTimeline(
        name,
        start_frame=100,
        end_frame=400,
        markers=REVIEW_NOTES if markers is None else markers,
        video=[FakeTrack("Cam A", [FakeTimelineItem("C0012.mp4", 100, 300, source_start=1000)])],
    )


# --- read -----------------------------------------------------------------------------------


def test_read_returns_every_marker_with_colour_name_note_and_dual_time(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers()

    assert result["ok"] is True
    assert result["count"] == 2
    first, second = result["markers"]
    assert first["color"] == "Red"
    assert first["name"] == "cut early"
    assert first["note"] == "come off the wide two bars sooner"
    assert first["custom_data"] == "round-1"
    assert first["record"]["frames"] == 120
    assert first["record"]["timecode"] == "00:00:02:00"
    assert first["record"]["fps"] == 59.94
    assert first["duration"]["frames"] == 1
    assert second["name"] == "Sunset Boulevard"
    assert second["record"]["frames"] == 240


def test_read_reports_resolve_own_relative_frame_alongside_the_record_frame(
    attach: Attach,
) -> None:
    """Both clocks, named: the agent plans in record frames, run_python sees the other."""
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers()

    assert [marker["frame"] for marker in result["markers"]] == [20, 140]
    assert [marker["record"]["frames"] for marker in result["markers"]] == [120, 240]
    assert result["timeline"]["start"]["frames"] == 100


def test_read_ends_a_marker_with_duration_half_open(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    span = list_markers()["markers"][1]

    assert span["record"]["frames"] == 240
    assert span["end"]["frames"] == 270


def test_read_counts_the_colours_so_a_review_round_is_visible_at_a_glance(
    attach: Attach,
) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers()

    assert result["colors"] == {"Blue": 1, "Red": 1}


def test_read_can_narrow_to_one_colour(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers(color="blue")

    assert result["count"] == 1
    assert result["markers"][0]["name"] == "Sunset Boulevard"


def test_read_can_narrow_to_a_range_in_record_frames(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers(start=200, end=300)

    assert [marker["name"] for marker in result["markers"]] == ["Sunset Boulevard"]


def test_read_takes_a_range_in_seconds_with_an_explicit_snap(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers(start={"seconds": 3.0, "snap": "floor"})

    assert [marker["name"] for marker in result["markers"]] == ["Sunset Boulevard"]


def test_read_refuses_seconds_without_a_snap(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers(start={"seconds": 3.0})

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"


def test_read_of_a_timeline_with_no_markers_is_empty_not_an_error(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={})))

    result = list_markers()

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["markers"] == []
    assert result["colors"] == {}


def test_read_names_a_timeline_other_than_the_open_one(attach: Attach) -> None:
    other = a_reviewed_cut("sunset-set v2", markers={10.0: {"color": "Green", "note": "old"}})
    attach(studio(timeline=a_reviewed_cut(), timelines=[a_reviewed_cut(), other]))

    result = list_markers(timeline="sunset-set v2")

    assert result["timeline"]["name"] == "sunset-set v2"
    assert result["markers"][0]["note"] == "old"


def test_read_of_a_timeline_that_is_not_there_says_which_ones_are(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = list_markers(timeline="sunset-set v9")

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_not_found"


def test_read_spills_past_the_cap_rather_than_flooding_the_reply(attach: Attach) -> None:
    many = {float(frame): {"color": "Red", "note": f"note {frame}"} for frame in range(0, 50)}
    attach(studio(timeline=a_reviewed_cut(markers=many)))

    result = list_markers(limit=10)

    assert result["count"] == 50
    assert result["truncated"] is True
    assert len(result["markers"]) == 10
    spilled = Path(result["spilled_to"])
    assert len(json.loads(spilled.read_text(encoding="utf-8"))["markers"]) == 50


def test_read_survives_a_marker_resolve_describes_with_nothing(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={"30": {}})))

    marker = list_markers()["markers"][0]

    assert marker["record"]["frames"] == 130
    assert marker["color"] == ""
    assert marker["note"] == ""
    assert marker["duration"]["frames"] == 1


def test_read_skips_a_marker_it_cannot_place_rather_than_inventing_a_frame(
    attach: Attach,
) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={"nowhere": {"note": "?"}, 40.0: {}})))

    result = list_markers()

    assert result["count"] == 1
    assert result["markers"][0]["record"]["frames"] == 140


def test_read_of_a_build_without_the_marker_getter_is_empty_not_a_failure(
    attach: Attach,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old Resolve missing a getter costs that field; only a dead handle stops the read."""
    cut = a_reviewed_cut()
    monkeypatch.delattr(type(cut), "GetMarkers")
    attach(studio(timeline=cut))

    result = list_markers()

    assert result["ok"] is True
    assert result["count"] == 0


def test_read_needs_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = list_markers()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"


# --- write ----------------------------------------------------------------------------------


def test_set_writes_a_marker_with_colour_name_and_note_at_a_record_frame(
    attach: Attach,
) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    result = set_markers([{"frame": 220, "color": "Yellow", "name": "soft", "note": "unsure"}])

    assert result["ok"] is True
    assert result["added"] == 1
    assert result["failed"] == 0
    written = result["results"][0]
    assert written["ok"] is True
    assert written["record"]["frames"] == 220
    assert written["frame"] == 120
    assert written["unchanged"] is False
    # The relative frame is what reaches Resolve, and a one-frame default duration is what
    # makes the marker visible in the GUI at all.
    assert cut.marker_writes == [
        {
            "frame": 120.0,
            "color": "Yellow",
            "name": "soft",
            "note": "unsure",
            "duration": 1,
            "customData": "",
        }
    ]


def test_a_written_marker_reads_back_through_the_read_tool(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={})))

    set_markers([{"frame": 220, "color": "Blue", "name": "Encore", "note": "song starts"}])

    marker = list_markers()["markers"][0]
    assert marker["record"]["frames"] == 220
    assert marker["name"] == "Encore"
    assert marker["color"] == "Blue"


def test_set_carries_duration_and_custom_data_when_asked(attach: Attach) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    result = set_markers(
        [
            {
                "frame": {"seconds": 2.0, "snap": "floor"},
                "color": "Blue",
                "name": "Sunset Boulevard",
                "duration": 30,
                "custom_data": "songs.json:sunset",
            }
        ]
    )

    assert result["ok"] is True
    written = cut.marker_writes[0]
    assert written["frame"] == 19.0  # 2.0s at 59.94 is record frame 119, start frame 100
    assert written["duration"] == 30
    assert written["customData"] == "songs.json:sunset"


def test_set_writes_a_batch_and_reports_each_one(attach: Attach) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    result = set_markers(
        [
            {"frame": 120, "color": "Blue", "name": "one"},
            {"frame": 240, "color": "Blue", "name": "two"},
        ]
    )

    assert result["added"] == 2
    assert [entry["record"]["frames"] for entry in result["results"]] == [120, 240]
    assert len(cut.marker_writes) == 2


def test_one_bad_entry_never_sinks_the_batch(attach: Attach) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    result = set_markers(
        [
            {"frame": 120, "color": "Blue", "name": "good"},
            {"frame": 130, "color": "Puce", "name": "bad colour"},
            {"frame": 140, "color": "Blue", "name": "also good"},
        ]
    )

    assert result["ok"] is True
    assert result["added"] == 2
    assert result["failed"] == 1
    assert [entry["ok"] for entry in result["results"]] == [True, False, True]
    assert result["results"][1]["error"]["code"] == "invalid_request"
    assert "Puce" in result["results"][1]["error"]["cause"]


def test_set_refuses_a_colour_resolve_does_not_have(attach: Attach) -> None:
    """Resolve answers an unknown colour with a bare False, so the check happens here."""
    attach(studio(timeline=a_reviewed_cut(markers={})))

    result = set_markers([{"frame": 120, "color": "Puce", "note": "n"}])

    assert result["added"] == 0
    error = result["results"][0]["error"]
    assert error["code"] == "invalid_request"
    assert "Blue" in error["fix"]


def test_set_takes_a_colour_in_any_case_and_writes_resolve_own_spelling(attach: Attach) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    set_markers([{"frame": 120, "color": "bLuE", "note": "n"}])

    assert cut.marker_writes[0]["color"] == "Blue"


def test_set_needs_a_colour(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={})))

    result = set_markers([{"frame": 120, "note": "no colour given"}])

    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "invalid_request"


def test_set_refuses_a_frame_outside_the_timeline(attach: Attach) -> None:
    """Resolve drops a marker past the end silently; the bounds are checked here instead."""
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))

    result = set_markers([{"frame": 900, "color": "Blue", "note": "past the end"}])

    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "invalid_request"
    assert result["results"][0]["error"]["detail"]["bounds"] == {"start": 100, "end": 400}
    assert cut.marker_writes == []


def test_set_leaves_a_directors_marker_alone_unless_told_to_replace_it(attach: Attach) -> None:
    """The director's own note is the one thing an agent must never overwrite by accident."""
    cut = a_reviewed_cut()
    attach(studio(timeline=cut))

    result = set_markers([{"frame": 120, "color": "Blue", "name": "mine"}])

    assert result["added"] == 0
    assert result["failed"] == 1
    error = result["results"][0]["error"]
    assert error["code"] == "invalid_request"
    assert error["detail"]["existing"]["name"] == "cut early"
    assert cut.GetMarkers()[20.0]["name"] == "cut early"


def test_set_replaces_an_existing_marker_when_told_to(attach: Attach) -> None:
    cut = a_reviewed_cut()
    attach(studio(timeline=cut))

    result = set_markers(
        [{"frame": 120, "color": "Blue", "name": "mine", "note": "handled"}], replace=True
    )

    assert result["added"] == 1
    assert result["results"][0]["replaced"] is True
    assert cut.GetMarkers()[20.0]["name"] == "mine"
    assert len(cut.GetMarkers()) == 2


def test_a_refused_replacement_puts_the_directors_marker_back(attach: Attach) -> None:
    """A replacement is a delete then an add, and Resolve can refuse the second half."""
    cut = a_reviewed_cut()
    attach(studio(timeline=cut))
    cut.refuse_marker_names = {"mine"}

    result = set_markers([{"frame": 120, "color": "Blue", "name": "mine"}], replace=True)

    assert result["added"] == 0
    assert result["results"][0]["error"]["code"] == "timeline_operation_failed"
    assert "has been put back" in result["results"][0]["error"]["cause"]
    assert cut.GetMarkers()[20.0]["name"] == "cut early"
    assert cut.GetMarkers()[20.0]["note"] == "come off the wide two bars sooner"


def test_writing_the_same_marker_twice_is_not_a_collision_with_the_director(
    attach: Attach,
) -> None:
    """What a batch replayed after a dropped connection looks like: its own work, still there."""
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))
    entry = {"frame": 220, "color": "Blue", "name": "Encore", "note": "song starts"}

    set_markers([entry])
    again = set_markers([entry])

    assert again["ok"] is True
    assert again["added"] == 1
    assert again["results"][0]["unchanged"] is True
    assert len(cut.marker_writes) == 1
    assert len(cut.GetMarkers()) == 1


def test_a_different_marker_on_the_same_frame_is_still_a_collision(attach: Attach) -> None:
    cut = a_reviewed_cut(markers={})
    attach(studio(timeline=cut))
    set_markers([{"frame": 220, "color": "Blue", "name": "Encore"}])

    result = set_markers([{"frame": 220, "color": "Blue", "name": "Encore II"}])

    assert result["failed"] == 1
    assert result["results"][0]["error"]["detail"]["existing"]["name"] == "Encore"


def test_set_reports_a_refusal_from_resolve_rather_than_claiming_success(
    attach: Attach,
) -> None:
    cut = a_reviewed_cut(markers={})
    cut.refuse_markers = True
    attach(studio(timeline=cut))

    result = set_markers([{"frame": 120, "color": "Blue", "note": "n"}])

    assert result["ok"] is True
    assert result["added"] == 0
    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "timeline_operation_failed"


def test_set_refuses_a_time_it_cannot_read(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={})))

    result = set_markers([{"frame": "somewhere", "color": "Blue"}])

    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "invalid_request"


def test_set_refuses_a_duration_shorter_than_a_frame(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut(markers={})))

    result = set_markers([{"frame": 120, "color": "Blue", "duration": 0}])

    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "invalid_request"


def test_set_of_nothing_is_not_an_error(attach: Attach) -> None:
    attach(studio(timeline=a_reviewed_cut()))

    result = set_markers([])

    assert result["ok"] is True
    assert result["added"] == 0
    assert result["results"] == []


def test_set_needs_a_timeline(attach: Attach) -> None:
    attach(studio(timeline=None, timelines=[]))

    result = set_markers([{"frame": 120, "color": "Blue"}])

    assert result["ok"] is False
    assert result["error"]["code"] == "no_timeline_open"


def test_markers_survive_resolve_quitting_mid_read(attach: Attach) -> None:
    """A dead handle is a stated failure, never a half-empty marker list."""
    dying = studio(timeline=a_reviewed_cut())
    dying.die_after(1)
    second = studio(timeline=a_reviewed_cut())
    second.drop()
    attach(dying, second)

    result = list_markers()

    assert result["ok"] is False
    assert result["error"]["code"] == "resolve_unavailable"


@pytest.mark.parametrize("survives", range(1, 12))
def test_a_death_part_way_through_a_read_never_returns_half_the_notes(
    attach: Attach, survives: int
) -> None:
    dying = studio(timeline=a_reviewed_cut())
    dying.die_after(survives)
    attach(dying, studio(timeline=a_reviewed_cut()))

    result = list_markers()

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["markers"][0]["note"] == "come off the wide two bars sooner"
