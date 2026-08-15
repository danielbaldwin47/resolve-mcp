"""The ffmpeg commands the video routes run, and what their refusals mean.

Both are argv lists, never shell strings, and both take the same ``runner`` seam the audio
route takes — the filter expressions below are the part worth testing, and they are testable
without a decoder. Commas inside a filter expression are escaped, because an unescaped one
ends the filter and starts the next: ``min(iw,1568)`` would be read as a filter named
``1568)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import (
    FrameGrabError,
    InvalidRequestError,
    OcclusionScanError,
    ResolveMcpError,
    SceneDetectionError,
)
from ..ffmpeg import Completed, Runner, hwaccels, invoke, refused
from ..logging_config import get_logger

log = get_logger("video")

JPEG_QUALITY = "3"
"""ffmpeg's ``-q:v`` scale, 2 (best) to 31. Three is visually clean at a fraction of 2's size."""

CUDA_FLAGS = ("-hwaccel", "cuda")
"""NVDEC, without ``-hwaccel_output_format cuda``: the frames decode on the card and come
back to system memory, which is the shape every consumer here wants — all of them are numpy
or JPEG passes over pixels, so decoded frames have to land in RAM either way."""

HWACCEL_MODES = ("auto", "cuda", "off")

INTERNAL_FALLBACK = "Failed setup for format"
"""What ffmpeg prints (at warning level, exit 0) when the hardware decoder cannot take the
stream — e.g. NVDEC on 4:2:2 profiles — and it silently finishes the decode in software."""


class Decode(NamedTuple):
    """The decode this box gets: which flags to pass, and the report the record carries.

    ``reason`` is only ever set on a software decode — it is the answer to "why did this
    not use the GPU", which is the question G10 cost a session to ask (#202).
    """

    flags: tuple[str, ...]
    device: str  # "cuda" | "cpu"
    reason: str | None = None

    def report(self) -> dict[str, Any]:
        return {"device": self.device, "reason": self.reason}


_announced: set[tuple[str, str]] = set()
"""Which (executable, device) choices have already been logged, so a session's log says
where decodes run once, not once per frame grab."""


def reset_decode_announcements() -> None:
    _announced.clear()


def choose_decode(config: Config | None = None, runner: Runner | None = None) -> Decode:
    """What ``config.ffmpeg_hwaccel`` means on this box, probed rather than assumed.

    ``auto`` uses NVDEC when the binary lists cuda and degrades to software when it does
    not; ``cuda`` forces the flag so a box that cannot honour it fails loudly; ``off``
    never decodes on the card. Every software choice carries its reason, and each choice
    is logged once per process — a CPU decode that says nothing is the G10 failure.
    """
    config = config or get_config()
    mode = config.ffmpeg_hwaccel
    if mode not in HWACCEL_MODES:
        raise InvalidRequestError(
            cause=f"ffmpeg_hwaccel={mode!r} is not a hardware-decode mode.",
            fix=(
                "Set RESOLVE_MCP_FFMPEG_HWACCEL to auto (probe the binary and use NVDEC "
                "when it lists cuda), cuda (force it), or off (software decode)."
            ),
            detail={"requested": mode, "modes": list(HWACCEL_MODES)},
        )

    if mode == "off":
        choice = Decode((), "cpu", "hardware decode disabled (ffmpeg_hwaccel=off)")
    elif mode == "cuda" or "cuda" in hwaccels(config, runner):
        choice = Decode(CUDA_FLAGS, "cuda")
    else:
        supported = ", ".join(sorted(hwaccels(config, runner))) or "none"
        choice = Decode((), "cpu", f"ffmpeg lists no cuda hwaccel (supported: {supported})")

    marker = (config.ffmpeg, choice.device)
    if marker not in _announced:
        _announced.add(marker)
        if choice.device == "cuda":
            log.info("Video decode on NVDEC (-hwaccel cuda) via %s", config.ffmpeg)
        else:
            log.info("Video decode in software via %s: %s", config.ffmpeg, choice.reason)
    return choice


def _decoded(
    build: Callable[[Sequence[str]], list[str]],
    source: Path | str,
    runner: Runner | None,
    config: Config,
) -> tuple[Completed, dict[str, Any]]:
    """Run a decode with the configured hardware choice, falling back loudly if it fails.

    A hardware decode that exits non-zero is retried once in software before the failure
    is believed: NVDEC refuses codecs it does not know, and the file may be fine. The
    report says the retry happened — a fallback nothing records is the bug this ticket
    exists for — and a file that is truly unreadable fails the software attempt with
    ffmpeg's own message, which is the error worth relaying. Forcing ``ffmpeg_hwaccel=cuda``
    turns the retry off: forcing is a claim about the box, and a box that cannot honour it
    should fail in the caller's face rather than quietly decode in software.
    """
    choice = choose_decode(config, runner)
    finished = invoke(build(choice.flags), runner=runner, config=config)
    report = choice.report()
    if finished.returncode == 0 and choice.flags and INTERNAL_FALLBACK in finished.stderr:
        # The sneakiest fallback of the three: NVDEC lacks the codec profile (this box's
        # 4:2:2 concert footage, measured 2026-08-14), so ffmpeg warns on stderr, decodes
        # in software and exits 0 — the frames are good, but the card never touched them.
        # The exit-code retry below cannot see it; the stderr line is the only witness.
        # Deliberately also reached when ``cuda`` is forced: the frames arrived, so failing
        # would discard good work — here the loudness *is* the record.
        log.warning(
            "Hardware decode of %s fell back inside ffmpeg: the decoder lacks this codec "
            "profile, so the frames were decoded in software",
            source,
        )
        report = Decode(
            (), "cpu", "ffmpeg fell back internally: the hardware decoder lacks this codec profile"
        ).report()
    elif finished.returncode != 0 and choice.flags and config.ffmpeg_hwaccel != "cuda":
        log.warning(
            "Hardware decode of %s failed (exit %d); retrying in software",
            source,
            finished.returncode,
        )
        finished = invoke(build(()), runner=runner, config=config)
        report = Decode((), "cpu", "hardware decode failed; retried in software").report()
    return finished, report


def still_command(
    executable: str,
    source: Path | str,
    target: Path | str,
    seconds: float,
    max_edge: int,
    decode: Sequence[str] = (),
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
        "warning",
        "-y",
        *decode,
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


def scene_command(
    executable: str,
    source: Path | str,
    threshold: float,
    decode: Sequence[str] = (),
) -> list[str]:
    """Decode the whole file, keep the frames that differ from the last one, print their times.

    ``showinfo`` writes one line per kept frame to stderr, which is why the log level goes
    up to info here where the others sit at warning — the floor every decode command keeps
    so ffmpeg's internal hwaccel fallback line stays visible (#202). Nothing is encoded —
    the output is the null muxer, so what this costs is one decode pass.
    """
    return [
        executable,
        "-nostdin",
        "-loglevel",
        "info",
        "-y",
        *decode,
        "-i",
        str(source),
        "-filter:v",
        f"select=gt(scene\\,{threshold}),showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]


def sample_command(
    executable: str,
    source: Path | str,
    target: Path | str,
    start_seconds: float,
    duration_seconds: float,
    rate: float,
    width: int,
    height: int,
    decode: Sequence[str] = (),
) -> list[str]:
    """Seek, take ``rate`` frames a second of the next ``duration_seconds``, write raw grey.

    Raw rather than an image sequence: the occlusion scan reads pixels, and one file of
    ``width * height`` bytes per sample is a numpy reshape rather than a decoder. Grey
    because every part of the heuristic is luma — a colour plane would triple the bytes to
    answer nothing. The scale is forced rather than fitted: the measurement is in fractions
    of frame area, and a fixed grid is what lets the raw file be reshaped without asking
    ffmpeg what shape it wrote.

    The comma in the filter chain separates two filters and is left unescaped; the commas
    that need escaping are the ones *inside* a filter's arguments.
    """
    return [
        executable,
        "-nostdin",
        "-loglevel",
        "warning",
        "-y",
        *decode,
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration_seconds:.6f}",
        "-an",
        "-vf",
        f"fps={rate:g},scale={width}:{height}",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        str(target),
    ]


class Grabbed(NamedTuple):
    path: Path
    decode: dict[str, Any]


class Scanned(NamedTuple):
    printed: str
    decode: dict[str, Any]


class Sampled(NamedTuple):
    path: Path
    decode: dict[str, Any]


def grab(
    source: Path | str,
    target: Path | str,
    seconds: float,
    max_edge: int,
    runner: Runner | None = None,
    config: Config | None = None,
) -> Grabbed:
    """Write one frame of ``source`` to ``target`` as a JPEG, or fail with ffmpeg's message."""
    config = config or get_config()
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    finished, decode = _decoded(
        lambda flags: still_command(config.ffmpeg, source, destination, seconds, max_edge, flags),
        source,
        runner,
        config,
    )

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
    return Grabbed(destination, decode)


