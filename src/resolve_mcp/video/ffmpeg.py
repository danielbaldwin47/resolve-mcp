"""The two ffmpeg commands the video routes run, and what their refusals mean.

Both are argv lists, never shell strings, and both take the same ``runner`` seam the audio
route takes — the filter expressions below are the part worth testing, and they are testable
without a decoder. Commas inside a filter expression are escaped, because an unescaped one
ends the filter and starts the next: ``min(iw,1568)`` would be read as a filter named
``1568)``.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config, get_config
from ..errors import FrameGrabError, SceneDetectionError
from ..ffmpeg import Runner, invoke, refused
from ..logging_config import get_logger

log = get_logger("video")

JPEG_QUALITY = "3"
"""ffmpeg's ``-q:v`` scale, 2 (best) to 31. Three is visually clean at a fraction of 2's size."""


def still_command(
    executable: str,
    source: Path | str,
    target: Path | str,
    seconds: float,
    max_edge: int,
) -> list[str]:
    """Seek, take one frame, scale it to fit inside ``max_edge``, write a JPEG.

    ``-ss`` before ``-i`` seeks the input rather than decoding up to the moment, which is
    the difference between a grab that is instant on a concert master and one that is not;
    ffmpeg has decoded to the exact frame from an input seek since 2.1, so it costs nothing
    in accuracy. ``force_original_aspect_ratio=decrease`` is what keeps a source already
    inside the cap at its own size instead of blowing it up.
    """
    fit = f"min(iw\\,{max_edge})"
    tall = f"min(ih\\,{max_edge})"
    return [
        executable,
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{seconds:.6f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale=w={fit}:h={tall}:force_original_aspect_ratio=decrease",
        "-q:v",
        JPEG_QUALITY,
        str(target),
    ]


def scene_command(executable: str, source: Path | str, threshold: float) -> list[str]:
    """Decode the whole file, keep the frames that differ from the last one, print their times.

    ``showinfo`` writes one line per kept frame to stderr, which is why the log level goes
    up to info for this command alone. Nothing is encoded — the output is the null muxer, so
    what this costs is one decode pass.
    """
    return [
        executable,
        "-nostdin",
        "-loglevel",
        "info",
        "-y",
        "-i",
        str(source),
        "-filter:v",
        f"select=gt(scene\\,{threshold}),showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]


def grab(
    source: Path | str,
    target: Path | str,
    seconds: float,
    max_edge: int,
    runner: Runner | None = None,
    config: Config | None = None,
) -> Path:
    """Write one frame of ``source`` to ``target`` as a JPEG, or fail with ffmpeg's message."""
    config = config or get_config()
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = still_command(config.ffmpeg, source, destination, seconds, max_edge)
    finished = invoke(argv, runner=runner, config=config)

    if finished.returncode != 0:
        raise refused(source, finished, FrameGrabError, seconds=seconds)
    if not destination.exists():
        raise FrameGrabError(
            cause=f"ffmpeg reported success but wrote nothing to {destination}.",
            fix=(
                "A seek past the end of the file exits zero and writes no frame. "
                "inspect_clip reports the media bounds this grab has to sit inside."
            ),
            detail={"source": str(source), "seconds": seconds, "expected": str(destination)},
        )
    log.info("Grabbed a frame of %s at %.3fs to %s", source, seconds, destination)
    return destination


def scan(
    source: Path | str,
    threshold: float,
    runner: Runner | None = None,
    config: Config | None = None,
) -> str:
    """Run the scene filter over ``source`` and hand back the log it printed."""
    config = config or get_config()
    argv = scene_command(config.ffmpeg, source, threshold)
    finished = invoke(argv, runner=runner, config=config)

    if finished.returncode != 0:
        raise refused(source, finished, SceneDetectionError, threshold=threshold)
    log.info("Scanned %s for scene cuts at threshold %.2f", source, threshold)
    return finished.stderr


def selected_seconds(log_text: str) -> list[float]:
    """The ``pts_time`` of every frame ``showinfo`` reported, in the order it reported them."""
    times: list[float] = []
    for line in log_text.splitlines():
        marker = line.find("pts_time:")
        if marker < 0:
            continue
        reading = _leading_number(line[marker + len("pts_time:") :])
        if reading is not None:
            times.append(reading)
    return times


def _leading_number(text: str) -> float | None:
    fields = text.split()
    try:
        return float(fields[0]) if fields else None
    except ValueError:
        return None
