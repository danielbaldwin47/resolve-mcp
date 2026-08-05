"""The per-source-clip route: pull audio straight off the file Resolve points at.

This never asks Resolve to do anything — the clip's File Path is a path on disk, and
ffmpeg reads it far faster than a render queue would, in parallel with whatever the
director is doing in the GUI. What it cannot see is anything Resolve does to that audio
(track mapping, levels, a linked file); ``acquire`` checks for that before choosing this
route.

The subprocess call is a parameter (``runner``) so that command construction, failure
shaping and the missing-binary case are all testable without ffmpeg installed — while the
real thing stays one plain ``subprocess.run``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from ..config import Config, get_config
from ..errors import AudioExtractionError, FfmpegUnavailableError, InvalidRequestError
from ..logging_config import get_logger

log = get_logger("audio")

CODECS = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}
STDERR_TAIL = 800


class Completed(NamedTuple):
    """What the runner reports back: ffmpeg says why it refused on stderr."""

    returncode: int
    stderr: str


Runner = Callable[[Sequence[str]], Completed]


def command(
    executable: str,
    source: Path | str,
    target: Path | str,
    sample_rate: int,
    bit_depth: int,
) -> list[str]:
    """The ffmpeg invocation, as a list — never a shell string.

    ``-map 0:a:0`` takes the first audio stream only. A clip whose audio is spread across
    several streams is not this route's job; the timeline route captures the mix Resolve
    makes of them.
    """
    codec = CODECS.get(bit_depth)
    if codec is None:
        raise InvalidRequestError(
            cause=f"{bit_depth} is not a WAV bit depth this server writes.",
            fix=f"Use one of {', '.join(str(depth) for depth in sorted(CODECS))}.",
            detail={"requested": bit_depth, "supported": sorted(CODECS)},
        )
    return [
        executable,
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-acodec",
        codec,
        "-ar",
        str(sample_rate),
        str(target),
    ]


def _run(argv: Sequence[str]) -> Completed:
    finished = subprocess.run(argv, capture_output=True, text=True, check=False)
    return Completed(finished.returncode, finished.stderr or "")


def extract(
    source: Path | str,
    target: Path | str,
    sample_rate: int,
    bit_depth: int,
    runner: Runner | None = None,
    config: Config | None = None,
) -> Path:
    """Write the clip's audio to ``target`` as a WAV, or fail with ffmpeg's own message."""
    config = config or get_config()
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = command(config.ffmpeg, source, destination, sample_rate, bit_depth)

    try:
        finished = (runner or _run)(argv)
    except FileNotFoundError as exc:
        raise FfmpegUnavailableError(
            cause=f"No ffmpeg at {config.ffmpeg!r}.",
            detail={"executable": config.ffmpeg},
        ) from exc

    if finished.returncode != 0:
        raise AudioExtractionError(
            cause=f"ffmpeg refused {Path(source).name} (exit {finished.returncode}).",
            detail={
                "source": str(source),
                "exit_code": finished.returncode,
                "stderr": finished.stderr[-STDERR_TAIL:],
            },
        )
    if not destination.exists():
        raise AudioExtractionError(
            cause=f"ffmpeg reported success but wrote nothing to {destination}.",
            detail={"expected": str(destination)},
        )
    log.info("Extracted audio from %s to %s", source, destination)
    return destination
