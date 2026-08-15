"""Which device the torch-backed analysis models infer on, said out loud.

The beat grid (beat_this) and the applause curve (PANNs) run on whatever torch the
analysis extra installed, and since #245 that is a CUDA build: the extra sources torch,
torchaudio and torchcodec from the cu130 wheel index on Windows, where PyPI would hand
back the CPU build. A `+cpu` torch in this venv is therefore the same class of thing as a
`+cpu` separator — a broken install, not a policy — and says so at WARNING.

This module keeps that choice honest rather than silent (#202, the G10 failure): every
inference site announces its build and device once per process, and the jobs that run
these models carry the same note in their records, so a slow run is diagnosable from the
log alone.

Announcing is not enough on its own, because neither model follows torch. beat_this
defaults to ``device="cpu"`` whatever wheel is installed, and PANNs defaults to ``"cuda"``
and falls back silently — so a record saying `cuda` could sit over a grid the CPU
computed. Both inference sites hand the model `inference_device()` rather than let it
pick, which is what makes the note answerable for the run.
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


def inference_device() -> str:
    """``"cuda"`` where torch sees a card, ``"cpu"`` otherwise — what the models are told.

    No torch is ``"cpu"``: the caller is about to import a model that raises its own
    dependency error with a fix in it, and a device string is not the place to report a
    missing stack.
    """
    note = torch_note()
    return "cpu" if note is None else str(note["device"])


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
            # #245: the wheel, not the box. Same shape as the separator's +cpu warning —
            # name the build and the command that replaces it, because "it was slow" is
            # how G10 lasted (#202).
            log.warning(
                "%s inference on the CPU: torch %s is the CPU build. Reinstall the CUDA "
                "wheels into this venv — `uv sync --extra analysis --reinstall-package "
                "torch` on a box with an NVIDIA card (see "
                "docs/reference/compute-device-inventory.md).",
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
