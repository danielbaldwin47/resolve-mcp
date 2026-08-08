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
from typing import Any

from resolve_mcp.document import content_hash
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
    assert result["placed"] == {"segments": 3, "overlays": 0, "audio": False, "selectors": 0}
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
    assert result["placed"] == {"segments": 3, "overlays": 0, "audio": True, "selectors": 0}


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


# --- markers across a rebuild (#130) ------------------------------------------------------
#
# The markers on a reviewed version are hand-placed — blue ones name the songs a titles file
# anchors to — and a rebuild makes an empty timeline. Carrying them is the one thing that
# stops a rebuild costing a human another pass of re-marking every song boundary.
#
# What makes it correct is *not* copying the frame numbers. Record frames are exactly what a
# rebuild moves. The master mix underneath does not move, so a marker is carried by the frame
# of the mix it sat over, and these tests pin that: the same musical moment, wherever the
# picture above it went.


MIX = "sunset-master.wav"


def _mix(record: int = 0, source: int = 0, stamp: str = "0", name: str = MIX) -> FakeTimelineItem:
    """The master mix as it sits under an earlier version — the axis the carry reads."""
    return FakeTimelineItem(
        name,
        record,
        240,
        source_start=source,
        media_item=FakeMediaPoolItem(name, properties={"Start": stamp}),
    )


def _earlier(
    markers: dict[Any, dict[str, Any]] | None = None,
    mix: list[FakeTimelineItem] | None = None,
    name: str = "sunset-set v3",
    end_frame: int | None = None,
) -> FakeTimeline:
    """``sunset-set v3``: the reviewed version a rebuild supersedes, marked up by hand."""
    return FakeTimeline(
        name,
        "59.94",
        video=[FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 0, 240)])],
        audio=[FakeTrack("Master", [_mix()] if mix is None else mix)],
        markers=markers,
        end_frame=end_frame,
    )


def _a_marker(name: str, color: str = "Blue", note: str = "", custom: str = "") -> dict[str, Any]:
    return {"color": color, "name": name, "note": note, "duration": 1, "customData": custom}


def _carried(timeline: FakeTimeline) -> list[tuple[float, str, str]]:
    """What landed on the new version: where, what colour, and which song it names."""
    return sorted(
        (frame, marker["color"], marker["name"]) for frame, marker in timeline.GetMarkers().items()
    )


def _rebuild(resolve: Any, tmp_path: Path, attach: Attach, **kwargs: Any) -> dict[str, Any]:
    attach(resolve)
    return build_timeline(a_cut(tmp_path, valid_doc()), **kwargs)


def test_a_rebuild_carries_the_hand_placed_markers_onto_the_new_version(
    attach: Attach, tmp_path: Path
) -> None:
    """The whole point: the song anchors survive, so a titles file re-applies untouched."""
    earlier = _earlier({100: _a_marker("sunset boulevard"), 180: _a_marker("night ferry")})
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 2
    assert result["markers"]["from"] == "sunset-set v3"
    assert result["markers"]["reason"] is None
    assert _carried(built(resolve, "sunset-set v4")) == [
        (100.0, "Blue", "sunset boulevard"),
        (180.0, "Blue", "night ferry"),
    ]


def test_a_carried_marker_keeps_the_colour_note_and_custom_data_it_had(
    attach: Attach, tmp_path: Path
) -> None:
    """A director's note is only worth carrying with the words on it (#42: the join is data)."""
    note = _a_marker("tighten this", "Red", "comes in late", "songs.json:sunset")
    earlier = _earlier({120: note})
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    _rebuild(resolve, tmp_path, attach)

    assert built(resolve, "sunset-set v4").GetMarkers() == {
        120.0: {
            "color": "Red",
            "name": "tighten this",
            "note": "comes in late",
            "duration": 1,
            "customData": "songs.json:sunset",
        }
    }


def test_a_marker_lands_on_the_mix_frame_it_sat_over_not_the_record_frame_it_had(
    attach: Attach, tmp_path: Path
) -> None:
    """v3's mix started 40 frames in; this cut's starts at 0, so every marker moves 40 later.

    This is the test the whole feature turns on. A copy would leave the marker at 60 and put
    the song anchor 40 frames before the song.
    """
    earlier = _earlier({60: _a_marker("sunset boulevard")}, mix=[_mix(source=40)])
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["shift"] == 40
    assert _carried(built(resolve, "sunset-set v4")) == [(100.0, "Blue", "sunset boulevard")]


