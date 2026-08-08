"""Driving Resolve's render queue: add a job, start it, watch it, take it back off.

The queue is a global, stateful part of the application — settings are set on the project,
not passed to a call — so three things here are decisions rather than API calls:

* **The queue is left as it was found.** Every job this server adds is deleted once it has
  finished, whatever the outcome. The director's own queue is theirs; an export for
  analysis must not silently accumulate entries in it.

* **Status is polled, never assumed.** ``StartRendering`` returns as soon as the job is
  accepted. The only truthful completion signal is the job's own status going to Complete,
  and a Failed or Cancelled status is a failure even though every call returned True.

* **A job that never starts is a different failure from a slow one.** Resolve's render
  engine can wedge such that every job it is handed sits at "Ready for background render"
  at 0% forever — deleting the queue does not clear it, only restarting Resolve does — and
  from the outside that is identical to a long render (#92, live on Studio 21.0.3.7). The
  signal that separates them is a status transition, not a duration: a healthy job leaves
  the queue in seconds. So starting has a deadline of its own, far shorter than the
  render's, and blowing it says the engine is wedged rather than guessing at the GUI.

* **A completed render still has to have written the file.** Resolve reports success for
  renders that land nothing on disk (an unwritable target directory does this). The caller
  passes the path it expects, and its absence is a failure, not a cache entry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import RenderPresetNotFoundError, RenderQueueError, ResolveMcpError
from ..logging_config import get_logger

log = get_logger("render")

Project = Any

COMPLETE = "Complete"
FAILED = ("Failed", "Cancelled")
QUEUED = ("Ready", "Ready for background render")
"""Statuses that say the job is accepted but not running — the only ones the start deadline
may refuse on.

