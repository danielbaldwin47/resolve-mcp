"""build_timeline at the Resolve seam: what lands on the timeline, and what never starts.

Two things are under test here and nothing else matters as much:

* a valid cut becomes a *new* version with frame-exact placements, and
* everything that could half-build one — an invalid file, a locked track, a refused
  creation, an append that slid somewhere else — stops with a structured failure.

The fake models the append footguns the #18 spike confirmed on live Resolve (truthy
returns that place nothing, relocation on overlap, stills ignoring ``endFrame``), so a
wrapper that trusted Resolve's return value fails these tests rather than a real cut.
"""

from __future__ import annotations

from pathlib import Path

from resolve_mcp.cut.document import content_hash
from resolve_mcp.tools.cut import build_timeline

from .conftest import Attach
from .cutfile import (
    TOTAL_FRAMES,
    a_cut,
    a_pool,
    built,
    doc_with_alternates,
    empty_project,
    placements,
    selector,
    shots,
    valid_doc,
)
from .fakes import FakeMediaPoolItem, FakeTimeline, FakeTimelineItem, FakeTrack, studio

# --- the clean build ----------------------------------------------------------------------


def test_a_valid_cut_builds_the_first_version(attach: Attach, tmp_path: Path) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert result["timeline"]["name"] == "sunset-set v1"
    assert result["timeline"]["base_name"] == "sunset-set"
    assert result["timeline"]["version"] == 1


