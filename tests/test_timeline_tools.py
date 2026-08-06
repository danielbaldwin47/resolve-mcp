"""The timeline read tools, called in-process against the fake Resolve seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.resolve import timeline as wrapper
from resolve_mcp.tools.timeline import inspect_timeline, list_timelines

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
    FakeTrack,
    studio,
    sync_reference,
)


def a_cut(name: str = "sunset-set v3", fps: str = "59.94") -> FakeTimeline:
    """A built cut: one video track of shots over one continuous audio track."""
    return FakeTimeline(
        name,
        fps,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [
                    FakeTimelineItem("C0012.mp4", 100, 60, source_start=1000),
                    FakeTimelineItem("C0031.mp4", 160, 90, source_start=4200),
                    FakeTimelineItem("C0012.mp4", 250, 50, source_start=1400),
                ],
            )
        ],
        audio=[FakeTrack("Master", [FakeTimelineItem("master_mix.wav", 100, 200)])],
    )


# --- list --------------------------------------------------------------------------------


def test_list_reports_versions_durations_and_which_one_is_open(attach: Attach) -> None:
    cut = a_cut()
    reference = sync_reference("sunset-set sync")
    attach(studio(timelines=[reference, cut], timeline=cut))

    result = list_timelines()

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["current"] == "sunset-set v3"
    listed = {entry["name"]: entry for entry in result["timelines"]}
    assert listed["sunset-set v3"]["version"] == 3
    assert listed["sunset-set v3"]["base_name"] == "sunset-set"
    assert listed["sunset-set v3"]["current"] is True
    assert listed["sunset-set v3"]["duration"]["frames"] == 200
    assert listed["sunset-set sync"]["version"] is None
    assert listed["sunset-set sync"]["current"] is False


def test_list_shows_the_track_stack_that_marks_the_sync_reference(attach: Attach) -> None:
    """The reference is the timeline with an angle per video track — visible from the list."""
    attach(studio(timelines=[sync_reference(angles={f"Cam {n}": (0, 0, 10) for n in "ABCD"})]))

    result = list_timelines()

    assert result["timelines"][0]["tracks"] == {"video": 4, "audio": 0, "subtitle": 0}


def test_list_names_the_newest_version_of_each_cut(attach: Attach) -> None:
    attach(
        studio(
            timelines=[
                a_cut("sunset-set v1"),
                a_cut("sunset-set v12"),
                a_cut("sunset-set v3"),
                a_cut("encore_v2"),
                sync_reference("sunset-set sync"),
            ]
        )
    )

    result = list_timelines()

    assert result["latest_versions"] == {"sunset-set": "sunset-set v12", "encore": "encore_v2"}


def test_list_spills_past_the_cap_rather_than_flooding_the_reply(attach: Attach) -> None:
    attach(studio(timelines=[a_cut(f"sunset-set v{index}") for index in range(1, 6)]))

    result = list_timelines(limit=2)

    assert result["count"] == 5
    assert result["truncated"] is True
    assert len(result["timelines"]) == 2
    spilled = Path(result["spilled_to"])
    assert len(json.loads(spilled.read_text(encoding="utf-8"))["timelines"]) == 5


def test_list_of_a_project_with_no_timelines_is_empty_not_an_error(attach: Attach) -> None:
    attach(studio(timeline=None, timelines=[]))

    result = list_timelines()

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["current"] is None


def test_timeline_tools_need_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = list_timelines()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"
    assert result["error"]["fix"]


# --- inspect -----------------------------------------------------------------------------


def test_inspect_defaults_to_the_open_timeline(attach: Attach) -> None:
    cut = a_cut()
    attach(studio(timelines=[sync_reference(), cut], timeline=cut))

    result = inspect_timeline()

    assert result["ok"] is True
    assert result["timeline"]["name"] == "sunset-set v3"
    assert result["timeline"]["current"] is True


def test_inspect_names_the_timelines_that_exist_when_the_name_is_wrong(attach: Attach) -> None:
    attach(studio(timelines=[a_cut("sunset-set v3")]))

    result = inspect_timeline(timeline="sunset-set V3")

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_not_found"
    assert "sunset-set v3" in result["error"]["fix"]


def test_inspect_with_nothing_open_says_so(attach: Attach) -> None:
    attach(studio(timeline=None, timelines=[a_cut()]))

    result = inspect_timeline()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_timeline_open"
    assert result["error"]["fix"]


def test_summary_answers_how_long_and_how_many_tracks_without_listing_them(
    attach: Attach,
) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="summary")

    assert result["timeline"]["tracks"] == {"video": 1, "audio": 1, "subtitle": 0}
    assert result["timeline"]["duration"]["frames"] == 200
    assert result["tracks"] is None
    assert result["item_count"] == 4


def test_track_detail_describes_the_stack_without_the_clips(attach: Attach) -> None:
    attach(
        studio(
            timeline=FakeTimeline(
                "sunset-set sync",
                video=[FakeTrack("Cam A", [FakeTimelineItem("A.mp4", 0, 10)], locked=True)],
            )
        )
    )

    result = inspect_timeline(detail="tracks")

    assert result["tracks"] == [
        {
            "type": "video",
            "index": 1,
            "name": "Cam A",
            "enabled": True,
            "locked": True,
            "item_count": 1,
        }
    ]


def test_clip_detail_lists_the_shots_in_dual_time(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="clips")

    first = result["tracks"][0]["items"][0]
    assert first["name"] == "C0012.mp4"
    assert first["record"]["in"] == {
        "frames": 100,
        "seconds": 1.668,
        "timecode": "00:00:01:40",
        "fps": 59.94,
    }
    assert first["record"]["out"]["frames"] == 160
    assert first["record"]["duration"]["frames"] == 60
    assert first["source"]["in"]["frames"] == 1000
    assert first["source"]["out"]["frames"] == 1060


def test_an_unknown_detail_level_names_the_ones_that_exist(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="everything")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "clips" in result["error"]["fix"]


# --- the stacked sync reference ----------------------------------------------------------


def test_each_angle_carries_the_offset_that_maps_its_source_onto_the_timeline(
    attach: Attach,
) -> None:
    """timeline_frame = source_frame + sync_offset, which is what a common clock needs."""
    attach(
        studio(
            timeline=sync_reference(
                angles={"Cam A": (0, 1000, 500), "Cam B": (120, 3000, 400)},
            )
        )
    )

    result = inspect_timeline(detail="clips")

    offsets = {track["name"]: track["items"][0]["sync_offset"] for track in result["tracks"]}
    assert offsets["Cam A"]["frames"] == -1000
    assert offsets["Cam B"]["frames"] == -2880
    assert offsets["Cam A"]["timecode"] == "-00:00:16:40"
    assert offsets["Cam A"]["seconds"] == -16.683


def test_the_source_start_is_read_before_the_left_offset_that_stands_in_for_it(
    attach: Attach,
) -> None:
    item = FakeTimelineItem("A.mp4", 120, 400, source_start=3000, left_offset=7)
    attach(studio(timeline=FakeTimeline("sync", video=[FakeTrack("Cam A", [item])])))

    result = inspect_timeline(detail="clips")

    assert result["tracks"][0]["items"][0]["source"]["in"]["frames"] == 3000


def test_an_angle_on_a_resolve_without_source_frames_still_reports_its_offset(
    attach: Attach,
) -> None:
    """Pre-18.5 has no GetSourceStartFrame; the left offset is the closest thing it has."""
    item = FakeTimelineItem(
        "A.mp4", 120, 400, source_start=3000, left_offset=3000, supports_source_frames=False
    )
    attach(studio(timeline=FakeTimeline("sync", video=[FakeTrack("Cam A", [item])])))

    result = inspect_timeline(detail="clips")

    assert result["tracks"][0]["items"][0]["sync_offset"]["frames"] == -2880


def test_one_title_that_refuses_a_getter_costs_a_field_not_the_inspection(
    attach: Attach,
) -> None:
    """A Text+ generator answers some getters and refuses others; the cut still reads."""
    title = FakeTimelineItem(
        "Title 01",
        0,
        60,
        refuses={"GetSourceStartFrame", "GetMediaPoolItem", "GetTakesCount"},
    )
    shot = FakeTimelineItem("C0012.mp4", 60, 40, source_start=1000)
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [title, shot])])))

    result = inspect_timeline(detail="clips")

    assert result["ok"] is True
    titled, filmed = result["tracks"][0]["items"]
    assert titled["name"] == "Title 01"
    assert titled["clip"] is None
    assert titled["takes"] == 0
    assert titled["record"]["duration"]["frames"] == 60
    assert filmed["source"]["in"]["frames"] == 1000


def test_a_source_span_is_never_half_one_clock_and_half_another(attach: Attach) -> None:
    """An in point counted from the media start against an absolute out point is a lie."""
    item = FakeTimelineItem(
        "A.mp4",
        0,
        100,
        source_start=5,
        left_offset=5,
        source_end=86_499,
        supports_source_frames=False,
    )
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    source = result["tracks"][0]["items"][0]["source"]
    assert source["in"]["frames"] == 5
    assert source["out"]["frames"] == 105


def test_a_retimed_shot_reports_the_source_frames_it_really_covers(attach: Attach) -> None:
    """Half speed: 400 timeline frames over 200 source frames, so duration will not do."""
    item = FakeTimelineItem("A.mp4", 0, 400, source_start=1000, source_end=1199)
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    source = result["tracks"][0]["items"][0]["source"]
    assert source["in"]["frames"] == 1000
    assert source["out"]["frames"] == 1200


def test_a_shot_reports_the_media_pool_clip_it_came_from(attach: Attach) -> None:
    clip = FakeMediaPoolItem("C0012.mp4", "D:/angles/C0012.mp4")
    item = FakeTimelineItem("C0012.mp4", 0, 10, media_item=clip, takes=3)
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    reported = result["tracks"][0]["items"][0]
    assert reported["clip"] == "C0012.mp4"
    assert reported["takes"] == 3
    assert reported["enabled"] is True


def test_a_selector_is_counted_by_the_plural_name_the_api_really_declares(
    attach: Attach,
) -> None:
    """``GetTakeCount`` is not an API method, and asking for it counts nothing.

    The singular reads back as ``None`` (see ``FakeTimelineItem.NOT_API_METHODS``), which
    ``Reader`` cannot tell from a getter that is not there — so it takes the default branch
    and every clip reports zero takes, silently and forever (#68). Only the plural
    ``GetTakesCount`` counts a selector, so this test fails against the singular rather
    than degrading to a plausible zero.
    """
    item = FakeTimelineItem("C0012.mp4", 0, 10, takes=3)
    # The premise: the fake answers the wrong name the way a live item does.
    assert item.GetTakeCount is None
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    assert result["tracks"][0]["items"][0]["takes"] == 3


# --- #84: the getters that only answer for the current timeline -----------------------------


def _surveying(other_is_current: bool = True) -> tuple[Any, FakeProject]:
    """A timeline read the way an agent surveying a project reads it: from the outside.

    ``getters_need_current`` is what the live sweep proved of Studio 21.0.3.7 — enabled,
    locked and takes all answer falsy for a timeline that is not the project's current one.
    The flags are set the *opposite* way round from their falsy value (enabled and locked
    both true, a real selector of three) so that a wrapper reporting the lie cannot pass by
    coincidence.
    """
    item = FakeTimelineItem("C0012.mp4", 0, 10, takes=3)
    survey = FakeTimeline(
        "survey v1",
        video=[FakeTrack("Video 1", [item], enabled=True, locked=True)],
    )
    survey.getters_need_current = True
    open_now = a_cut()
    current = open_now if other_is_current else survey
    resolve = studio(timelines=[open_now, survey], timeline=current)
    project: Any = resolve.GetProjectManager().GetCurrentProject()
    assert isinstance(project, FakeProject)
    return resolve, project


def test_a_timeline_that_is_not_current_reports_unknown_not_a_confident_zero(
    attach: Attach,
) -> None:
    """#84: ``0`` and "unknown" are different answers, and Resolve returns the first.

    An agent asking "does this shot have alternates?" about a timeline it is not currently
    looking at got a confident ``takes: 0`` — the exact silent-wrong-value shape the
    wrappers in this repo exist to prevent. ``None`` is the honest reading, and the reply
    says which fields it applies to and how to get the real ones.
    """
    resolve, project = _surveying()
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips")

    assert result["ok"] is True
    assert result["timeline"]["current"] is False
    track = result["tracks"][0]
    assert track["enabled"] is None
    assert track["locked"] is None
    assert result["tracks"][0]["items"][0]["takes"] is None
    currency = result["currency"]
    assert currency["read_as_current"] is False
    assert currency["made_current"] is False
    assert currency["unknown_fields"] == ["enabled", "locked", "takes"]
    assert "make_current" in (currency["fix"] or "")


def test_make_current_switches_for_the_read_and_puts_the_timeline_back(attach: Attach) -> None:
    """The opt-in: the caller accepts a GUI switch and gets the real numbers for it.

    The switch is the *only* way to get them — the #84 probe proved a fresh handle, and
    even a fresh project object, still reads falsy — so the restore is what keeps a read
    from moving the director's timeline out from under them.
    """
    resolve, project = _surveying()
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips", make_current=True)

    assert result["ok"] is True
    track = result["tracks"][0]
    assert track["enabled"] is True
    assert track["locked"] is True
    assert result["tracks"][0]["items"][0]["takes"] == 3
    assert result["currency"]["read_as_current"] is True
    assert result["currency"]["made_current"] is True
    assert result["currency"]["unknown_fields"] == []
    # ``current`` is the project's standing state, which the restore puts back — so it
    # stays false even though the read itself was taken with the timeline current. The two
    # answer different questions and are deliberately allowed to disagree here.
    assert result["timeline"]["current"] is False
    assert project.timeline_switches == ["survey v1", "sunset-set v3"]
    back = project.GetCurrentTimeline()
    assert back is not None
    assert back.GetName() == "sunset-set v3"


def test_the_current_timeline_answers_in_full_without_being_switched(attach: Attach) -> None:
    """The common read is unchanged: no nulls, and no switch to restore."""
    resolve, project = _surveying(other_is_current=False)
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips")

    track = result["tracks"][0]
    assert track["enabled"] is True
    assert track["locked"] is True
    assert result["tracks"][0]["items"][0]["takes"] == 3
    assert result["currency"] == {
        "read_as_current": True,
        "made_current": False,
        "unknown_fields": [],
        "fix": None,
    }
    assert project.timeline_switches == []


def test_make_current_on_the_open_timeline_switches_nothing(attach: Attach) -> None:
    """Asking for a switch that is not needed must not move anything."""
    resolve, project = _surveying(other_is_current=False)
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips", make_current=True)

    assert result["tracks"][0]["items"][0]["takes"] == 3
    assert result["currency"]["made_current"] is False
    assert project.timeline_switches == []


def test_a_switch_resolve_refuses_leaves_the_fields_unknown_rather_than_certified(
    attach: Attach,
) -> None:
    """``made_current`` is what the switch achieved, never what it was asked to do.

    Resolve refuses ``SetCurrentTimeline`` while a modal dialog is up (#41). Taking the
    request as the answer would hand the reader the falsy values this whole path exists to
    distrust — and stamp ``read_as_current: true`` on them, which is worse than the bug it
    was fixing, because the reply would now vouch for the wrong number.
    """
    resolve, project = _surveying()
    project.refuse_set_current = True
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips", make_current=True)

    assert result["ok"] is True
    assert result["currency"]["read_as_current"] is False
    assert result["currency"]["made_current"] is False
    assert result["currency"]["unknown_fields"] == ["enabled", "locked", "takes"]
    track = result["tracks"][0]
    assert track["enabled"] is None
    assert track["locked"] is None
    assert result["tracks"][0]["items"][0]["takes"] is None


def test_the_fields_that_survived_the_sweep_are_still_read_off_a_non_current_timeline(
    attach: Attach,
) -> None:
    """The nulls are confined to the three proven getters, not spread over the reading.

    The #84 sweep read every Timeline and TimelineItem getter with a non-falsy true value
    on a non-current timeline: frames, names, source bounds, ``GetClipEnabled`` and
    ``GetMarkers`` did not drift. Blanking those too would answer "unknown" to questions
    Resolve answers perfectly well.
    """
    resolve, project = _surveying()
    attach(resolve)

    result = inspect_timeline("survey v1", detail="clips")

    assert result["timeline"]["name"] == "survey v1"
    assert result["tracks"][0]["name"] == "Video 1"
    assert result["tracks"][0]["item_count"] == 1
    shot = result["tracks"][0]["items"][0]
    assert shot["name"] == "C0012.mp4"
    assert shot["record"]["in"]["frames"] == 0
    assert shot["record"]["duration"]["frames"] == 10
    assert shot["enabled"] is True


def test_the_out_point_is_one_past_the_last_frame_whatever_get_end_says(attach: Attach) -> None:
    """Half-open [in, out) is the cut file's convention; GetEnd is not trusted to agree."""
    item = FakeTimelineItem("C0012.mp4", 100, 60, end_is_inclusive=True)
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    record = result["tracks"][0]["items"][0]["record"]
    assert record["out"]["frames"] == 160
    assert record["duration"]["frames"] == 60


# --- range -------------------------------------------------------------------------------


def test_a_range_in_frames_keeps_only_the_shots_that_touch_it(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="clips", start=155, end=200)

    names = [item["name"] for item in result["tracks"][0]["items"]]
    assert names == ["C0012.mp4", "C0031.mp4"]
    assert result["range"]["in"]["frames"] == 155
    assert result["range"]["out"]["frames"] == 200


def test_a_shot_that_ends_exactly_where_the_range_starts_is_outside_it(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="clips", start=160, end=250)

    assert [item["name"] for item in result["tracks"][0]["items"]] == ["C0031.mp4"]


def test_a_range_in_seconds_snaps_the_way_the_caller_says(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(
        detail="clips",
        start={"seconds": 2.6, "snap": "floor"},
        end={"seconds": 3.4, "snap": "ceil"},
    )

    assert result["range"]["in"]["frames"] == 155
    assert result["range"]["out"]["frames"] == 204


def test_a_range_in_bare_seconds_is_rejected_rather_than_guessed(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(start={"seconds": 2.6})

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "snap" in result["error"]["fix"]
    assert result["error"]["detail"]["field"] == "start"


def test_a_range_defaults_to_the_whole_timeline(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(detail="clips")

    assert result["range"]["in"]["frames"] == 100
    assert result["range"]["out"]["frames"] == 300
    assert result["item_count"] == 4


def test_a_backwards_range_is_rejected(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = inspect_timeline(start=200, end=150)

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"


# --- pagination --------------------------------------------------------------------------


def test_a_long_timeline_caps_the_shots_it_returns_and_spills_the_rest(attach: Attach) -> None:
    shots = [FakeTimelineItem(f"C{index:04d}.mp4", index * 10, 10) for index in range(20)]
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", shots)])))

    result = inspect_timeline(detail="clips", limit=5)

    assert result["item_count"] == 20
    assert result["truncated"] is True
    assert len(result["tracks"][0]["items"]) == 5
    spilled = Path(result["spilled_to"])
    assert spilled.exists()
    assert len(json.loads(spilled.read_text(encoding="utf-8"))["tracks"][0]["items"]) == 20


def test_a_busy_video_track_cannot_starve_the_angles_stacked_under_it(attach: Attach) -> None:
    """The stacked reference is the layout this tool exists to make readable."""
    busy = FakeTrack(
        "Cam A", [FakeTimelineItem(f"A{index}.mp4", index * 10, 10) for index in range(10)]
    )
    quiet = FakeTrack(
        "Cam B", [FakeTimelineItem(f"B{index}.mp4", index * 10, 10) for index in range(2)]
    )
    attach(studio(timeline=FakeTimeline("sync", video=[busy, quiet])))

    result = inspect_timeline(detail="clips", limit=4)

    kept = {track["name"]: [item["name"] for item in track["items"]] for track in result["tracks"]}
    assert kept == {"Cam A": ["A0.mp4", "A1.mp4"], "Cam B": ["B0.mp4", "B1.mp4"]}
    assert result["truncated"] is True


def test_the_cap_counts_shots_across_every_track(attach: Attach) -> None:
    tracks = [
        FakeTrack(f"Cam {letter}", [FakeTimelineItem(f"{letter}.mp4", index * 10, 10)])
        for index, letter in enumerate("ABCD")
    ]
    attach(studio(timeline=FakeTimeline("sync", video=tracks)))

    result = inspect_timeline(detail="clips", limit=2)

    returned = sum(len(track["items"]) for track in result["tracks"])
    assert returned == 2
    assert result["item_count"] == 4
    assert result["truncated"] is True


def test_the_default_cap_keeps_a_reply_inside_the_client_token_budget(attach: Attach) -> None:
    """The 25k-token cap is the reason the cap exists, so the default has to fit under it.

    Dense JSON — repeated keys, runs of digits, six dual-time blocks a shot — runs nearer
    3.3 characters to the token than the 4 that prose does, so the budget is counted at the
    tighter rate. It is the shot shape, not the number of shots, that makes a limit picked
    by feel land wrong; this fails if either grows.
    """
    shots = [
        FakeTimelineItem(f"C{index:04d}.mp4", index * 10, 10, source_start=index)
        for index in range(500)
    ]
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", shots)])))

    result = inspect_timeline(detail="clips")

    assert result["truncated"] is True
    assert len(json.dumps(result)) < 3.3 * 25_000


def test_the_timeline_duration_is_the_end_resolve_reports(attach: Attach) -> None:
    """A timeline has no duration getter, so this one reading rests on GetEndFrame."""
    attach(
        studio(
            timeline=FakeTimeline(
                "cut v1",
                start_frame=100,
                end_frame=400,
                video=[FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 100, 60)])],
            )
        )
    )

    result = inspect_timeline(detail="summary")

    assert result["timeline"]["end"]["frames"] == 400
    assert result["timeline"]["duration"]["frames"] == 300


def test_a_summary_never_spills(attach: Attach) -> None:
    shots = [FakeTimelineItem(f"C{index:04d}.mp4", index * 10, 10) for index in range(20)]
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", shots)])))

    result = inspect_timeline(detail="summary", limit=5)

    assert result["truncated"] is False
    assert result["spilled_to"] is None


# --- the connection, under the tools -----------------------------------------------------


def test_a_dropped_handle_reconnects_once_and_still_answers(attach: Attach) -> None:
    dead = studio(timeline=a_cut())
    connector = attach(dead, studio(timeline=a_cut()))
    assert list_timelines()["ok"] is True

    dead.drop()

    result = list_timelines()

    assert result["ok"] is True
    assert result["timelines"][0]["name"] == "sunset-set v3"
    assert connector.attempts == 2


def test_resolve_quitting_mid_call_is_a_connection_failure_not_a_bug(attach: Attach) -> None:
    dying = studio(timeline=a_cut())
    dying.die_after(1)
    second = studio(timeline=a_cut())
    second.drop()
    attach(dying, second)

    result = inspect_timeline()

    assert result["ok"] is False
    assert result["error"]["code"] == "resolve_unavailable"


def a_telling_cut() -> FakeTimeline:
    """A cut whose every readable field differs from the value a fallback would invent.

    Track names, marker counts, clip names, take counts and the enabled flag all have a
    default somewhere in the reading path; a timeline built out of those defaults could be
    fabricated without any test noticing. This one cannot.
    """
    return FakeTimeline(
        "sunset-set v3",
        start_frame=100,
        markers={120.0: {"note": "fill"}, 240.0: {"note": "solo"}},
        video=[
            FakeTrack(
                "Cam A",
                [
                    FakeTimelineItem(
                        "C0012.mp4",
                        100,
                        60,
                        source_start=1000,
                        media_item=FakeMediaPoolItem("C0012.mp4", "D:/angles/C0012.mp4"),
                        enabled=False,
                        takes=2,
                    )
                ],
                locked=True,
            )
        ],
        audio=[FakeTrack("Master", [FakeTimelineItem("master_mix.wav", 100, 200)])],
    )


@pytest.mark.parametrize("survives", range(1, 41))
def test_a_death_part_way_through_a_read_never_returns_half_a_timeline(
    attach: Attach, survives: int
) -> None:
    """Wherever the handle dies, the answer is a complete reading or a stated failure.

    Falling back is right for a getter an older Resolve does not have and wrong for a
    handle that has gone away: the second would hand the agent a timeline of plausible
    defaults with nothing to distrust. This walks the death forward through every call the
    read makes, and every field it checks would read as a fallback if one had been taken.
    """
    dying = studio(timeline=a_telling_cut())
    dying.die_after(survives)
    attach(dying, studio(timeline=a_telling_cut()))

    result = inspect_timeline(detail="clips")

    assert result["ok"] is True
    assert result["timeline"]["duration"]["frames"] == 200
    assert result["timeline"]["markers"] == 2
    assert result["timeline"]["tracks"] == {"video": 1, "audio": 1, "subtitle": 0}
    video = result["tracks"][0]
    assert video["name"] == "Cam A"
    assert video["locked"] is True
    shot = video["items"][0]
    assert shot["name"] == "C0012.mp4"
    assert shot["clip"] == "C0012.mp4"
    assert shot["takes"] == 2
    assert shot["enabled"] is False
    assert shot["sync_offset"]["frames"] == -900


def test_a_bug_in_the_wrapper_is_reported_once_as_a_bug(
    attach: Attach, monkeypatch: pytest.MonkeyPatch
) -> None:
    attach(studio(timeline=a_cut()))
    calls: list[str] = []

    def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("called")
        raise ValueError("boom")

    monkeypatch.setattr(wrapper, "list_timelines", explode)

    result = list_timelines()

    assert result["ok"] is False
    assert result["error"]["code"] == "internal_error"
    assert calls == ["called"]


def test_a_timeline_that_cannot_be_read_is_skipped_rather_than_sinking_the_list(
    attach: Attach,
) -> None:
    """Resolve hands out timeline proxies that occasionally answer nothing at all."""
    attach(studio(timelines=[a_cut("sunset-set v1"), None, a_cut("sunset-set v2")]))

    result = list_timelines()

    assert result["ok"] is True
    assert [entry["name"] for entry in result["timelines"]] == [
        "sunset-set v1",
        "sunset-set v2",
    ]
