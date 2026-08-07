"""Deliver: listing render presets, and rendering a timeline or a range of one as a job.

The seam is the Resolve scripting boundary — the fake project *is* the render queue, so
every decision here (which preset was loaded, what MarkIn/MarkOut the range became, whether
the file was replaced or refused) is read off the calls the tool made. What no fake can
answer is whether Resolve itself reads MarkIn/MarkOut on the timeline's own clock; that is a
live AC, and it is the one thing this file cannot prove.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.config import Config, get_config, set_config
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.tools.jobs import get_job
from resolve_mcp.tools.render import list_render_presets, render_timeline

from .conftest import Attach
from .fakes import FakeProject, FakeResolve, FakeTimeline, studio

PRESET = "H.264 Master"
DEFAULT_PRESET = "H.265 Master"
PRESETS_ON_THIS_PROJECT = ["H.264 Master", "H.265 Master", "ProRes 422 HQ"]


# --- presets -------------------------------------------------------------------------------


def test_the_preset_list_names_what_the_deliver_page_offers(attach: Attach) -> None:
    attach(studio())

    reply = list_render_presets()

    assert reply["ok"] is True
    assert reply["presets"] == ["H.264 Master", "H.265 Master", "ProRes 422 HQ"]
    assert reply["count"] == 3


def test_the_preset_list_reports_the_format_currently_loaded(attach: Attach) -> None:
    resolve = studio()
    _project(resolve).SetCurrentRenderFormatAndCodec("mov", "ProRes422HQ")
    attach(resolve)

    reply = list_render_presets()

    assert reply["current"] == {"format": "mov", "codec": "ProRes422HQ"}


def test_no_project_open_is_a_failure_not_an_empty_preset_list(attach: Attach) -> None:
    attach(studio(project=None))

    reply = list_render_presets()

    assert reply["ok"] is False
    assert reply["error"]["code"] == "no_project_open"


# --- the default preset --------------------------------------------------------------------


def test_a_render_with_no_preset_named_uses_the_configured_default(attach: Attach) -> None:
    """"Render this" means render with the preset the server knows — #96's whole point."""
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "24"))
    attach(resolve)

    started = render_timeline()
    record = wait_for(started["job"]["job_id"])

    assert started["job"]["params"]["preset"] == DEFAULT_PRESET
    assert started["job"]["params"]["preset_source"] == "default"
    assert record.state == "completed"
    assert record.result is not None
    assert record.result["preset"] == DEFAULT_PRESET
    assert record.result["preset_source"] == "default"
    assert record.result["codec"] == "H.265"
    assert _project(resolve).loaded_presets == [DEFAULT_PRESET]


def test_the_env_override_changes_which_preset_a_bare_render_uses(
    attach: Attach, tmp_path: Path
) -> None:
    """The default is config like any other key, so a machine can be pointed elsewhere."""
    set_config(
        Config.from_env(
            {
                "RESOLVE_MCP_CACHE": str(tmp_path / "cache"),
                "RESOLVE_MCP_DEFAULT_RENDER_PRESET": "ProRes 422 HQ",
            }
        )
    )
    resolve = studio(timeline=_concert())
    attach(resolve)

    record = wait_for(render_timeline()["job"]["job_id"])

    assert record.state == "completed"
    assert record.result is not None
    assert record.result["preset"] == "ProRes 422 HQ"
    assert record.result["preset_source"] == "default"
    assert _project(resolve).loaded_presets == ["ProRes 422 HQ"]