def test_segments_land_butt_joined_in_document_order(attach: Attach, tmp_path: Path) -> None:
    """Sequential V1: positions are computed, so gaps cannot happen and must not appear."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, valid_doc()))

    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 0, 100),
        ("C0031.mp4", 100, 80),
        ("C0012.mp4", 180, 60),
    ]


def test_each_segment_takes_the_source_frames_the_cut_named(attach: Attach, tmp_path: Path) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, valid_doc()))

    items = built(resolve, "sunset-set v1").GetItemListInTrack("video", 1) or []
    assert [item.GetSourceStartFrame() for item in items] == [1000, 4000, 2500]


def test_the_master_audio_is_one_continuous_clip_under_the_cut(
    attach: Attach, tmp_path: Path
) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, valid_doc()))

    assert placements(built(resolve, "sunset-set v1"), "audio") == [
        ("sunset-master.wav", 0, TOTAL_FRAMES)
    ]


def test_record_frames_are_absolute_from_the_timeline_start(
    attach: Attach, tmp_path: Path
) -> None:
    """A one-hour start timecode is the norm; a recordFrame of 0 would land before it (#18 d)."""
    pool = a_pool()
    pool.new_timeline_start = 3600
    resolve = empty_project(pool)
    attach(resolve)

    build_timeline(a_cut(tmp_path, valid_doc()))

    assert [start for _, start, _ in placements(built(resolve, "sunset-set v1"))] == [
        3600,
        3700,
        3780,
    ]


def test_every_append_names_its_media_type_and_track(attach: Attach, tmp_path: Path) -> None:
    """``trackIndex`` without ``mediaType`` returns True and drops the clip — never send one."""
    pool = a_pool()
    attach(empty_project(pool))

    build_timeline(a_cut(tmp_path, valid_doc()))

    assert pool.appends
    assert all("mediaType" in append and "trackIndex" in append for append in pool.appends)
    assert {append["mediaType"] for append in pool.appends} == {1, 2}


def test_a_cut_without_audio_builds_video_alone(attach: Attach, tmp_path: Path) -> None:
    """A rough cut has no master mix; that is a shape, not an error."""
    doc = valid_doc()
    del doc["audio"]
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert result["placed"] == {"segments": 3, "audio": False, "selectors": 0}
    assert placements(built(resolve, "sunset-set v1"), "audio") == []


def test_the_report_echoes_the_cut_hash_and_where_it_came_from(
    attach: Attach, tmp_path: Path
) -> None:
    """Provenance: the built timeline is traceable to the exact bytes that built it."""
    attach(empty_project(a_pool()))
    cut_file = a_cut(tmp_path, valid_doc())

    result = build_timeline(cut_file)

    assert result["content_hash"] == content_hash(Path(cut_file).read_bytes())
    assert result["cut_file"] == cut_file
    assert result["context"]["project"] == "sunset-set"
    assert result["context"]["timeline"] == "sunset-set v1"


def test_the_built_timeline_reports_its_own_span(attach: Attach, tmp_path: Path) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["timeline"]["duration"]["frames"] == TOTAL_FRAMES
    assert result["timeline"]["fps"] == 59.94
    assert result["placed"] == {"segments": 3, "audio": True, "selectors": 0}


def test_the_build_opens_the_timeline_it_made(attach: Attach, tmp_path: Path) -> None:
    """Resolve appends to the *current* timeline, so the build has to switch to it."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, valid_doc()))

    assert resolve.current_project.GetCurrentTimeline().GetName() == "sunset-set v1"


# --- versions -----------------------------------------------------------------------------


def test_a_rebuild_takes_the_next_version_and_leaves_the_old_ones_alone(
    attach: Attach, tmp_path: Path
) -> None:
    earlier = FakeTimeline(
        "sunset-set v3",
        "59.94",
        video=[FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 0, 500)])],
    )
    resolve = studio(
        timeline=None,
        timelines=[FakeTimeline("sunset-set v1", "59.94"), earlier],
        pool=a_pool(),
    )
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["timeline"]["name"] == "sunset-set v4"
    assert placements(earlier) == [("C0012.mp4", 0, 500)]


def test_a_deleted_version_does_not_hand_its_number_out_twice(
    attach: Attach, tmp_path: Path
) -> None:
    """Max, not count: v2 deleted must still leave v4 as the next name after v3."""
    resolve = studio(
        timeline=None,
        timelines=[FakeTimeline("sunset-set v1", "59.94"), FakeTimeline("sunset-set v3", "59.94")],
        pool=a_pool(),
    )
    attach(resolve)

    assert build_timeline(a_cut(tmp_path, valid_doc()))["timeline"]["name"] == "sunset-set v4"


def test_another_cuts_versions_do_not_move_this_ones_number(
    attach: Attach, tmp_path: Path
) -> None:
    resolve = studio(
        timeline=None,
        timelines=[FakeTimeline("holiday-gig v7", "59.94"), FakeTimeline("sunset-set", "59.94")],
        pool=a_pool(),
    )
    attach(resolve)

    assert build_timeline(a_cut(tmp_path, valid_doc()))["timeline"]["name"] == "sunset-set v1"


# --- pre-flight: nothing starts on a bad file ---------------------------------------------


def test_an_invalid_cut_aborts_before_resolve_is_touched(attach: Attach, tmp_path: Path) -> None:
    doc = valid_doc()
    doc["segments"][1]["out"] = doc["segments"][1]["in"]
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is False
    assert result["error"]["code"] == "cut_invalid"
    assert [finding["rule"] for finding in result["error"]["detail"]["errors"]] == ["E3"]
    assert resolve.current_project.GetTimelineCount() == 0
    assert "CreateEmptyTimeline" not in pool.calls


def test_the_abort_reports_every_error_at_once(attach: Attach, tmp_path: Path) -> None:
    doc = valid_doc()
    doc["segments"][0]["out"] = doc["segments"][0]["in"]
    doc["segments"][1]["source"] = "no_such_alias"
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, doc))

    assert {finding["rule"] for finding in result["error"]["detail"]["errors"]} == {"E3", "E4"}
    assert result["error"]["detail"]["content_hash"]


def test_a_file_that_is_not_json_aborts_as_a_validation_finding(
    attach: Attach, tmp_path: Path
) -> None:
    attach(empty_project(a_pool()))
    path = tmp_path / "broken.cut.json"
    path.write_text("{not json", encoding="utf-8")

    result = build_timeline(str(path))

    assert result["ok"] is False
    assert result["error"]["detail"]["errors"][0]["rule"] == "E1"


def test_warnings_are_reported_and_do_not_block(attach: Attach, tmp_path: Path) -> None:
    """W1 is a creative call, not a rule; the flash frame builds and is named."""
    doc = valid_doc()
    doc["segments"][2]["out"] = doc["segments"][2]["in"] + 4
    doc["audio"]["out"] = 184
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert [finding["rule"] for finding in result["warnings"]] == ["W1"]


def test_overlays_are_refused_rather_than_silently_dropped(
    attach: Attach, tmp_path: Path
) -> None:
    """Anchored overlays are a later tool; building the V1 alone would be a half-built cut."""
    doc = valid_doc()
    doc["overlays"] = [
        {
            "id": "b01",
            "source": "keys_wide",
            "in": 4000,
            "out": 4020,
            "over": {"segment": "s001", "offset": 10},
        }
    ]
    pool = a_pool()
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_cut_feature"
    assert "CreateEmptyTimeline" not in pool.calls


def test_no_project_open_fails_before_anything_else(attach: Attach, tmp_path: Path) -> None:
    attach(studio(project=None))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"


# --- the footguns -------------------------------------------------------------------------


def test_a_locked_target_track_is_reported_instead_of_silently_dropping_the_cut(
    attach: Attach, tmp_path: Path
) -> None:
    """Resolve returns items for an append onto a locked track and places nothing (#18 d)."""
    pool = a_pool()
    pool.new_timeline_locked = True
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    findings = result["error"]["detail"]["errors"]
    assert [finding["rule"] for finding in findings] == ["E11", "E11"]
    assert [finding["id"] for finding in findings] == ["V1", "A1"]
    assert "AppendToTimeline" not in pool.calls


def test_a_still_gets_its_out_written_once_so_endframe_is_honoured(
    attach: Attach, tmp_path: Path
) -> None:
    """Without the one-time Out write every still lands at the default duration (#18 a)."""
    still = FakeMediaPoolItem(
        "backdrop.png",
        file_path="D:/media/backdrop.png",
        properties={"Type": "Still", "FPS": "", "Frames": "1", "Start": "0", "End": "0"},
    )
    doc = valid_doc()
    doc["sources"]["keys_wide"] = {"clip": "backdrop.png", "bin": "Angles"}
    resolve = empty_project(a_pool(keys_wide=still))
    attach(resolve)

    build_timeline(a_cut(tmp_path, doc))

    assert ("Out", "0") in still.property_writes
    assert placements(built(resolve, "sunset-set v1"))[1] == ("backdrop.png", 100, 80)


def test_an_append_that_landed_somewhere_else_fails_the_build(
    attach: Attach, tmp_path: Path
) -> None:
    """No overwrite on overlap: a blocked clip slides, so placement is verified, not trusted."""
    pool = a_pool()
    pool.new_timeline_items = [FakeTimelineItem("stray.mp4", 0, 50)]
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert "sunset-set v1" in result["error"]["detail"]["timeline"]
    assert result["error"]["detail"]["misplaced"]


def test_a_refused_timeline_creation_is_a_structured_failure(
    attach: Attach, tmp_path: Path
) -> None:
    pool = a_pool()
    pool.create_timeline_result = False
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert "sunset-set v1" in result["error"]["cause"]


def test_a_refused_track_add_stops_rather_than_appending_into_nothing(
    attach: Attach, tmp_path: Path
) -> None:
    """An append onto a track that is not there is dropped silently, so this must not loop."""
    pool = a_pool()
    pool.new_timeline_tracks = (0, 0)
    pool.add_track_result = False
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert "AppendToTimeline" not in pool.calls


def test_resolve_quitting_mid_build_is_a_structured_failure(
    attach: Attach, tmp_path: Path
) -> None:
    """A build cannot be resumed, so a death mid-write must not be smoothed into success.

    The version it was making is scrap and the agent has to be told so — as a cause and a
    fix like every other failure, never as a traceback across the tool boundary.
    """
    versions: list[FakeTimeline | None] = [FakeTimeline("sunset-set v1", "59.94")]
    resolve = studio(timeline=None, timelines=versions, pool=a_pool())
    attach(resolve, None)
    resolve.die_after(14)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["cause"]
    assert result["error"]["fix"]
    assert result["error"]["code"] in {"resolve_unavailable", "build_failed", "internal_error"}


# --- alternates as take selectors -----------------------------------------------------------


def test_a_segment_with_alternates_becomes_a_take_selector(
    attach: Attach, tmp_path: Path
) -> None:
    """Selector = [main, alternates in order] — the order swap_take's indexes count in."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, doc_with_alternates()))

    first, second, third = shots(built(resolve, "sunset-set v1"))
    assert selector(first) == [("C0012.mp4", 1000, 1100), ("C0031.mp4", 4500, 4600)]
    assert selector(second) == [
        ("C0031.mp4", 4000, 4080),
        ("C0012.mp4", 5000, 5080),
        ("C0012.mp4", 7000, 7080),
    ]
    assert third.GetTakesCount() == 0


def test_the_main_clip_is_the_selection(attach: Attach, tmp_path: Path) -> None:
    """What is on the track has to be what the cut file says, or the build is a lie."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, doc_with_alternates()))

    first, second, _ = shots(built(resolve, "sunset-set v1"))
    assert first.GetSelectedTakeIndex() == 1
    assert second.GetSelectedTakeIndex() == 1
    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 0, 100),
        ("C0031.mp4", 100, 80),
        ("C0012.mp4", 180, 60),
    ]


