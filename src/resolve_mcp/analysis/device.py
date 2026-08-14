"""Which device the torch-backed analysis models infer on, said out loud.

The beat grid (beat_this) and the applause curve (PANNs) run on whatever torch the
analysis extra installed. On Windows that is the CPU wheel **by design**: the measured
corpus — the numbers the style profiles were learned from — came off the CPU build, and a
GPU build that produced a different beat grid would silently change what those profiles
mean (see docs/reference/compute-device-inventory.md and the pins in pyproject.toml).

This module keeps that choice honest rather than silent (#202, the G10 failure): every
inference site announces its build and device once per process, and the jobs that run
these models carry the same note in their records, so a slow run is diagnosable from the
log alone.
"""

from __future__ import annotations

from typing import Any

from ..logging_config import get_logger

log = get_logger("analysis")

_announced: set[str] = set()


def torch_note() -> dict[str, Any] | None:
    """The installed torch build and the device inference lands on — ``None`` with no torch.

    ``None`` rather than an error: the callers attach this to results whose own imports
    already fail with the right message when the model stack is missing, and a note about
    a stack that is not there would only shadow that error.
    """
    try:
        import torch  # noqa: PLC0415 - a torch stack, loaded only when a job asks
    except ImportError:
        return None
    cuda = bool(torch.cuda.is_available())
    return {
        "torch": str(torch.__version__),
        "cuda_available": cuda,
        "device": "cuda" if cuda else "cpu",
    }


def announce(component: str) -> dict[str, Any] | None:
    """Log which device ``component`` infers on, once per process, and return the note."""
    note = torch_note()
    if note is None:
        return None
    if component not in _announced:
        _announced.add(component)
        if note["device"] == "cuda":
            log.info("%s inference on CUDA (torch %s)", component, note["torch"])
        elif "+cpu" in note["torch"]:
            log.info(
                "%s inference on the CPU (torch %s): the CPU build is the corpus policy — "
                "see docs/reference/compute-device-inventory.md",
                component,
                note["torch"],
            )
        else:
            log.warning(
                "%s inference on the CPU: torch %s cannot see a CUDA device",
                component,
                note["torch"],
            )
    return note


def reset_announcements() -> None:
    """Forget what was logged, so a test hears the announcement again."""
    _announced.clear()