def scan(
    source: Path | str,
    threshold: float,
    runner: Runner | None = None,
    config: Config | None = None,
) -> Scanned:
    """Run the scene filter over ``source`` and hand back the log it printed."""
    config = config or get_config()
    finished, decode = _decoded(
        lambda flags: scene_command(config.ffmpeg, source, threshold, flags),
        source,
        runner,
        config,
    )

    if finished.returncode != 0:
        raise refused(source, finished, SceneDetectionError, threshold=threshold)
    log.info("Scanned %s for scene cuts at threshold %.2f", source, threshold)
    return Scanned(finished.stderr, decode)


def sample(
    source: Path | str,
    target: Path | str,
    start_seconds: float,
    duration_seconds: float,
    rate: float,
    width: int,
    height: int,
    runner: Runner | None = None,
    config: Config | None = None,
    failure: type[ResolveMcpError] = OcclusionScanError,
) -> Sampled:
    """Write the sampled frames of a range to ``target`` as raw grey, or say why not.

    An empty file is a failure rather than an empty scan: ffmpeg seeked past the end of a
    file exits zero and writes nothing, and a scan of no frames would otherwise come back
    saying the shot is clean.

    ``failure`` is which refusal a caller wants back. Two scans sample the same way and fail
    the same way, and an agent told its image-quality scan failed for occlusion reasons would
    go looking at the wrong tool's documentation.
    """
    config = config or get_config()
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    finished, decode = _decoded(
        lambda flags: sample_command(
            config.ffmpeg,
            source,
            destination,
            start_seconds,
            duration_seconds,
            rate,
            width,
            height,
            flags,
        ),
        source,
        runner,
        config,
    )

    if finished.returncode != 0:
        raise refused(source, finished, failure, start_seconds=start_seconds)
    if not destination.exists() or destination.stat().st_size < width * height:
        raise failure(
            cause=f"ffmpeg reported success but wrote no frames to {destination}.",
            fix=(
                "A range that starts past the end of the file exits zero and writes nothing. "
                "inspect_clip reports the media bounds the range has to sit inside."
            ),
            detail={
                "source": str(source),
                "start_seconds": start_seconds,
                "duration_seconds": duration_seconds,
                "expected": str(destination),
            },
        )
    log.info(
        "Sampled %s from %.3fs for %.3fs at %g fps to %s",
        source,
        start_seconds,
        duration_seconds,
        rate,
        destination,
    )
    return Sampled(destination, decode)


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