Deliberately a whitelist of readings that *succeeded*. A status this build spells
differently, and an unreported one (the way a dying handle and an unsupported build both
read), are not "queued": they cost the render timeout rather than a false refusal, because
a re-render is the worse trade against a wedge that at least announces itself by never
finishing."""
POLL_SECONDS = 1.0
RENDER_TIMEOUT = 3600.0
START_TIMEOUT = 60.0
"""How long a job may sit queued before the engine is called wedged (#92).

Deliberately generous: the observed healthy transition out of the queue is under 10s, so a
minute leaves room for a loaded machine, and the cost of being wrong is a re-render."""

WEDGED_FIX = (
    "Restart Resolve — its render engine can get stuck so that every job it is given sits "
    "queued forever, and clearing the render queue does not clear it. If that is not it, "
    "check the Deliver page for a modal dialog holding the queue, then retry."
)
"""Both known causes of a job that never starts, likeliest first (#92).

The order is what was observed twice live: in #88's wedge and #92's, no dialog was open, and
only a restart cleared it. Naming the dialog alone sends the reader to a GUI with nothing to
show them."""

RESTORE_THE_PRESET_FIX = (
    "Restore the stock {preset!r} preset in this Resolve — it is the only route to this "
    "format on builds that refuse the format/codec pair directly."
)
"""What to tell a caller that never chose the preset name: the worker hardcodes it, so the
install is what is wrong, not the request."""

PRESET_UNUSABLE_FIX = (
    "Restore the stock {preset!r} preset in this Resolve, or open the Deliver page and see "
    "what it selects — nothing else names the output this render needs."
)

UNKNOWN_FORMAT = "unknown"
"""What 21.0.3.7 answers for the current format after ``Audio Only`` loads — and it renders a
WAV regardless (live). A reading of it says the build will not say, not that it disagrees."""

STATUS = "JobStatus"
PERCENT = "CompletionPercentage"

SINGLE_CLIP = 1
"""One file for the span rendered. The other mode writes a file per clip on the timeline."""


def presets(project: Project) -> list[str]:
    """Every render preset this project offers, in the order Resolve lists them."""
    listed = project.GetRenderPresetList() or []
    return [str(one) for one in listed]


def require_preset(project: Project, name: str) -> list[str]:
    """Check the project has this preset, and hand back the list it was checked against.

    Resolve answers a bare ``False`` both for a preset it does not have and for one it will
    not load, so the name is checked against the list to tell the two apart. Reading the
    list disturbs nothing, which is why a caller can ask this before queuing anything.
    """
    available = presets(project)
    if name not in available:
        raise RenderPresetNotFoundError(name, available)
    return available


def load_preset(project: Project, name: str) -> None:
    """Make ``name`` the settings the next job captures — format, codec, resolution and all.

    A preset is the only honest way to name a deliverable's shape: format and codec alone
    leave resolution, bit rate and the rest at whatever the project was last set to, and
    what those *should* be is the director's decision, made once in the Deliver page.
    """
    available = require_preset(project, name)
    if not project.LoadRenderPreset(name):
        raise RenderQueueError(
            cause=f"Resolve would not load the render preset {name!r}.",
            fix=(
                "Open the Deliver page and select the preset by hand — a preset that will "
                "not load is usually one saved against media or a codec this machine lacks."
            ),
            detail={"preset": name, "available": available},
        )
    log.info("Loaded render preset %s", name)


def current_format(project: Project) -> dict[str, str]:
    """The format and codec the project would render with now — ``format`` is the extension.

    Empty when Resolve will not say: a deliverable whose suffix cannot be known is a
    failure the caller has to shape, not a default this layer is allowed to guess.
    """
    reading = project.GetCurrentRenderFormatAndCodec() or {}
    return {str(key): str(value) for key, value in reading.items() if value}


def submit(
    project: Project,
    settings: dict[str, Any],
    format_and_codec: tuple[str, str] | None = None,
    preset: str | None = None,
) -> str:
    """Push one job onto the queue and return its id.

    ``SetRenderSettings``, the format/codec pair and a loaded preset are all project-level
    state; they are set immediately before the job is added so that the job captures them.
    The pair travels as one because Resolve takes it as one, and both selectors are optional
    because a caller that has already loaded a preset itself (the deliver route) has both
    set — setting them again would overwrite the rest of what that preset chose.

    Given both, ``preset`` is the route tried first and the pair is what is left when the
    preset is unusable. That order is the way round it is because of the build this runs on:
    Resolve 21.0.3 lists Wave among its render formats, returns an empty codec map for it,
    and refuses every ("wav", …) pair, so audio is reachable only through the stock preset
    that already selects it (#32, live) — asking for the pair first spent one guaranteed
    failure on every export there (#131). The pair stays as the fallback for the builds
    where it works, including an install whose stock preset was renamed or deleted. The
    settings are applied after the preset either way, so the caller's target directory and
    name still win.

    Going first costs the preset something the fallback ordering never had to pay: a preset
    carries a *whole* render config, so on a build that would have taken the pair, loading it
    replaces settings the caller never asked to change. That is the price of the swap and it
    is accepted — ``Audio Only`` is the shape this render wants anyway, the caller's settings
    are re-applied over it, and the alternative is the guaranteed-failing call on the build
    that is actually supported.

    What is *not* accepted is a preset quietly rendering something else. Only a reading that
    succeeded may say so: ``GetCurrentRenderFormatAndCodec`` answers ``{"format": "unknown"}``
    after ``Audio Only`` loads on 21.0.3.7 and renders a WAV regardless (live), so an
    unreadable format leaves the preset in charge — a check that treated it as disagreement
    would reject the one route that works. A readable format that differs demotes the preset
    to the pair, because a customised preset otherwise beats an explicit request silently and
    the job lands a file under a name that says it is something it is not.
    """
    _select_output(project, format_and_codec, preset)
    if not project.SetRenderSettings(settings):
        raise RenderQueueError(
            cause="Resolve refused the render settings.",
            detail={"settings": settings},
        )
    job_id = project.AddRenderJob()
    if not job_id:
        raise RenderQueueError(cause="Resolve would not add the job to the render queue.")
    log.info("Queued render job %s", job_id)
    return str(job_id)


def _select_output(
    project: Project,
    format_and_codec: tuple[str, str] | None,
    preset: str | None,
) -> None:
    """Put the project on the asked-for output: by preset where this build takes one, else pair.

    This is where ``submit``'s ordering lives, and its docstring is where the reasons are.
    Nothing is selected when the caller named neither — the deliver route loaded its own
    preset before calling and would have this overwrite it.
    """
    refusal: str | None = None
    detail: dict[str, Any] = {}
    if preset is not None:
        refusal, detail = _preset_refusal(project, preset, format_and_codec)
        if refusal is None:
            return
    if format_and_codec is None:
        if refusal is None:
            return
        # Nothing else names the output, and queuing anyway would render whatever the
        # project was last set to — a file the caller never asked for.
        raise RenderQueueError(
            cause=f"Resolve would not render through the {preset!r} preset: {refusal}",
            fix=PRESET_UNUSABLE_FIX.format(preset=preset),
            detail={**detail, "preset": preset},
        )
    format_, codec = format_and_codec
    if project.SetCurrentRenderFormatAndCodec(format_, codec):
        if refusal is not None:
            log.info(
                "Not rendering through the %r preset (%s) — asking Resolve for %s/%s directly",
                preset,
                refusal,
                format_,
                codec,
            )
        return
    if refusal is None:
        raise RenderQueueError(
            cause=f"Resolve would not render {format_}/{codec}.",
            detail={"format": format_, "codec": codec, "preset": None},
        )
    raise RenderQueueError(
        cause=(
            f"Resolve would not render {format_}/{codec}, and the {preset!r} preset is no "
            f"route to it either: {refusal}"
        ),
        # Not the underlying error's fix: that one tells the caller to re-spell the preset,
        # and the caller never chose this name — the worker hardcodes it. What is actually
        # wrong is the install.
        fix=RESTORE_THE_PRESET_FIX.format(preset=preset),
        detail={**detail, "format": format_, "codec": codec, "preset": preset},
    )


def _preset_refusal(
    project: Project,
    preset: str,
    format_and_codec: tuple[str, str] | None,
) -> tuple[str | None, dict[str, Any]]:
    """Load ``preset``; answer why it is no route to the asked-for format, ``None`` if it is.

    Loading is how the question is asked — a preset that will not load, and one that loads
    and selects something else, are both "no route", and only the attempt tells them apart.

    The detail returned alongside carries what presets do exist, which is the one fact that
    identifies a renamed or localised install.
    """
    # load_preset, not LoadRenderPreset: a bare False means both "no such preset" and
    # "will not load", and the two want different answers from the caller.
    try:
        load_preset(project, preset)
    except ResolveMcpError as exc:
        return exc.cause, exc.detail
    if format_and_codec is None:
        return None, {}
    selected = current_format(project).get("format", "")
    # Only a reading that *succeeded* may demote the preset. The build this ordering exists
    # for answers "unknown" here after ``Audio Only`` loads and then renders a WAV anyway
    # (live, 21.0.3.7), so treating an unreadable format as disagreement would reject the one
    # route that works. A readable format that differs is another matter: a preset customised
    # to render something else would otherwise silently beat an explicit request, and the job
    # would land a file under a name that says it is something it is not.
    if selected.casefold() in ("", UNKNOWN_FORMAT):
        return None, {}
    wanted = format_and_codec[0]
    if selected.casefold() == wanted.casefold():
        return None, {}
    return (
        f"it renders {selected}, not {wanted}",
        {"selected": selected},
    )


def render(
    project: Project,
    job_id: str,
    expecting: Path,
    progress: Callable[[float, str], None] | None = None,
    poll: float = POLL_SECONDS,
    timeout: float = RENDER_TIMEOUT,
    start_timeout: float = START_TIMEOUT,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Start the job, watch it to completion, and hand back the file it wrote.

    Two deadlines, because two different failures wear the same face. ``start_timeout``
    covers a job that never leaves the queue — Resolve's render engine can wedge such that
    every job it is given sits at "Ready for background render" at 0% forever — and
    ``timeout`` covers one that is genuinely running and taking too long.

    ``now`` and ``sleep`` are parameters so the polling loop is testable without a test
    that actually waits.
    """
    if not project.StartRendering(job_id):
        _remove(project, job_id)
        raise RenderQueueError(
            cause="Resolve would not start the render.",
            detail={"render_job_id": job_id},
        )
    try:
        # Keyword, not positional: timeout and start_timeout are adjacent floats, and
        # swapping them would typecheck and quietly hand the caller the wrong diagnosis.
        _watch(
            project,
            job_id,
            progress,
            poll=poll,
            timeout=timeout,
            start_timeout=start_timeout,
            now=now,
            sleep=sleep,
        )
    finally:
        _remove(project, job_id)

    if not expecting.exists():
        raise RenderQueueError(
            cause=f"The render reported success but wrote nothing to {expecting}.",
            detail={"expected": str(expecting)},
        )
    log.info("Render job %s wrote %s", job_id, expecting)
    return expecting


def _watch(
    project: Project,
    job_id: str,
    progress: Callable[[float, str], None] | None,
    poll: float,
    timeout: float,
    start_timeout: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Poll the job until it completes, fails, or blows one of the two deadlines.

    A job that has not left ``QUEUED`` has not started, and a wedged engine leaves it there
    for as long as anyone cares to wait — the distinguishing signal is the transition, not
    the duration, so it is worth a deadline of its own (#92, live on 21.0.3.7).
    """
    began_at = now()
    has_started = False
    while True:
        reading = project.GetRenderJobStatus(job_id) or {}
        status = str(reading.get(STATUS, ""))
        percent = _percent(reading)
        if progress is not None:
            progress(percent, f"rendering ({int(percent * 100)}%)")
        if status == COMPLETE:
            return
        if status in FAILED:
            raise RenderQueueError(
                cause=f"The render job ended {status}.",
                detail={"render_job_id": job_id, "status": reading},
            )
        waited = now() - began_at
        # Three states, not two: a reading that is neither QUEUED nor empty is the job
        # running, and an empty one is a reading that failed — which neither starts the
        # clock's other half nor is allowed to refuse.
        if not has_started and status and status not in QUEUED:
            has_started = True
            log.info("Render job %s started rendering after %.0fs (%s)", job_id, waited, status)
        if not has_started and status in QUEUED and waited > start_timeout:
            raise RenderQueueError(
                cause=(
                    f"Resolve accepted the render job and never started it: it was still "
                    f"{status!r} after {start_timeout:.0f}s, at {int(percent * 100)}%. A job "
                    f"that has not left the queue by now is not a slow render — nothing is "
                    f"running it."
                ),
                fix=WEDGED_FIX,
                detail={
                    "render_job_id": job_id,
                    "start_timeout_seconds": start_timeout,
                    "status": reading,
                },
            )
        if waited > timeout:
            raise RenderQueueError(
                cause=f"The render job was still {status or 'unreported'} after {timeout:.0f}s.",
                fix=(
                    # Which advice is true here depends on whether the job was ever seen
                    # running: a caller may set the two deadlines such that this one lands
                    # first, and a job that never started wants the wedge advice, not a
                    # confident "it did start".
                    (
                        "The job did start, so this is a render that ran long or stalled "
                        "mid-way. Check the Deliver page — a modal dialog in the Resolve "
                        "GUI also stalls a running queue. Cancel the job there, then retry."
                    )
                    if has_started
                    else WEDGED_FIX
                ),
                detail={
                    "render_job_id": job_id,
                    "timeout_seconds": timeout,
                    "started": has_started,
                },
            )
        sleep(poll)


def _percent(reading: dict[str, Any]) -> float:
    try:
        return min(max(float(reading.get(PERCENT, 0)) / 100.0, 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _remove(project: Project, job_id: str) -> None:
    """Take the job back off the queue. Failing to is not worth failing the render over."""
    try:
        project.DeleteRenderJob(job_id)
    except Exception:  # noqa: BLE001 - a queue we could not tidy is not a lost render
        log.warning("Could not remove render job %s from the queue", job_id, exc_info=True)
