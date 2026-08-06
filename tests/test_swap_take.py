"""swap_take: the one in-place edit the cut model allows, and what it refuses to do blind.

Every test here swaps on a timeline this build actually made, because that is the only
timeline a swap is defined against — the selector's order comes from the cut file, and a
cut file that no longer describes the timeline is the failure mode worth catching, not a
detail. The server never writes the cut file, so the report has to hand the agent the edit
that puts the two back in step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from resolve_mcp.tools.cut import build_timeline, swap_take

from .conftest import Attach
from .cutfile import a_cut, a_pool, built, doc_with_alternates, empty_project, shots, valid_doc
from .fakes import FakeTimeline, studio


def a_built_cut(attach: Attach, tmp_path: Path, doc: Any | None = None) -> tuple[Any, str]:
    """Build the alternates cut and hand back the fake and the cut file that made it."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    cut_file = a_cut(tmp_path, doc_with_alternates() if doc is None else doc)
    result = build_timeline(cut_file)
    assert result["ok"] is True
    return resolve, cut_file


def selected(resolve: Any, position: int) -> int:
    return int(shots(built(resolve, "sunset-set v1"))[position].GetSelectedTakeIndex())


# --- the swap -------------------------------------------------------------------------------


def test_a_swap_moves_the_selection_in_place(attach: Attach, tmp_path: Path) -> None:
    resolve, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 2)

    assert result["ok"] is True
    assert result["changed"] is True
    assert selected(resolve, 0) == 2
    assert selected(resolve, 1) == 1


def test_a_swap_moves_nothing_else_on_the_timeline(attach: Attach, tmp_path: Path) -> None:
    """In place means in place: the shot keeps its position and its length."""
    resolve, cut_file = a_built_cut(attach, tmp_path)

    swap_take(cut_file, "s002", 3)

    timeline = built(resolve, "sunset-set v1")
    assert [(item.GetStart(), item.GetDuration()) for item in shots(timeline)] == [
        (0, 100),
        (100, 80),
        (180, 60),
    ]


def test_the_report_names_the_take_it_took_and_the_one_it_left(
    attach: Attach, tmp_path: Path
) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 2)

    assert result["segment"] == "s001"
    assert result["selected"] == {
        "index": 2,
        "source": "keys_wide",
        "clip": "C0031.mp4",
        "in": 4500,
        "out": 4600,
    }
    assert result["previous"] == {
        "index": 1,
        "source": "gtr_close",
        "clip": "C0012.mp4",
        "in": 1000,
        "out": 1100,
    }
    assert result["duration"]["frames"] == 100
    assert result["timeline"]["name"] == "sunset-set v1"
    assert result["content_hash"]


def test_the_report_lists_the_whole_selector_so_the_indexes_are_never_guessed(
    attach: Attach, tmp_path: Path
) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s002", 2)

    assert [(take["index"], take["source"], take["in"]) for take in result["selector"]] == [
        (1, "keys_wide", 4000),
        (2, "gtr_close", 5000),
        (3, "gtr_close", 7000),
    ]


def test_the_report_hands_back_the_cut_file_edit_the_swap_needs(
    attach: Attach, tmp_path: Path
) -> None:
    """Promoted to main, old main demoted into its slot — selector order survives a rebuild."""
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s002", 3)

    assert result["sync"] == {
        "segment": "s002",
        "source": "gtr_close",
        "in": 7000,
        "out": 7080,
        "alternates": [
            {"source": "gtr_close", "in": 5000, "out": 5080},
            {"source": "keys_wide", "in": 4000, "out": 4080},
        ],
    }


def test_reverting_to_the_main_take_asks_for_no_cut_file_edit(
    attach: Attach, tmp_path: Path
) -> None:
    """The file already says main is the selection, so putting it back needs no edit."""
    resolve, cut_file = a_built_cut(attach, tmp_path)
    swap_take(cut_file, "s001", 2)

    result = swap_take(cut_file, "s001", 1)

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["sync"] is None
    assert selected(resolve, 0) == 1


def test_selecting_the_take_already_selected_changes_nothing(
    attach: Attach, tmp_path: Path
) -> None:
    resolve, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 1)

    assert result["ok"] is True
    assert result["changed"] is False
    assert selected(resolve, 0) == 1


