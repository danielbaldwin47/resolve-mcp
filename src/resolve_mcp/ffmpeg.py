"""The one place this server shells out to ffmpeg.

Audio extraction, frame grabs and scene scans are three different commands with three
different failures, but they run the same way: an argv list — never a shell string — handed
to a ``Runner``. The runner is a parameter everywhere so that command construction and
failure shaping stay testable on a machine with no ffmpeg on it, while the real path stays
one plain ``subprocess.run``.

A missing binary is the one failure that means the same thing to every route, so it is
shaped here. What a non-zero exit means is the caller's to say: ffmpeg refusing to decode a
file is an extraction failure to the audio route and a grab failure to the video one, and
the agent needs the fix that belongs to what it asked for.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from typing import NamedTuple

from .config import Config, get_config
from .errors import FfmpegUnavailableError

STDERR_TAIL = 800
"""How much of ffmpeg's own complaint travels back in a failure — the end, where it says why."""


class Completed(NamedTuple):
    """What the runner reports back: ffmpeg says why it refused on stderr."""

    returncode: int
    stderr: str


Runner = Callable[[Sequence[str]], Completed]


def run(argv: Sequence[str]) -> Completed:
    finished = subprocess.run(argv, capture_output=True, text=True, check=False)
    return Completed(finished.returncode, finished.stderr or "")


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