def test_the_report_counts_the_selectors_it_made(attach: Attach, tmp_path: Path) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, doc_with_alternates()))

    assert result["ok"] is True
    assert result["placed"] == {"segments": 3, "audio": True, "selectors": 2}


def test_a_cut_without_alternates_makes_no_selectors(attach: Attach, tmp_path: Path) -> None:
    """An ordinary shot stays an ordinary clip: GetTakesCount is 0, not 1."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["placed"]["selectors"] == 0
    assert [item.GetTakesCount() for item in shots(built(resolve, "sunset-set v1"))] == [0, 0, 0]


def test_a_refused_take_fails_the_build_and_names_the_segment(
    attach: Attach, tmp_path: Path
) -> None:
    """A shot that lost its alternates is a timeline that no longer matches its cut file."""
    pool = a_pool()
    pool.take_quirks = {"add_take_result": False}
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc_with_alternates()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert result["error"]["detail"]["segment"] == "s001"
    assert result["error"]["detail"]["timeline"] == "sunset-set v1"


def test_a_take_that_reports_success_and_lands_nowhere_fails_the_build(
    attach: Attach, tmp_path: Path
) -> None:
    """``AddTake`` answers Bool, so the selector is read back rather than believed."""
    pool = a_pool()
    pool.take_quirks = {"takes_land": False}
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc_with_alternates()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert result["error"]["detail"]["segment"] == "s001"
    assert result["error"]["detail"]["takes"] == {"wanted": 2, "found": 0}


def test_a_selection_that_does_not_land_fails_the_build(attach: Attach, tmp_path: Path) -> None:
    """Main *is* the selection; a selector sitting on an alternate is the wrong angle."""
    pool = a_pool()
    pool.take_quirks = {"select_take_lands": False}
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc_with_alternates()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert result["error"]["detail"]["segment"] == "s001"


def test_a_refused_selection_fails_the_build(attach: Attach, tmp_path: Path) -> None:
    pool = a_pool()
    pool.take_quirks = {"select_take_result": False}
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc_with_alternates()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"


def test_unequal_alternates_never_reach_resolve(attach: Attach, tmp_path: Path) -> None:
    """E8 is what makes an in-place swap possible at all, so it aborts pre-flight."""
    doc = doc_with_alternates()
    doc["segments"][0]["alternates"] = [{"source": "keys_wide", "in": 4500, "out": 4599}]
    pool = a_pool()
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is False
    assert result["error"]["code"] == "cut_invalid"
    errors = result["error"]["detail"]["errors"]
    assert [finding["rule"] for finding in errors] == ["E8"]
    assert errors[0]["id"] == "s001"
    assert errors[0]["fix_hint"]
    assert "CreateEmptyTimeline" not in pool.calls


def test_a_missing_video_track_is_created_before_the_append(
    attach: Attach, tmp_path: Path
) -> None:
    """A track index past the next free one is accepted and dropped, so tracks are pre-made."""
    pool = a_pool()
    pool.new_timeline_tracks = (0, 0)
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert len(placements(built(resolve, "sunset-set v1"))) == 3
    assert placements(built(resolve, "sunset-set v1"), "audio")
