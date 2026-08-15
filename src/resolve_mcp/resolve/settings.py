"""Timeline settings the server writes, and the string-typed API they go through.

Resolve's ``GetSetting``/``SetSetting`` are one untyped pair over every setting a timeline
has: values go in as strings and come back as strings, ``1920`` and ``"1920"`` are not the
same argument, and the return value is the usual Resolve "true" — an answer about whether
the call was understood, not about whether anything changed.

Resolution is the one setting a build has to write, because a timeline is created at the
project's default and the cut states what it is *for* (gauntlet G13, #187). Two things make
it more than a single call:

* **A timeline ignores its own resolution until it owns its settings.** ``useCustomSettings``
  is what detaches a timeline from the project's, and the resolution keys are inert while it
  is off — the write is taken and the timeline stays 4K. So the flag goes first, always
  (established live on 21.0.3: ``gauntlet/recon/r3_reso.py``).
* **The read-back is the evidence.** Every write here is followed by a read of what the
  timeline now says, parsed back from the strings it answers in, and a value that did not
  land fails the build. A render is the only other place the number would show up, by which
  point the file exists and is the wrong size.
"""

from __future__ import annotations

from typing import Any, Final

from ..cut.resolution import Resolution
from ..errors import BuildFailedError
from ..logging_config import get_logger

log = get_logger("timeline")

Timeline = Any

CUSTOM_SETTINGS: Final = "useCustomSettings"
"""Whether the timeline holds its own settings rather than the project's. ``"1"`` is on."""

WIDTH: Final = "timelineResolutionWidth"
HEIGHT: Final = "timelineResolutionHeight"
"""The two halves of the frame, each written and read as a string of pixels."""

ON: Final = "1"
"""What ``useCustomSettings`` takes — the API's boolean, spelled as the string it wants."""


def read_resolution(timeline: Timeline) -> Resolution | None:
    """What the timeline says it is, or ``None`` if it will not say.

    Both sides or neither: half a frame size is not a reading, and a caller reporting one
    would be stating a delivery that no timeline has. The same type the cut file states, so
    the comparison below is a value comparison rather than two dicts agreeing by accident.
    """
    width = _setting_int(timeline, WIDTH)
    height = _setting_int(timeline, HEIGHT)
    if width is None or height is None:
        return None
    return Resolution(width=width, height=height)


def apply_resolution(timeline: Timeline, resolution: Resolution, name: str) -> None:
    """Put ``timeline`` on the cut's stated frame size, and prove it took.

    Raises :class:`BuildFailedError` when the timeline reads back as anything other than
    what was asked for — including when it will not say what it is at all. A build that
    carried on here would deliver a timeline at the project's default and report the
    resolution the cut asked for, which is the one outcome worse than not offering the
    setting.
    """
    _write(timeline, CUSTOM_SETTINGS, ON, name)
    _write(timeline, WIDTH, str(resolution.width), name)
    _write(timeline, HEIGHT, str(resolution.height), name)

    landed = read_resolution(timeline)
    if landed != resolution:
        raise BuildFailedError(
            cause=(
                f"Resolve would not put {name!r} on {resolution.width}x{resolution.height}: "
                f"it reads back as {_described(landed)}."
            ),
            fix="Check the timeline's own settings in Resolve (Timeline > Timeline "
            "Settings), or drop 'timeline.resolution' from the cut file to build at the "
            "project's default.",
            detail={
                "timeline": name,
                "requested": resolution.as_dict(),
                "reported": landed.as_dict() if landed is not None else None,
            },
        )
    log.info("%s is on %dx%d (custom timeline settings)", name, resolution.width, resolution.height)


def _write(timeline: Timeline, key: str, value: str, name: str) -> None:
    """One ``SetSetting``. A refusal is logged, never raised — the read-back is the judge.

    The return value says the call was understood, not that the timeline changed: writing a
    resolution key with ``useCustomSettings`` off answers the same as writing it with the
    flag on. So a False is a log line rather than a failure, and what fails the build is the
    number the timeline reads back with.
    """
    try:
        taken = timeline.SetSetting(key, value)
    except Exception as exc:  # noqa: BLE001 - a bridge error here is a build failure, not a crash
        raise BuildFailedError(
            cause=f"Resolve failed while setting {key} on {name!r}: {exc}.",
            detail={"timeline": name, "setting": key, "value": value},
        ) from exc
    if not taken:
        log.warning("Resolve answered false to %s=%s on %s", key, value, name)


def _setting_int(timeline: Timeline, key: str) -> int | None:
    """One setting as the number it means, through the string the API answers in."""
    try:
        value = timeline.GetSetting(key)
    except Exception:  # noqa: BLE001 - an unreadable setting is "will not say", not a failure
        log.debug("Unreadable %s", key)
        return None
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        log.debug("Unparseable %s: %r", key, value)
        return None


def _described(landed: Resolution | None) -> str:
    return "nothing" if landed is None else f"{landed.width}x{landed.height}"


__all__ = ["CUSTOM_SETTINGS", "HEIGHT", "ON", "WIDTH", "apply_resolution", "read_resolution"]
