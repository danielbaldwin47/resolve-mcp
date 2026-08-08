"""State the live suite builds for itself, rather than assuming the project has it (#135).

The live tier runs against a working project on a real machine, and for a long time it read
that project as a given: a still it hoped was in the pool, whatever timeline the last test
left current, a clip it hoped had cuts in it. Every one of those was a failure waiting for
the day the project changed, and #119 §B found four of them at once.

So the suite builds what it needs. Two things it needs cannot be built by the tools under
test — a project clear of the *previous* run's leftovers, and a clip with hard cuts in it —
and both live here. The decisions they make (which names are the suite's own, what to do
when Resolve is sitting on one of them, what command generates the clip) are settled in
``tests/test_live_state.py`` against the fakes; what is left for the live tier is whether
Resolve and ffmpeg take the calls.

Cleanup stays at the timeline and bin level. ``DeleteProject`` is refused on the live box
(#119 §B), so a scratch *project* is not an option and the suite tidies inside the one that
is open.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from resolve_mcp.config import get_config
from resolve_mcp.ffmpeg import STDERR_TAIL, Runner, invoke
from resolve_mcp.resolve.timeline import name_of, same_timeline, timelines_of

Pool = Any
Project = Any
Timeline = Any

SUITE_TIMELINE = re.compile(r"^resolve-mcp[- ]")
"""Every timeline the suite builds is named from this prefix.

``resolve-mcp-smoke v<n>`` (every build), ``resolve-mcp-lock-probe`` (the locked-track
probe) and ``resolve-mcp smoke <what> <hhmmss>`` (the round-trip and dissolve imports) all
start this way, and the separator has to be part of the match: a sweep is a delete with no
undo, so a director's cut that merely *starts* with the same letters must not qualify, and
neither must one that mentions the prefix further along.
"""


class Swept(NamedTuple):
    """What the sweep managed, by name — ``kept`` is what Resolve would not let go of."""

    deleted: list[str]
    kept: list[str]


def is_suite_timeline(name: str) -> bool:
    """Whether this name is one the live suite built and is free to delete."""
    return SUITE_TIMELINE.match(name) is not None


def sweep_suite_timelines(pool: Pool, project: Project) -> Swept:
    """Delete the previous run's leftovers, so this run builds onto a known project.

    Resolve will not delete the timeline it is sitting on, and says so only by returning
    ``False`` — and a run ends on its own last build, so the current timeline is normally
    one of ours. Moving to a cut the suite did not build is therefore the first step; a
    project holding nothing else has nowhere to move to, and the one left behind is
    reported rather than swallowed.

    One call per timeline, not one batch call: a batch answers ``False`` for the whole
    list, which would leave the sweep unable to say which cut stuck.
    """
    held = timelines_of(project)
    ours = [timeline for timeline in held if is_suite_timeline(name_of(timeline))]
    if not ours:
        return Swept([], [])

    current = project.GetCurrentTimeline()
    if any(same_timeline(current, timeline) for timeline in ours):
        elsewhere = next(
            (timeline for timeline in held if not is_suite_timeline(name_of(timeline))), None
        )
        if elsewhere is not None:
            project.SetCurrentTimeline(elsewhere)

    deleted: list[str] = []
    kept: list[str] = []
    for timeline in ours:
        name = name_of(timeline)
        (deleted if pool.DeleteTimelines([timeline]) else kept).append(name)
    return Swept(deleted, kept)


class ClipGenerationError(RuntimeError):
    """ffmpeg refused to generate the scan clip. Scaffolding, so not a server error."""


HARD_CUT_COLOURS = ("red", "green", "blue", "white")
"""One solid colour per segment, so every boundary is a whole frame changing at once.

Four colours means three cuts. The live assertion is "at least one", so a detector that
misses a boundary still proves the clip-clock mapping rather than turning the tier red.
"""

HARD_CUT_SIZE = "320x240"
"""Small enough that generating, importing and decoding it are all instant."""

HARD_CUT_FPS = 24
HARD_CUT_SECONDS = 1


def hard_cut_argv(
    ffmpeg: str,
    destination: Path,
    colours: Sequence[str] = HARD_CUT_COLOURS,
    fps: int = HARD_CUT_FPS,
    size: str = HARD_CUT_SIZE,
    seconds: int = HARD_CUT_SECONDS,
) -> list[str]:
    """The command that builds a clip whose only content is hard cuts.

    ``-y`` because ffmpeg prompts on an existing output, and a prompt in a test run is a
    hang rather than a failure.
    """
    argv = [ffmpeg, "-y"]
    for colour in colours:
        argv += ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:r={fps}:d={seconds}"]
    streams = "".join(f"[{index}:v]" for index in range(len(colours)))
    argv += [
        "-filter_complex",
        f"{streams}concat=n={len(colours)}:v=1:a=0[cut]",
        "-map",
        "[cut]",
        "-pix_fmt",
        "yuv420p",
        str(destination),
    ]
    return argv


def write_hard_cut_clip(
    destination: Path,
    runner: Runner | None = None,
    ffmpeg: str | None = None,
    colours: Sequence[str] = HARD_CUT_COLOURS,
    fps: int = HARD_CUT_FPS,
    size: str = HARD_CUT_SIZE,
    seconds: int = HARD_CUT_SECONDS,
) -> Path:
    """Generate the clip, raising what says why if ffmpeg will not.

    A missing binary comes back as ``FfmpegUnavailableError`` from ``invoke`` — the same
    shape every other route gets — so the live fixture can skip on it rather than reading
    an exit code.
    """
    binary = ffmpeg if ffmpeg is not None else get_config().ffmpeg
    argv = hard_cut_argv(binary, destination, colours, fps, size, seconds)
    finished = invoke(argv, runner=runner)
    if finished.returncode != 0:
        raise ClipGenerationError(
            f"ffmpeg refused to generate {destination.name} (exit {finished.returncode}): "
            f"{finished.stderr[-STDERR_TAIL:]}"
        )
    return destination