def test_a_named_preset_is_marked_explicit_and_beats_the_default(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    started = render_timeline(preset=PRESET)
    record = wait_for(started["job"]["job_id"])

    assert started["job"]["params"]["preset_source"] == "explicit"
    assert record.result is not None
    assert record.result["preset"] == PRESET
    assert record.result["preset_source"] == "explicit"
    assert _project(resolve).loaded_presets == [PRESET]


def test_defaulting_to_a_preset_and_naming_it_are_one_cache_entry(attach: Attach) -> None:
    """How the preset was chosen does not change a frame of the file, so it is not in the key.

    A concert render costs minutes to hours; re-running one because the second call spelled
    out the name the first call defaulted to would be the expensive kind of wrong. The
    replayed result still reports the render that actually happened — ``default``.
    """
    resolve = studio(timeline=_concert())
    attach(resolve)

    first = wait_for(render_timeline(name="Blue Monk")["job"]["job_id"])
    again = render_timeline(preset=DEFAULT_PRESET, name="Blue Monk")["job"]

    assert again["cached"] is True
    assert again["result"] == first.result
    assert len(_project(resolve).render_jobs) == 1


# --- the whole timeline --------------------------------------------------------------------


def test_a_full_timeline_render_produces_the_file(attach: Attach) -> None:
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "24"))
    attach(resolve)

    started = render_timeline(preset=PRESET)
    record = wait_for(started["job"]["job_id"])

    assert record.state == "completed"
    assert record.result is not None
    written = Path(record.result["path"])
    assert written.exists()
    assert written.suffix == ".mp4"
    assert record.result["format"] == "mp4"
    assert record.result["codec"] == "H.264"
    assert record.result["whole_timeline"] is True
    assert record.result["size_bytes"] > 0

    project = _project(resolve)
    assert project.loaded_presets == [PRESET]
    assert project.render_settings["SelectAllFrames"] is True
    assert "MarkIn" not in project.render_settings
    assert project.render_settings["TargetDir"] == str(get_config().render_dir)
    assert project.render_mode == 1
    assert project.render_queue == []


def test_a_whole_timeline_render_still_says_what_it_covers(attach: Attach) -> None:
    """Dual time everywhere: a file that will not say what it holds has to be opened to know."""
    attach(studio(timeline=_concert()))

    record = wait_for(render_timeline(preset=PRESET)["job"]["job_id"])

    assert record.result is not None
    covered = record.result["range"]
    assert covered["start"]["frames"] == 86_400
    assert covered["end"]["frames"] == 87_400
    assert covered["duration"]["frames"] == 1_000
    assert covered["duration"]["timecode"] == "00:00:41:16"


def test_the_render_reports_through_get_job_like_any_other_job(attach: Attach) -> None:
    attach(studio(timeline=FakeTimeline("sunset-set v3", "24")))

    job_id = render_timeline(preset=PRESET)["job"]["job_id"]
    wait_for(job_id)
    polled = get_job(job_id)

    assert polled["ok"] is True
    assert polled["job"]["kind"] == "render_timeline"
    assert polled["job"]["state"] == "completed"
    assert polled["job"]["progress"] == 1.0
    assert Path(polled["job"]["result"]["path"]).exists()


def test_the_deliverable_is_named_for_the_song_not_for_the_cache(attach: Attach) -> None:
    attach(studio(timeline=FakeTimeline("sunset-set v3", "24")))

    started = render_timeline(preset=PRESET, name="Blue Monk")
    record = wait_for(started["job"]["job_id"])

    assert record.result is not None
    assert Path(record.result["path"]).name == "Blue-Monk.mp4"


def test_a_named_target_directory_is_where_the_deliverable_lands(
    attach: Attach,
    tmp_path: Path,
) -> None:
    attach(studio(timeline=FakeTimeline("sunset-set v3", "24")))
    deliverables = tmp_path / "deliverables"

    started = render_timeline(preset=PRESET, name="Blue Monk", target_dir=str(deliverables))
    record = wait_for(started["job"]["job_id"])

    assert record.result is not None
    assert Path(record.result["path"]) == deliverables / "Blue-Monk.mp4"


def test_the_directors_timeline_is_put_back_after_rendering_another_one(
    attach: Attach,
) -> None:
    current = FakeTimeline("sunset-set v3", "24")
    other = FakeTimeline("sunset-set v2", "24")
    resolve = studio(timeline=current, timelines=[current, other])
    attach(resolve)

    wait_for(render_timeline(preset=PRESET, timeline="sunset-set v2")["job"]["job_id"])

    project = _project(resolve)
    assert project.timeline_switches == ["sunset-set v2", "sunset-set v3"]
    assert project.GetCurrentTimeline() is current


# --- a range off a longer timeline ---------------------------------------------------------


