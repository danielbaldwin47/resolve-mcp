"""Deliverables: a render preset plus a span of one timeline, run as a background job.

A concert set is one timeline and a dozen files. That shape drives every decision here:

* **A preset names the deliverable's shape, and nothing else does.** Format and codec on
  their own leave resolution, bit rate and colour at whatever the project was last set to.
  What those should be is the director's call, made once in the Deliver page and saved; this
  server picks a saved preset by name and overrides only where the file goes and which
  frames it covers. That is also why the spec never said what a preset *contains* — the
  settings belong to the project, not to the server. Which presets there are is the fourth
  decision below.

* **A range is the same half-open ``[in, out)`` as everywhere else, on the timeline's own
  clock.** The numbers ``inspect_timeline`` and ``list_markers`` report are the numbers to
  pass, so marking a song's boundaries and rendering it are the same two numbers. Resolve's
  ``MarkOut`` is inclusive, so the conversion happens here, once — the one frame of
  difference is invisible in review and wrong in the file.

* **The file is replaced only where the server owns the directory.** Resolve does not
  reliably overwrite; a name already taken can come back as ``name_0.mp4`` beside the old
  file, and the job would then report a path holding yesterday's export. So the server's own
  render directory is cleared before rendering, and a directory the caller named is refused
  unless ``refresh`` says otherwise. A deliverable the director already sent out is not this
  server's to overwrite on a guess.

* **The catalog is one changeable default plus explicitly named extras** (#71). The default
  is ``config.default_render_preset``, shipped as the Resolve built-in ``H.265 Master`` and
  overridable like any other key, so "render this" means render with the preset the server
  knows and a name in the call is the override. What comes out of here are streaming uploads
  — YouTube, Instagram — rendered per song off one concert timeline; there is no standing
  review or master role for a preset to fill, and no per-platform roles either: a vertical
  Instagram deliverable is a timeline and a cut, not a preset. Extra presets (a future review
  preset, ``h.265 NVIDIA``) are names the director saves in the GUI and passes explicitly.

  This catalog records a name and a one-line purpose, never a settings table: presets are GUI
  state the director owns and re-tunes as the methodology moves, and a copy of their settings
  here would be a second version of the truth that nothing updates. For the same reason the
  cut file never names a preset — delivery format is a render-time parameter, so one cut can
  go out to two platforms without being rewritten.

Caching is the timeline fingerprint plus these parameters, so a re-render of an unchanged
song is instant and a cut that moved renders again — see ``resolve.timeline.fingerprint``
for what that reading can and cannot see.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config, get_config
from .errors import InvalidRequestError, RenderQueueError, RenderTargetExistsError
from .jobs import cache
from .jobs.runner import JobOutput, Progress, band, start_job
from .logging_config import get_logger
from .naming import slug
from .resolve import render
from .resolve.connection import ResolveConnection
from .resolve.session import current_project, frame_rate
from .resolve.timeline import Reader, current_timeline, find_timeline, fingerprint
from .timing import dual_time, to_frames

log = get_logger("deliver")

Timeline = Any
Project = Any

KIND = "render_timeline"

DELIVER_TIMEOUT = 6 * 3600.0
"""A concert render legitimately runs for hours; the timeout is for a queue that has stalled.