def test_a_start_stamp_on_the_mix_does_not_shift_the_carry(
    attach: Attach, tmp_path: Path
) -> None:
    """A WAV stamped 01:00:00:00 reports source frames an hour in; forgetting to subtract
    that stamp would shift every carried marker by an hour and still look like a reading."""
    earlier = _earlier(
        {60: _a_marker("sunset boulevard")},
        mix=[_mix(source=216040, stamp="216000")],
    )
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["shift"] == 40
    assert _carried(built(resolve, "sunset-set v4")) == [(100.0, "Blue", "sunset boulevard")]


def test_the_earlier_version_keeps_the_markers_it_was_reviewed_with(
    attach: Attach, tmp_path: Path
) -> None:
    """A carry is a copy forward, never a move: v3 is what a reviewer already signed off."""
    earlier = _earlier({100: _a_marker("sunset boulevard")})
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    _rebuild(resolve, tmp_path, attach)

    assert _carried(earlier) == [(100.0, "Blue", "sunset boulevard")]


def test_markers_come_from_the_version_being_superseded_not_the_oldest_one(
    attach: Attach, tmp_path: Path
) -> None:
    earlier = _earlier({100: _a_marker("sunset boulevard")})
    stale = _earlier({40: _a_marker("an abandoned pass")}, name="sunset-set v1")
    resolve = studio(timeline=None, timelines=[stale, earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["from"] == "sunset-set v3"
    assert _carried(built(resolve, "sunset-set v4")) == [(100.0, "Blue", "sunset boulevard")]


def test_the_first_version_of_a_cut_has_nothing_to_carry(attach: Attach, tmp_path: Path) -> None:
    resolve = empty_project(a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"] == {
        "carried": 0,
        "skipped": 0,
        "from": None,
        "shift": None,
        "refused": [],
        "reason": "Nothing to carry: this is the first version of this cut.",
    }


def test_carry_markers_off_leaves_the_earlier_versions_markers_where_they_are(
    attach: Attach, tmp_path: Path
) -> None:
    earlier = _earlier({100: _a_marker("sunset boulevard")})
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach, carry_markers=False)

    assert result["markers"]["carried"] == 0
    assert "carry_markers was off" in result["markers"]["reason"]
    assert built(resolve, "sunset-set v4").GetMarkers() == {}


def test_an_earlier_version_that_was_never_marked_up_says_so(
    attach: Attach, tmp_path: Path
) -> None:
    resolve = studio(timeline=None, timelines=[_earlier()], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 0
    assert result["markers"]["reason"] == "sunset-set v3 carries no markers."


def test_a_cut_with_no_master_mix_refuses_to_guess_where_the_markers_go(
    attach: Attach, tmp_path: Path
) -> None:
    """A rough cut has no continuous mix, so the two versions share no axis. Markers that
    merely look plausible are worse than markers the agent knows it has to place."""
    doc = valid_doc()
    del doc["audio"]
    earlier = _earlier({100: _a_marker("sunset boulevard")})
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["markers"]["carried"] == 0
    assert result["markers"]["from"] == "sunset-set v3"
    assert "no single master mix" in result["markers"]["reason"]
    assert "placed by hand" in result["markers"]["reason"]
    assert built(resolve, "sunset-set v4").GetMarkers() == {}


def test_a_multi_channel_mix_on_the_earlier_version_is_one_anchor_not_eight(
    attach: Attach, tmp_path: Path
) -> None:
    """Live, an 8-channel MXF appends as one item per channel across A1-A8 — one placement
    said eight times. Counting items instead of reading them refuses a real concert mix."""
    earlier = _earlier({60: _a_marker("sunset boulevard")}, mix=[_mix(source=40)] * 8)
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 1
    assert result["markers"]["shift"] == 40
    assert _carried(built(resolve, "sunset-set v4")) == [(100.0, "Blue", "sunset boulevard")]


def test_an_earlier_version_that_lays_the_mix_at_two_offsets_is_not_an_anchor(
    attach: Attach, tmp_path: Path
) -> None:
    """Which of the two says where the mix sits? Nothing answers that, so nothing guesses."""
    earlier = _earlier({100: _a_marker("sunset boulevard")}, mix=[_mix(), _mix(record=300)])
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 0
    assert "does not agree where" in result["markers"]["reason"]


def test_an_earlier_version_over_a_different_mix_is_not_an_anchor(
    attach: Attach, tmp_path: Path
) -> None:
    """A hand-edited version over camera scratch shares no clock with this cut's mix."""
    earlier = _earlier({100: _a_marker("sunset boulevard")}, mix=[_mix(name="C0012.mp4")])
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 0
    assert built(resolve, "sunset-set v4").GetMarkers() == {}


def test_a_marker_this_version_has_no_room_for_is_named_not_silently_dropped(
    attach: Attach, tmp_path: Path
) -> None:
    """The tightening cut the marker sat in is gone. Which song lost its anchor is the
    actionable half — a count alone would send someone diffing two timelines to find out."""
    earlier = _earlier(
        {100: _a_marker("sunset boulevard"), 400: _a_marker("night ferry")},
        end_frame=500,
    )
    resolve = studio(timeline=None, timelines=[earlier], pool=a_pool())

    result = _rebuild(resolve, tmp_path, attach)

    assert result["markers"]["carried"] == 1
    assert result["markers"]["skipped"] == 1
    refused = result["markers"]["refused"]
    assert [entry["name"] for entry in refused] == ["night ferry"]
    assert refused[0]["record"] == 400
    assert refused[0]["error"] is not None
    assert _carried(built(resolve, "sunset-set v4")) == [(100.0, "Blue", "sunset boulevard")]


def test_a_version_deleted_mid_build_does_not_sink_a_build_that_landed(
    attach: Attach, tmp_path: Path
) -> None:
    """The name came off this project moments earlier, so this is a race, not a mistake —
    and every clip is already on the timeline by the time the markers are read."""
    earlier = _earlier({100: _a_marker("sunset boulevard")})
    pool = a_pool()
    resolve = studio(timeline=None, timelines=[earlier], pool=pool)
    attach(resolve)
    create = pool.CreateEmptyTimeline

    project = resolve.current_project
    assert project is not None

    def create_and_lose_the_earlier_version(name: str) -> Any:
        made = create(name)
        project.remove_timeline(earlier)
        return made

    pool.CreateEmptyTimeline = create_and_lose_the_earlier_version

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert result["markers"]["carried"] == 0
    assert "gone by the time" in result["markers"]["reason"]


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


def test_no_project_open_fails_before_anything_else(attach: Attach, tmp_path: Path) -> None:
    attach(studio(project=None))

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"


# --- anchored overlays --------------------------------------------------------------------


def with_overlay(out: int = 3030) -> dict[str, Any]:
    """:func:`valid_doc` plus one b-roll overlay anchored 10 frames into ``s002``.

    ``s002`` starts at frame 100 of the V1, so the overlay belongs at 110 — a number
    nothing in the cut file states, which is the point of anchoring. ``out`` lengthens it
    past the anchor's end, which is what covering a seam looks like.
    """
    return valid_doc(
        overlays=[
            {
                "id": "b01",
                "source": "gtr_close",
                "in": 3000,
                "out": out,
                "over": {"segment": "s002", "offset": 10},
            }
        ]
    )


def test_an_overlay_lands_on_v2_at_its_anchor_plus_offset(
    attach: Attach, tmp_path: Path
) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_overlay()))

    assert result["ok"] is True
    assert placements(built(resolve, "sunset-set v1"), "video", 2) == [("C0012.mp4", 110, 30)]


