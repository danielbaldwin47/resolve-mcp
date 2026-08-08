"""The decisions behind the state the live suite builds for itself (#135).

The live tier is the only place these helpers run for real, and it is the one tier that
cannot be run in CI — so every *decision* they make is settled here instead: which names
are the suite's own, what happens when the timeline Resolve is sitting on is one of them,
and what command generates the hard-cut clip the scene scan needs. What is left for the
live tier is whether Resolve and ffmpeg accept the calls, which is exactly the split
CLAUDE.md draws.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.errors import FfmpegUnavailableError
from resolve_mcp.ffmpeg import Completed
from resolve_mcp.resolve.timeline import timelines_of

from .fakes import FakeMediaPool, FakeProject, FakeTimeline
from .live_state import (
    HARD_CUT_COLOURS,
    ClipGenerationError,
    hard_cut_argv,
    is_suite_timeline,
    sweep_suite_timelines,
    write_hard_cut_clip,
)

# --- which names are the suite's own -------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "resolve-mcp-smoke v1",
        "resolve-mcp-smoke v27",
        "resolve-mcp-lock-probe",
        "resolve-mcp smoke round-trip 043551",
        "resolve-mcp smoke dissolve 065705",
    ],
)
def test_every_name_the_suite_builds_reads_as_the_suites_own(name: str) -> None:
    assert is_suite_timeline(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "MCP Monkfish Tune 02 v5",
        "Monkfish Main",
        "Zinc - Set 2 Main",
        # The prefix has to end at a separator, or a director's own name that merely starts
        # the same way would be swept.
        "resolve-mcpx",
        "resolve-mcp",
        # A sweep that matched anywhere in the name would take a cut somebody kept on
        # purpose, and there is no undo for a deleted timeline.
        "keep resolve-mcp-smoke v1",
        "",
    ],
)
def test_a_name_the_suite_did_not_build_is_never_swept(name: str) -> None:
    assert is_suite_timeline(name) is False


# --- the sweep ------------------------------------------------------------------------------


def a_project(
    *names: str,
    current: str | None = None,
) -> tuple[FakeMediaPool, FakeProject, dict[str, FakeTimeline]]:
    """A project holding one timeline per name, with ``current`` the one Resolve sits on."""
    timelines = {name: FakeTimeline(name) for name in names}
    pool = FakeMediaPool()
    project = FakeProject(
        "2026-06_Zinc_and_Monkfish",
        timeline=timelines[current] if current else None,
        media_pool=pool,
        timelines=list(timelines.values()),
    )
    return pool, project, timelines


def test_the_sweep_deletes_the_suites_leftovers_and_leaves_the_directors_cuts() -> None:
    pool, project, timelines = a_project(
        "Monkfish Main",
        "resolve-mcp-smoke v1",
        "MCP Monkfish Tune 02 v5",
        "resolve-mcp-lock-probe",
        current="MCP Monkfish Tune 02 v5",
    )

    swept = sweep_suite_timelines(pool, project)

    assert swept.deleted == ["resolve-mcp-smoke v1", "resolve-mcp-lock-probe"]
    assert swept.kept == []
    assert timelines_of(project) == [
        timelines["Monkfish Main"],
        timelines["MCP Monkfish Tune 02 v5"],
    ]


def test_a_project_with_no_leftovers_is_left_entirely_alone() -> None:
    pool, project, _ = a_project("Monkfish Main", current="Monkfish Main")

    swept = sweep_suite_timelines(pool, project)

    assert swept.deleted == []
    assert swept.kept == []
    assert pool.deleted_timelines == []
    assert project.timeline_switches == []


def test_resolve_is_moved_off_a_leftover_before_the_sweep_deletes_it() -> None:
    """Resolve refuses to delete the timeline it is sitting on, and says so only by
    returning ``False`` — so a sweep that did not move first would leave the cut behind
    and never hear why. A previous run ends on its own build, so this is the normal case.
    """
    pool, project, timelines = a_project(
        "Monkfish Main",
        "resolve-mcp-smoke v1",
        current="resolve-mcp-smoke v1",
    )

    swept = sweep_suite_timelines(pool, project)

    assert project.GetCurrentTimeline() is timelines["Monkfish Main"]
    assert swept.deleted == ["resolve-mcp-smoke v1"]
    assert swept.kept == []


def test_the_last_leftover_survives_when_it_is_the_only_cut_left_to_sit_on() -> None:
    """A project holding nothing but the suite's own builds has nowhere to move to, so one
    of them stays. It is reported rather than swallowed: the next run's version numbers
    carry on from it, and a caller that assumed a clean project would be wrong."""
    pool, project, _ = a_project(
        "resolve-mcp-smoke v1",
        "resolve-mcp-smoke v2",
        current="resolve-mcp-smoke v2",
    )

    swept = sweep_suite_timelines(pool, project)

    assert swept.deleted == ["resolve-mcp-smoke v1"]
    assert swept.kept == ["resolve-mcp-smoke v2"]


def test_one_cut_resolve_will_not_delete_does_not_stop_the_rest_from_going() -> None:
    """Deleting one at a time is what makes this reportable: a single batch call answers
    ``False`` for the whole list, and the sweep could not say which one stuck."""
    pool, project, timelines = a_project(
        "Monkfish Main",
        "resolve-mcp-smoke v1",
        "resolve-mcp-smoke v2",
        current="Monkfish Main",
    )
    stuck = timelines["resolve-mcp-smoke v1"]
    pool.refuse_deleting = {stuck}

    swept = sweep_suite_timelines(pool, project)

    assert swept.deleted == ["resolve-mcp-smoke v2"]
    assert swept.kept == ["resolve-mcp-smoke v1"]


# --- the hard-cut clip the scene scan needs ------------------------------------------------


def test_the_generated_clip_is_one_solid_colour_per_cut_concatenated_in_order() -> None:
    """Solid colours are the point: a scene score has to clear its threshold on every cut
    for the clip to be worth scanning, and nothing clears it like a whole frame changing.
    """
    argv = hard_cut_argv("ffmpeg", Path("C:/scratch/hard-cut.mp4"), colours=("red", "green"))

    assert argv[0] == "ffmpeg"
    assert argv.count("lavfi") == 2
    assert "color=c=red:s=320x240:r=24:d=1" in argv
    assert "color=c=green:s=320x240:r=24:d=1" in argv
    assert "[0:v][1:v]concat=n=2:v=1:a=0[cut]" in argv
    assert argv[-1] == str(Path("C:/scratch/hard-cut.mp4"))


def test_the_generated_clip_overwrites_rather_than_asking() -> None:
    """ffmpeg prompts on an existing output, and a prompt in a test run is a hang."""
    argv = hard_cut_argv("ffmpeg", Path("out.mp4"))

    assert "-y" in argv
    assert argv.count("lavfi") == len(HARD_CUT_COLOURS)


def test_the_default_clip_carries_more_cuts_than_the_one_the_scan_has_to_find() -> None:
    """The live assertion is "at least one cut"; four colours means three, so a detector
    that misses one still proves the mapping rather than turning the tier red."""
    assert len(HARD_CUT_COLOURS) >= 3


def test_writing_the_clip_names_the_frame_size_and_rate_the_scan_reads_it_on() -> None:
    seen: list[list[str]] = []

    def runner(argv: object) -> Completed:
        seen.append(list(argv))  # type: ignore[call-overload]
        return Completed(0, "")

    write_hard_cut_clip(Path("out.mp4"), runner=runner, ffmpeg="ffmpeg", fps=30, size="640x360")

    assert "color=c=red:s=640x360:r=30:d=1" in seen[0]


def test_a_refused_generation_says_what_ffmpeg_said_about_it() -> None:
    def runner(argv: object) -> Completed:
        return Completed(1, "Unknown filter 'concat'")

    with pytest.raises(ClipGenerationError) as raised:
        write_hard_cut_clip(Path("out.mp4"), runner=runner, ffmpeg="ffmpeg")

    assert "Unknown filter 'concat'" in str(raised.value)


def test_a_missing_ffmpeg_is_the_error_that_names_the_binary() -> None:
    """Shaped by ``ffmpeg.invoke`` rather than here, so the live fixture can skip on it."""

    def runner(argv: object) -> Completed:
        raise FileNotFoundError

    with pytest.raises(FfmpegUnavailableError):
        write_hard_cut_clip(Path("out.mp4"), runner=runner, ffmpeg="ffmpeg")