The audio route's hour is right for a mix of one timeline. A 4K master of a two-hour set is
not a stalled render at ninety minutes, and killing it would waste the whole thing.
"""

RENDER_FLOOR = 0.05
RENDER_CEILING = 0.98


def list_presets(connection: ResolveConnection) -> dict[str, Any]:
    """The render presets this project offers, and the format it would render with now."""
    project = current_project(connection, "No project is open, so there are no render presets.")
    found = render.presets(project)
    return {
        "presets": found,
        "count": len(found),
        "current": render.current_format(project) or None,
    }


def render_timeline(
    connection: ResolveConnection,
    preset: str | None = None,
    timeline: str | None = None,
    name: str | None = None,
    target_dir: str | None = None,
    start: Any = None,
    end: Any = None,
    refresh: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job that renders a timeline, or a span of one. Returns the job record.

    Everything that can be checked without touching the render queue is checked here, before
    a job exists: a preset that is not in the project, a range that runs off the end of the
    timeline, a file already sitting where this one would land. Those are the caller's
    mistakes, and a raised error names them a poll earlier than a failed job would.

    Omitting ``preset`` renders with ``config.default_render_preset``. A default the project
    does not have refuses exactly like a name that was typed out: there is no second preset
    to fall back to, only a wrong-shaped file that would go out under a right-sounding name.
    """
    config = config or get_config()
    project = current_project(connection, "No project is open, so there is nothing to render.")
    found = find_timeline(project, timeline)
    timeline_name = str(found.GetName() or "timeline")
    fps = frame_rate(project, found)
    span = _span(start, end, found, fps)

    using = config.default_render_preset if preset is None else preset

    # Reading the preset list is cheap and does not disturb the queue; loading it is what
    # changes project state, and that waits for the job's turn under the Resolve lock.
    render.require_preset(project, using)

    covered = span or (int(found.GetStartFrame()), int(found.GetEndFrame()))
    stem = _stem(name, timeline_name, span)
    directory = Path(target_dir) if target_dir is not None else config.render_dir
    params: dict[str, Any] = {
        "timeline": timeline_name,
        "preset": using,
        "preset_source": "default" if preset is None else "explicit",
        "name": stem,
        "target_dir": str(directory),
        "whole_timeline": span is None,
        "start": covered[0],
        "end": covered[1],
        "fps": fps,
    }

    # How the preset was chosen is reported, not keyed on: it does not change a frame of the
    # file, and a concert render costs minutes to hours. Keying on it would re-render the
    # whole song the first time a caller spelled out the name it had been defaulting to.
    keyed = {field: value for field, value in params.items() if field != "preset_source"}
    key = cache.cache_key(KIND, [fingerprint(Reader(connection), found)], keyed)

    # The lookup start_job is about to make, made a moment early: a cache hit renders
    # nothing, so it is the one case where a file already at the target is this job's own
    # and not something to refuse over.
    if target_dir is not None and not refresh and cache.lookup(key, config) is None:
        _refuse_a_taken_target(directory, stem)

    def work(progress: Progress) -> JobOutput:
        return render_deliverable(project, found, params, progress)

    return start_job(
        KIND,
        params,
        work,
        cache_key=key,
        touches_resolve=True,
        refresh=refresh,
        config=config,
    )


def render_deliverable(
    project: Project,
    timeline: Timeline,
    params: dict[str, Any],
    progress: Progress,
) -> JobOutput:
    """The worker: load the preset, queue the span, wait for the file it writes.

    Everything it needs is in ``params``, which is also what the job record shows: a render
    that has to be diagnosed from a job file on disk is one whose inputs are all in it.
    """
    directory = Path(str(params["target_dir"]))
    directory.mkdir(parents=True, exist_ok=True)
    stem = str(params["name"])

    progress(0.02, "loading the render preset")
    with current_timeline(project, timeline):
        render.load_preset(project, str(params["preset"]))
        format_and_codec = render.current_format(project)
        extension = format_and_codec.get("format", "")
        if not extension:
            raise RenderQueueError(
                cause=f"Resolve would not say what format {params['preset']!r} renders.",
                fix=(
                    "Open the Deliver page and load the preset by hand to see what it does; "
                    "a preset that reports no format usually did not load."
                ),
                detail={"preset": params["preset"]},
            )
        expecting = directory / f"{stem}.{extension}"
        _clear(expecting)

        project.SetCurrentRenderMode(render.SINGLE_CLIP)
        progress(0.04, "queuing the render")
        job_id = render.submit(project, _settings(directory, stem, params))
        render.render(
            project,
            job_id,
            expecting,
            band(progress, RENDER_FLOOR, RENDER_CEILING),
            timeout=DELIVER_TIMEOUT,
        )

    log.info("Rendered %s from %s", expecting, params["timeline"])
    return JobOutput(_result(expecting, format_and_codec, params), (expecting,))


def _settings(directory: Path, stem: str, params: dict[str, Any]) -> dict[str, Any]:
    """Where the file goes and which frames it covers — the preset decides the rest.

    Nothing else is set: forcing ``ExportVideo``/``ExportAudio`` here would silently
    contradict an audio-only or video-only preset the director saved deliberately.
    """
    settings: dict[str, Any] = {"TargetDir": str(directory), "CustomName": stem}
    if params["whole_timeline"]:
        settings["SelectAllFrames"] = True
        return settings
    settings["SelectAllFrames"] = False
    settings["MarkIn"] = int(params["start"])
    # Half-open in, inclusive out: MarkOut names the last frame Resolve renders.
    settings["MarkOut"] = int(params["end"]) - 1
    return settings


