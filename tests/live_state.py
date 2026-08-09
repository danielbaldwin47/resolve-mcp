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

    The timeline Resolve is sitting on is left where it is. Resolve refuses to delete it
    anyway, but the reason it is not moved off first is worse than a refusal: switching
    away and deleting in the same breath is what Resolve Studio 21.0.3 was doing when it
    logged ``Internal undo error 3`` and wrote a crash dump, in the autosave the deletions
    triggered (#135). So the sweep takes what it can reach and reports the rest, and
    :func:`restore_current` is what stops the leftover being permanent — a session that
    ends on the director's own cut leaves nothing current for the next sweep to refuse.

    One call per timeline, not one batch call: a batch answers ``False`` for the whole
    list, which would leave the sweep unable to say which cut stuck.
    """
    ours = [timeline for timeline in timelines_of(project) if is_suite_timeline(name_of(timeline))]
    if not ours:
        return Swept([], [])

    delete = getattr(pool, "DeleteTimelines", None)
    if not callable(delete):
        # A handle Resolve has dropped answers None for every method rather than raising,
        # so an unswept project is what a dying Resolve looks like from here. Reporting it
        # beats a TypeError out of a session fixture, which errors every test at setup and
        # buries the one message that says Resolve is gone.
        return Swept([], [name_of(timeline) for timeline in ours])

    current = project.GetCurrentTimeline()
    deleted: list[str] = []
    kept: list[str] = []
    for timeline in ours:
        name = name_of(timeline)
        if same_timeline(current, timeline):
            kept.append(name)
            continue
        (deleted if delete([timeline]) else kept).append(name)
    return Swept(deleted, kept)


def restore_current(project: Project, timeline: Timeline) -> bool:
    """Leave the project on a cut the suite did not build.

    A run that ends on its own build leaves a timeline the next sweep cannot delete — and
    leaves whoever opens the GUI looking at a smoke test. ``timeline`` is what was open
    when the session started and is normally the answer.

    When the session *found* one of the suite's own cuts open, putting that back would make
    the leftover permanent: it is current, so the sweep skips it, and the run after that
    finds it current again. So any cut the suite did not build is preferred, and the
    leftover goes on the next run instead of surviving every one.
    """
    switch = getattr(project, "SetCurrentTimeline", None)
    if not callable(switch):
        return False
    target = timeline
    if target is None or is_suite_timeline(name_of(target)):
        held = timelines_of(project)
        target = next((one for one in held if not is_suite_timeline(name_of(one))), target)
    if target is None:
        return False
    if same_timeline(project.GetCurrentTimeline(), target):
        return True
    return bool(switch(target))


class ClipGenerationError(RuntimeError):
    """ffmpeg refused to generate the scan clip. Scaffolding, so not a server error."""


class NamedClipMissingError(AssertionError):
    """The env var names a clip the pool does not hold, or cannot decode."""


def named_scan_clip(footage: Sequence[dict[str, Any]], named: str) -> dict[str, Any] | None:
    """The pool entry the scan was pointed at, or ``None`` when it was pointed at nothing.

    An empty or unset variable means "build one" rather than "scan anything", which is the
    whole difference between this and the old "shortest clip in the pool" rule. A variable
    that names a clip the pool cannot offer is a mistake worth stopping on: silently
    generating a clip instead would report a pass for a scan the director never asked for.
    """
    wanted = named.strip()
    if not wanted:
        return None
    for entry in footage:
        if entry["name"] == wanted:
            return entry
    raise NamedClipMissingError(f"{wanted!r} names no decodable pool clip")


HARD_CUT_COLOURS = ("red", "green", "blue", "white")
"""One solid colour per segment, so every boundary is a whole frame changing at once.

Four colours means three cuts. The live assertion is "at least one", so a detector that
misses a boundary still proves the clip-clock mapping rather than turning the tier red.
"""

HARD_CUT_SIZE = "320x240"
"""Small enough that generating, importing and decoding it are all instant."""

HARD_CUT_FPS = 24
HARD_CUT_SECONDS = 1
"""A second a colour: long enough that a cut lands on a frame boundary either way."""


def hard_cut_argv(
    ffmpeg: str,
    destination: Path,
    colours: Sequence[str] = HARD_CUT_COLOURS,
    fps: int = HARD_CUT_FPS,
    size: str = HARD_CUT_SIZE,
) -> list[str]:
    """The command that builds a clip whose only content is hard cuts.

    ``-y`` because ffmpeg prompts on an existing output, and a prompt in a test run is a
    hang rather than a failure.
    """
    argv = [ffmpeg, "-y"]
    for colour in colours:
        argv += ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:r={fps}:d={HARD_CUT_SECONDS}"]
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
) -> Path:
    """Generate the clip, raising what says why if ffmpeg will not.

    A missing binary comes back as ``FfmpegUnavailableError`` from ``invoke`` — the same
    shape every other route gets — so the live fixture can skip on it rather than reading
    an exit code.
    """
    binary = ffmpeg if ffmpeg is not None else get_config().ffmpeg
    argv = hard_cut_argv(binary, destination, colours, fps, size)
    finished = invoke(argv, runner=runner)
    if finished.returncode != 0:
        raise ClipGenerationError(
            f"ffmpeg refused to generate {destination.name} (exit {finished.returncode}): "
            f"{finished.stderr[-STDERR_TAIL:]}"
        )
    return destination
