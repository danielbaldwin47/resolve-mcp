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
    FakeResolve,
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


def test_list_spills_past_the_cap_rather_than_flooding_the_reply(
    attach: Attach, tmp_path: Path
) -> None:
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
    attach(studio(timelines=[sync_reference(), a_cut()], timeline=a_cut()))

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

    offsets = {
        track["name"]: track["items"][0]["sync_offset"]["frames"] for track in result["tracks"]
    }
    assert offsets == {"Cam A": -1000, "Cam B": -2880}


def test_an_angle_on_a_resolve_without_source_frames_still_reports_its_offset(
    attach: Attach,
) -> None:
    """Pre-18.5 has no GetSourceStartFrame; the left offset carries the same information."""
    item = FakeTimelineItem("A.mp4", 120, 400, source_start=3000, supports_source_frames=False)
    attach(studio(timeline=FakeTimeline("sync", video=[FakeTrack("Cam A", [item])])))

    result = inspect_timeline(detail="clips")

    assert result["tracks"][0]["items"][0]["sync_offset"]["frames"] == -2880


def test_a_shot_reports_the_media_pool_clip_it_came_from(attach: Attach) -> None:
    clip = FakeMediaPoolItem("C0012.mp4", "D:/angles/C0012.mp4")
    item = FakeTimelineItem("C0012.mp4", 0, 10, media_item=clip, takes=3)
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", [item])])))

    result = inspect_timeline(detail="clips")

    reported = result["tracks"][0]["items"][0]
    assert reported["clip"] == "C0012.mp4"
    assert reported["takes"] == 3
    assert reported["enabled"] is True


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


def test_a_long_timeline_caps_the_shots_it_returns_and_spills_the_rest(
    attach: Attach, tmp_path: Path
) -> None:
    shots = [FakeTimelineItem(f"C{index:04d}.mp4", index * 10, 10) for index in range(20)]
    attach(studio(timeline=FakeTimeline("cut v1", video=[FakeTrack("Video 1", shots)])))

    result = inspect_timeline(detail="clips", limit=5)

    assert result["item_count"] == 20
    assert result["truncated"] is True
    assert len(result["tracks"][0]["items"]) == 5
    spilled = Path(result["spilled_to"])
    assert spilled.exists()
    assert len(json.loads(spilled.read_text(encoding="utf-8"))["tracks"][0]["items"]) == 20


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
    resolve: FakeResolve = studio(timelines=[a_cut("sunset-set v1"), a_cut("sunset-set v2")])
    project = resolve.projects["sunset-set"]
    project._timelines.insert(1, None)  # type: ignore[arg-type]

    attach(resolve)

    result = list_timelines()

    assert result["ok"] is True
    assert [entry["name"] for entry in result["timelines"]] == [
        "sunset-set v1",
        "sunset-set v2",
    ]