def _result(
    expecting: Path,
    format_and_codec: dict[str, str],
    params: dict[str, Any],
) -> dict[str, Any]:
    """What the agent reads off a finished render — where the file is, and what it covers.

    The range is reported whether or not one was asked for: a whole-timeline render covers
    the timeline's own bounds, and a deliverable that will not say what it holds is one the
    agent has to open Resolve to identify.

    ``preset_source`` says whether that preset was named in the call or came from the config
    default, so a file that came out the wrong shape leads back to the thing that chose it
    rather than to a guess about which of the two was in play.
    """
    fps = params["fps"]
    start = int(params["start"])
    end = int(params["end"])
    return {
        "path": str(expecting),
        "size_bytes": expecting.stat().st_size,
        "timeline": params["timeline"],
        "preset": params["preset"],
        "preset_source": params["preset_source"],
        "format": format_and_codec.get("format"),
        "codec": format_and_codec.get("codec"),
        "whole_timeline": params["whole_timeline"],
        "range": {
            "start": dual_time(start, fps),
            "end": dual_time(end, fps),
            "duration": dual_time(end - start, fps),
        },
    }


def _span(
    start: Any,
    end: Any,
    timeline: Timeline,
    fps: float | None,
) -> tuple[int, int] | None:
    """The half-open frame range to render, or ``None`` for the whole timeline.

    One bound alone runs to the timeline's own edge — a song at the top or the tail of a set
    is named by the boundary the agent found, not by the one it would have to look up.
    """
    if start is None and end is None:
        return None
    first = int(timeline.GetStartFrame())
    last = int(timeline.GetEndFrame())
    in_frame = to_frames(start, fps, "start")
    out_frame = to_frames(end, fps, "end")
    in_frame = first if in_frame is None else in_frame
    out_frame = last if out_frame is None else out_frame

    bounds = {"timeline_start": first, "timeline_end": last, "start": in_frame, "end": out_frame}
    if in_frame < first or out_frame > last:
        raise InvalidRequestError(
            cause=(
                f"The range [{in_frame}, {out_frame}) is not inside the timeline, "
                f"which runs [{first}, {last})."
            ),
            fix=(
                "Timeline frames are numbered from the timeline's own start, not from zero "
                "— inspect_timeline reports the bounds this range has to sit inside."
            ),
            detail=bounds,
        )
    if out_frame <= in_frame:
        raise InvalidRequestError(
            cause=f"The range [{in_frame}, {out_frame}) covers no frames.",
            fix="Ranges are half-open [start, end): end must be past start.",
            detail=bounds,
        )
    return in_frame, out_frame


def _stem(name: str | None, timeline_name: str, span: tuple[int, int] | None) -> str:
    """The deliverable's filename, without a suffix.

    A name the caller gave is kept as given (made filename-safe) — this is a file a human
    opens, so a cache-shaped name would be the wrong thing to hand over. Without one, a
    range is distinguished by its own frames so that two songs off one timeline do not both
    claim the timeline's name.
    """
    if name is not None:
        return slug(name, "render")
    base = slug(timeline_name, "render")
    return base if span is None else f"{base}-{span[0]}-{span[1]}"


def _refuse_a_taken_target(directory: Path, stem: str) -> None:
    """A caller-named directory is the director's; nothing in it is replaced on a guess.

    Which suffix the render will take is not known until the preset loads, so the check is
    on the name: any file called ``<name>.<anything>`` is close enough to be worth asking
    about, including a sidecar a re-render would leave describing the wrong file.
    """
    found = sorted(str(path) for path in directory.glob(f"{stem}.*") if path.is_file())
    if found:
        raise RenderTargetExistsError(
            cause=f"{found[0]} is already there, and this render would land on it.",
            detail={"target_dir": str(directory), "name": stem, "found": found},
        )


def _clear(expecting: Path) -> None:
    """Take the old file out of the way so Resolve cannot rename around it.

    Exactly the one file about to be written, never a glob: a sidecar the director keeps
    beside a deliverable — a subtitle, a still, an earlier take in another format — is not
    what this render replaces, even when it carries the same name.

    Only reached once the render is going ahead: either the directory is the server's own,
    or ``refresh`` said to replace what is in the caller's.
    """
    if expecting.exists():
        expecting.unlink()
        log.info("Replacing %s", expecting)