def test_a_range_render_marks_in_and_out_on_the_timelines_own_clock(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    started = render_timeline(preset=PRESET, name="Blue Monk", start=86_500, end=86_600)
    record = wait_for(started["job"]["job_id"])

    assert record.state == "completed"
    project = _project(resolve)
    assert project.render_settings["SelectAllFrames"] is False
    assert project.render_settings["MarkIn"] == 86_500
    # Half-open in, inclusive out: the last frame rendered is the one before end.
    assert project.render_settings["MarkOut"] == 86_599

    assert record.result is not None
    assert record.result["whole_timeline"] is False
    rendered = record.result["range"]
    assert rendered["start"]["frames"] == 86_500
    assert rendered["end"]["frames"] == 86_600
    assert rendered["duration"]["frames"] == 100
    assert rendered["duration"]["seconds"] == pytest.approx(100 / 24, abs=0.001)


def test_a_range_in_seconds_has_to_say_which_way_to_snap(attach: Attach) -> None:
    attach(studio(timeline=_concert()))

    reply = render_timeline(preset=PRESET, start={"seconds": 3600.0}, end=86_600)

    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid_request"
    assert "snap" in reply["error"]["fix"]


def test_a_range_in_seconds_with_a_snap_is_taken(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    started = render_timeline(
        preset=PRESET,
        start={"seconds": 3600.0, "snap": "floor"},
        end={"seconds": 3610.0, "snap": "ceil"},
    )
    wait_for(started["job"]["job_id"])

    assert _project(resolve).render_settings["MarkIn"] == 86_400
    assert _project(resolve).render_settings["MarkOut"] == 86_639


def test_one_bound_alone_runs_to_the_timelines_own_edge(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    wait_for(render_timeline(preset=PRESET, start=86_500)["job"]["job_id"])

    assert _project(resolve).render_settings["MarkIn"] == 86_500
    assert _project(resolve).render_settings["MarkOut"] == 87_399


def test_a_range_past_the_end_of_the_timeline_is_refused_before_anything_is_queued(
    attach: Attach,
) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    reply = render_timeline(preset=PRESET, start=86_500, end=99_999)

    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid_request"
    assert reply["error"]["detail"]["timeline_end"] == 87_400
    assert _project(resolve).render_jobs == []


def test_a_range_that_covers_no_frames_is_refused(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    reply = render_timeline(preset=PRESET, start=86_600, end=86_600)

    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid_request"
    assert _project(resolve).render_jobs == []


def test_two_songs_off_one_timeline_are_two_files(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    first = wait_for(
        render_timeline(preset=PRESET, name="Blue Monk", start=86_400, end=86_600)["job"]["job_id"]
    )
    second = wait_for(
        render_timeline(preset=PRESET, name="Straight No Chaser", start=86_600, end=86_900)["job"][
            "job_id"
        ]
    )

    assert first.result is not None
    assert second.result is not None
    assert first.result["path"] != second.result["path"]
    assert Path(first.result["path"]).exists()
    assert Path(second.result["path"]).exists()
    assert len(_project(resolve).render_jobs) == 2


# --- what the queue does wrong -------------------------------------------------------------


def test_an_unknown_preset_names_the_ones_that_exist(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    reply = render_timeline(preset="Vimeo 4K")

    assert reply["ok"] is False
    assert reply["error"]["code"] == "render_preset_not_found"
    assert reply["error"]["detail"]["available"] == PRESETS_ON_THIS_PROJECT
    assert _project(resolve).render_jobs == []


def test_a_default_preset_this_project_lacks_refuses_rather_than_falling_back(
    attach: Attach,
) -> None:
    """No fallback, ever (#71 Q4): a missing default is a question for the director.

    Rendering the nearest other preset would hand back a file of the wrong shape under a
    name that says it is right, and nothing downstream would catch it.
    """
    resolve = studio(timeline=_concert())
    del _project(resolve).render_presets[DEFAULT_PRESET]
    attach(resolve)

    reply = render_timeline()

    assert reply["ok"] is False
    assert reply["error"]["code"] == "render_preset_not_found"
    assert reply["error"]["detail"]["requested"] == DEFAULT_PRESET
    assert reply["error"]["detail"]["available"] == ["H.264 Master", "ProRes 422 HQ"]
    assert _project(resolve).render_jobs == []


def test_a_preset_resolve_refuses_to_load_never_reaches_the_queue(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    _project(resolve).accepts_preset = False
    attach(resolve)

    record = wait_for(render_timeline(preset=PRESET)["job"]["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "render_queue_failed"
    assert _project(resolve).render_jobs == []


def test_a_queue_that_refuses_the_job_fails_the_job_not_the_server(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    _project(resolve).accepts_job = False
    attach(resolve)

    record = wait_for(render_timeline(preset=PRESET)["job"]["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "render_queue_failed"


def test_a_render_that_reports_success_but_writes_nothing_is_a_failed_job(
    attach: Attach,
) -> None:
    resolve = studio(timeline=_concert())
    _project(resolve).render_writes_the_file = False
    attach(resolve)

    record = wait_for(render_timeline(preset=PRESET)["job"]["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert "wrote nothing" in record.error["cause"]


# --- caching and replacing -----------------------------------------------------------------


def test_the_same_render_twice_does_not_render_twice(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    first = wait_for(render_timeline(preset=PRESET, name="Blue Monk")["job"]["job_id"])
    again = render_timeline(preset=PRESET, name="Blue Monk")["job"]

    assert again["cached"] is True
    assert again["state"] == "completed"
    assert again["result"] == first.result
    assert len(_project(resolve).render_jobs) == 1


def test_refresh_renders_again_and_replaces_the_file(attach: Attach) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)

    first = wait_for(render_timeline(preset=PRESET, name="Blue Monk")["job"]["job_id"])
    again = wait_for(
        render_timeline(preset=PRESET, name="Blue Monk", refresh=True)["job"]["job_id"]
    )

    assert again.cached is False
    assert again.result is not None
    assert first.result is not None
    assert again.result["path"] == first.result["path"]
    assert Path(again.result["path"]).exists()
    assert len(_project(resolve).render_jobs) == 2


def test_a_file_already_at_the_target_is_never_replaced_silently(
    attach: Attach,
    tmp_path: Path,
) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    theirs = deliverables / "Blue-Monk.mp4"
    theirs.write_bytes(b"the director's own export")

    reply = render_timeline(preset=PRESET, name="Blue Monk", target_dir=str(deliverables))

    assert reply["ok"] is False
    assert reply["error"]["code"] == "render_target_exists"
    assert "refresh" in reply["error"]["fix"]
    assert theirs.read_bytes() == b"the director's own export"
    assert _project(resolve).render_jobs == []


def test_refresh_is_how_a_file_at_the_target_gets_replaced(
    attach: Attach,
    tmp_path: Path,
) -> None:
    resolve = studio(timeline=_concert())
    attach(resolve)
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "Blue-Monk.mp4").write_bytes(b"an older take")

    record = wait_for(
        render_timeline(
            preset=PRESET,
            name="Blue Monk",
            target_dir=str(deliverables),
            refresh=True,
        )["job"]["job_id"]
    )

    assert record.state == "completed"
    assert (deliverables / "Blue-Monk.mp4").read_bytes() != b"an older take"


def test_replacing_a_deliverable_leaves_its_sidecars_alone(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """refresh replaces the file this render writes — not everything sharing its name."""
    resolve = studio(timeline=_concert())
    attach(resolve)
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "Blue-Monk.mp4").write_bytes(b"an older take")
    sidecar = deliverables / "Blue-Monk.srt"
    sidecar.write_text("1\n00:00:00,000 --> 00:00:02,000\nthe subtitles\n", encoding="utf-8")

    record = wait_for(
        render_timeline(
            preset=PRESET,
            name="Blue Monk",
            target_dir=str(deliverables),
            refresh=True,
        )["job"]["job_id"]
    )

    assert record.state == "completed"
    assert sidecar.exists()
    assert "the subtitles" in sidecar.read_text(encoding="utf-8")


def test_a_cut_that_changed_renders_again_over_the_servers_own_file(attach: Attach) -> None:
    """A deliverable under the server's own directory is the server's to replace.

    The whole point of the default target is that re-rendering a song after a review round
    costs nothing but the render — no refresh flag, no name the director has to invent.
    """
    timeline = _concert()
    resolve = studio(timeline=timeline)
    attach(resolve)

    first = wait_for(render_timeline(preset=PRESET, name="Blue Monk")["job"]["job_id"])
    timeline._end_frame = 88_000
    again = render_timeline(preset=PRESET, name="Blue Monk")["job"]
    finished = wait_for(again["job_id"])

    assert again["cached"] is False
    assert finished.state == "completed"
    assert first.result is not None
    assert finished.result is not None
    assert finished.result["path"] == first.result["path"]
    assert len(_project(resolve).render_jobs) == 2


# --- helpers -------------------------------------------------------------------------------


def _concert() -> FakeTimeline:
    """An hour-start timeline, as Resolve numbers one: 01:00:00:00 at 24 fps is frame 86400."""
    return FakeTimeline("sunset-set v3", "24", start_frame=86_400, end_frame=87_400)


def _project(resolve: FakeResolve) -> FakeProject:
    project: Any = resolve.GetProjectManager().GetCurrentProject()
    assert isinstance(project, FakeProject)
    return project