def test_an_overlay_takes_the_source_frames_the_cut_named(
    attach: Attach, tmp_path: Path
) -> None:
    """``mediaType`` travels with ``trackIndex``, or Resolve drops the clip (#18 (d))."""
    pool = a_pool()
    attach(empty_project(pool))

    build_timeline(a_cut(tmp_path, with_overlay()))

    overlay = next(append for append in pool.appends if append["trackIndex"] == 2)
    assert (overlay["startFrame"], overlay["endFrame"]) == (3000, 3030)
    assert (overlay["mediaType"], overlay["recordFrame"]) == (1, 110)


def test_the_v1_under_an_overlay_is_laid_out_as_if_it_were_not_there(
    attach: Attach, tmp_path: Path
) -> None:
    """Overlays ride above the cut; they never displace the segments they cover."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, with_overlay()))

    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 0, 100),
        ("C0031.mp4", 100, 80),
        ("C0012.mp4", 180, 60),
    ]


def test_tightening_an_earlier_segment_keeps_the_overlay_over_the_same_content(
    attach: Attach, tmp_path: Path
) -> None:
    """The reason anchors exist: a re-time upstream moves the overlay with its anchor.

    ``s001`` loses 40 frames, so everything after it slides 40 earlier — and the overlay
    stays exactly 10 frames into ``s002``, covering the same frames of the same clip.
    """
    resolve = empty_project(a_pool())
    attach(resolve)
    build_timeline(a_cut(tmp_path, with_overlay()))

    tightened = with_overlay()
    tightened["segments"][0]["out"] = 1060
    tightened["audio"]["out"] = 200
    build_timeline(a_cut(tmp_path, tightened))

    before, after = (built(resolve, name) for name in ("sunset-set v1", "sunset-set v2"))
    assert placements(after, "video", 2) == [("C0012.mp4", 70, 30)]
    # Same clip, same source frames, same 10-frame offset into the same anchor segment.
    anchor_before, anchor_after = placements(before)[1], placements(after)[1]
    assert placements(before, "video", 2)[0][1] - anchor_before[1] == 10
    assert placements(after, "video", 2)[0][1] - anchor_after[1] == 10


def test_an_overlay_may_run_past_its_anchor_to_cover_a_seam(
    attach: Attach, tmp_path: Path
) -> None:
    """What b-roll is usually for: the overlay outlives its anchor and covers the cut."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_overlay(out=3130)))

    assert result["ok"] is True
    # 110 to 240: over the last 70 frames of s002, the s002/s003 seam, and all of s003.
    assert placements(built(resolve, "sunset-set v1"), "video", 2) == [("C0012.mp4", 110, 130)]
    assert placements(built(resolve, "sunset-set v1"))[2] == ("C0012.mp4", 180, 60)


