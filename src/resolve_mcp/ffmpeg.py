"""The one place this server shells out to ffmpeg.

Audio extraction, frame grabs and scene scans are three different commands with three
different failures, but they run the same way: an argv list — never a shell string — handed
to a ``Runner``. The runner is a parameter everywhere so that command construction and
failure shaping stay testable on a machine with no ffmpeg on it, while the real path stays
one plain ``subprocess.run``.

A missing binary is the one failure that means the same thing to every route, so it is
shaped here. So is a refusal — ffmpeg's exit code and its own complaint read the same way
whatever was asked for — but *what* the refusal means is the caller's to name: the same
unreadable file is an extraction failure to the audio route and a grab failure to the video
one, and the agent needs the fix that belongs to what it asked for.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from .config import Config, get_config
from .errors import FfmpegUnavailableError, ResolveMcpError

STDERR_TAIL = 800
"""How much of ffmpeg's own complaint travels back in a failure — the end, where it says why."""


class Completed(NamedTuple):
    """What the runner reports back: ffmpeg says why it refused on stderr.

    ``stdout`` is empty for every decode and encode — ffmpeg talks on stderr — and carries
    the answer for the one query that prints one: the ``-hwaccels`` capability probe.
    """

    returncode: int
    stderr: str
    stdout: str = ""


Runner = Callable[[Sequence[str]], Completed]


def run(argv: Sequence[str]) -> Completed:
    finished = subprocess.run(argv, capture_output=True, text=True, check=False)
    return Completed(finished.returncode, finished.stderr or "", finished.stdout or "")


def invoke(
    argv: Sequence[str],
    runner: Runner | None = None,
    config: Config | None = None,
) -> Completed:
    """Run ffmpeg, turning the missing binary into the error that names the fix."""
    config = config or get_config()
    try:
        return (runner or run)(argv)
    except FileNotFoundError as exc:
        raise FfmpegUnavailableError(
            cause=f"No ffmpeg at {config.ffmpeg!r}.",
            detail={"executable": config.ffmpeg},
        ) from exc


def hwaccels_command(executable: str) -> list[str]:
    """Ask the binary which hardware decoders it was built with. Prints one name a line."""
    return [executable, "-hide_banner", "-hwaccels"]


_hwaccel_probes: dict[str, frozenset[str]] = {}


def hwaccels(config: Config | None = None, runner: Runner | None = None) -> frozenset[str]:
    """The hardware decode methods this box's ffmpeg supports, probed once per process.

    A probe that fails reads as an empty set rather than an error: the video routes degrade
    to software decode, and the decode report says why. Only a *missing* binary still
    raises — every route was about to hit the same wall, and the fix is the same one.
    """
    config = config or get_config()
    cached = _hwaccel_probes.get(config.ffmpeg)
    if cached is not None:
        return cached

    finished = invoke(hwaccels_command(config.ffmpeg), runner=runner, config=config)
    lines = (line.strip() for line in finished.stdout.splitlines())
    found = frozenset(
        line for line in lines if line and ":" not in line and finished.returncode == 0
    )
    _hwaccel_probes[config.ffmpeg] = found
    return found


def reset_hwaccel_probe() -> None:
    """Forget the probe, so a test (or a config change) asks again."""
    _hwaccel_probes.clear()


def refused(
    source: Path | str,
    finished: Completed,
    failure: type[ResolveMcpError],
    **detail: Any,
) -> ResolveMcpError:
    """A non-zero exit as ``failure``, carrying the end of what ffmpeg said about it."""
    return failure(
        cause=f"ffmpeg refused {Path(source).name} (exit {finished.returncode}).",
        detail={
            "source": str(source),
            "exit_code": finished.returncode,
            "stderr": finished.stderr[-STDERR_TAIL:],
            **detail,
        },
    )