def test_a_swap_never_touches_the_media_pool(attach: Attach, tmp_path: Path) -> None:
    """The takes are already on the timeline; re-locating clips would only invent failures."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    cut_file = a_cut(tmp_path, doc_with_alternates())
    build_timeline(cut_file)
    pool.calls.clear()

    swap_take(cut_file, "s001", 2)

    assert pool.calls == []


def test_a_named_timeline_is_swapped_rather_than_the_open_one(
    attach: Attach, tmp_path: Path
) -> None:
    resolve, cut_file = a_built_cut(attach, tmp_path)
    build_timeline(cut_file)  # v2 is now the open one

    result = swap_take(cut_file, "s001", 2, timeline="sunset-set v1")

    assert result["timeline"]["name"] == "sunset-set v1"
    assert selected(resolve, 0) == 2
    assert shots(built(resolve, "sunset-set v2"))[0].GetSelectedTakeIndex() == 1


# --- what it refuses ------------------------------------------------------------------------


def test_a_take_index_past_the_selector_is_refused_with_the_selector_listed(
    attach: Attach, tmp_path: Path
) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 3)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["requested"] == 3
    assert [take["index"] for take in result["error"]["detail"]["selector"]] == [1, 2]


def test_a_take_index_below_one_is_refused(attach: Attach, tmp_path: Path) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 0)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"


def test_a_segment_with_no_alternates_has_nothing_to_swap(attach: Attach, tmp_path: Path) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s003", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert [take["source"] for take in result["error"]["detail"]["selector"]] == ["gtr_close"]
    assert "no alternates" in result["error"]["cause"]


def test_take_one_on_a_segment_with_no_alternates_says_so_rather_than_crying_drift(
    attach: Attach, tmp_path: Path
) -> None:
    """The shot is a plain clip, not a selector sitting on take 1 — and nothing has drifted."""
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s003", 1)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "no alternates" in result["error"]["cause"]


def test_an_unknown_segment_is_refused_with_the_ids_that_exist(
    attach: Attach, tmp_path: Path
) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s404", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["available"] == ["s001", "s002", "s003"]


def test_an_invalid_cut_file_stops_before_the_timeline_is_read(
    attach: Attach, tmp_path: Path
) -> None:
    _, _ = a_built_cut(attach, tmp_path)
    broken = a_cut(tmp_path, {"schema": 1}, name="broken.cut.json")

    result = swap_take(broken, "s001", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "cut_invalid"
    assert result["error"]["detail"]["errors"]


def test_a_timeline_without_the_selector_the_cut_describes_is_refused(
    attach: Attach, tmp_path: Path
) -> None:
    """Drift: the open version was built from a cut with no alternates on this shot."""
    _, _ = a_built_cut(attach, tmp_path, doc=valid_doc())
    with_takes = a_cut(tmp_path, doc_with_alternates(), name="alternates.cut.json")

    result = swap_take(with_takes, "s001", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["takes"] == {"wanted": 2, "found": 0}


def test_a_timeline_the_cut_does_not_line_up_with_is_refused(
    attach: Attach, tmp_path: Path
) -> None:
    """No clip starts where the cut says the segment does — a different cut built this."""
    _, _ = a_built_cut(attach, tmp_path, doc=valid_doc())
    shifted = doc_with_alternates()
    shifted["segments"][0]["out"] = 1050
    shifted["segments"][0]["alternates"] = [{"source": "keys_wide", "in": 4500, "out": 4550}]

    result = swap_take(a_cut(tmp_path, shifted, name="shifted.cut.json"), "s002", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["record_frame"] == 50


def test_a_shot_of_the_wrong_length_at_the_right_frame_is_not_this_segment(
    attach: Attach, tmp_path: Path
) -> None:
    """Two cuts can agree on where a segment starts and disagree on what it is."""
    _, _ = a_built_cut(attach, tmp_path, doc=valid_doc())
    relengthed = doc_with_alternates()
    relengthed["segments"][1]["out"] = 4070
    relengthed["segments"][1]["alternates"] = [{"source": "gtr_close", "in": 5000, "out": 5070}]

    result = swap_take(a_cut(tmp_path, relengthed, name="relengthed.cut.json"), "s002", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["record_frame"] == 100
    assert result["error"]["detail"]["duration"] == 70


def test_an_unknown_timeline_name_is_refused(attach: Attach, tmp_path: Path) -> None:
    _, cut_file = a_built_cut(attach, tmp_path)

    result = swap_take(cut_file, "s001", 2, timeline="sunset-set v9")

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_not_found"


def test_a_refused_selection_is_a_structured_failure(attach: Attach, tmp_path: Path) -> None:
    resolve, cut_file = a_built_cut(attach, tmp_path)
    shots(built(resolve, "sunset-set v1"))[0].select_take_result = False

    result = swap_take(cut_file, "s001", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_operation_failed"
    assert result["error"]["detail"]["segment"] == "s001"


def test_a_selection_that_reports_success_and_does_not_move_is_a_failure(
    attach: Attach, tmp_path: Path
) -> None:
    """``SelectTakeByIndex`` answers Bool; the selection is read back, never believed."""
    resolve, cut_file = a_built_cut(attach, tmp_path)
    shots(built(resolve, "sunset-set v1"))[0].select_take_lands = False

    result = swap_take(cut_file, "s001", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_operation_failed"
    assert result["error"]["detail"]["selected"] == 1


def test_no_timeline_open_and_none_named_is_refused(attach: Attach, tmp_path: Path) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)
    cut_file = a_cut(tmp_path, doc_with_alternates())

    result = swap_take(cut_file, "s001", 2)

    assert result["ok"] is False
    assert result["error"]["code"] == "no_timeline_open"


def test_a_timeline_with_no_video_track_is_refused(attach: Attach, tmp_path: Path) -> None:
    empty = FakeTimeline("sunset-set v1", "59.94", video=[])
    attach(studio(timeline=None, timelines=[empty], pool=a_pool()))
    cut_file = a_cut(tmp_path, doc_with_alternates())

    result = swap_take(cut_file, "s001", 2, timeline="sunset-set v1")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