def test_the_report_counts_the_overlays_apart_from_the_segments(
    attach: Attach, tmp_path: Path
) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, with_overlay()))

    assert result["placed"] == {"segments": 3, "overlays": 1, "audio": True, "selectors": 0}


def test_a_cut_without_overlays_builds_no_second_video_track(
    attach: Attach, tmp_path: Path
) -> None:
    """V2 is the overlays' track; a cut that has none must not grow one."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["placed"] == {"segments": 3, "overlays": 0, "audio": True, "selectors": 0}
    assert built(resolve, "sunset-set v1").GetTrackCount("video") == 1


def test_the_overlay_track_is_created_when_the_new_timeline_has_none(
    attach: Attach, tmp_path: Path
) -> None:
    """An append onto a track index past the next free one is dropped silently (#18 (d))."""
    pool = a_pool()
    pool.new_timeline_tracks = (0, 0)
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_overlay()))

    assert result["ok"] is True
    assert len(placements(built(resolve, "sunset-set v1"))) == 3
    assert placements(built(resolve, "sunset-set v1"), "video", 2) == [("C0012.mp4", 110, 30)]


def test_a_locked_overlay_track_is_reported_before_anything_is_appended(
    attach: Attach, tmp_path: Path
) -> None:
    """V2 is as lockable as V1, and a locked track swallows the append reporting success."""
    pool = a_pool()
    pool.new_timeline_tracks = (2, 1)
    pool.new_timeline_locked = True
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, with_overlay()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert [f["id"] for f in result["error"]["detail"]["errors"]] == ["V1", "V2", "A1"]
    assert "AppendToTimeline" not in pool.calls


def test_an_overlay_that_never_landed_fails_the_build(attach: Attach, tmp_path: Path) -> None:
    """V2 is read back like every other track: the append's word is not evidence."""
    pool = a_pool()
    pool.appends_land_nowhere = True
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, with_overlay()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    reported = result["error"]["detail"]["misplaced"]
    misplaced = {finding["id"]: finding["track"] for finding in reported}
    assert misplaced["b01"] == "V2"
    assert misplaced["s001"] == "V1"


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
    assert result["placed"] == {"segments": 3, "overlays": 0, "audio": True, "selectors": 2}


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
