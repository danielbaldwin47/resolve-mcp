"""Occlusion scanning: which stretches of an angle have something standing in front of it.

The gap this closes is a real one, and it cost a cut its verdict: three of the shots in a
concert edit were half-blocked by an audience head, a hat and a back, and nothing in this
server could have told the builder before it cut them. Every other measurement here says
what the *music* is doing; this one says whether the picture is worth cutting to at all.

Like a scene scan it decodes, so it is a job and it is cached against the media. Unlike a
scene scan it decodes a **range** at about a frame a second: a blocker takes a second or two
to wipe through, so a sample a second finds it, while decoding every frame of a 4K master to
find it would cost minutes to say the same thing. The arithmetic is ``blocking``; what lives
here is the range, the sampling, the spans and what comes back inline.

The answer the agent actually wants is the *windows* — the stretches to keep a cut out of —
so those come back inline. The per-sample curve goes to disk beside them, because a builder
that has to justify skipping a favourite shot needs the number, and a reviewer that disagrees
needs to see where the score sat rather than being told a verdict.

Every window carries a **kind**: ``obstruction`` for the ones to keep a cut out of, ``scene``
for the near-field mass the shot was framed to include. The score alone could not tell them
apart — it flagged six windows on the round it was tuned against and two were real — so the
classing is a second reading, ``blocking``'s ``novel`` and ``hidden``. Nothing is filtered out
on the way: a scene window that vanished would be indistinguishable from a stretch that was
never flagged, and the builder that disagrees with a class is the reason the curve is on disk.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..config import Config, get_config
from ..errors import InvalidRequestError, OcclusionScanError
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from ..timing import SECONDS_PRECISION, dual_time
from . import ffmpeg
from .sampled import readable_range, runs, sample_frame, sample_step
from .source import Source, locate

if TYPE_CHECKING:  # pragma: no cover - the worker imports this when it runs
    from .blocking import Scan

log = get_logger("video")

KIND = "analyze_occlusion"

DEFAULT_SAMPLE_FPS = 1.0
"""Samples a second. A near-field blocker takes a second or more to cross the frame, so this
finds one; going finer multiplies decode time to sharpen an edge nobody cuts on."""

MIN_SAMPLE_FPS = 0.2
MAX_SAMPLE_FPS = 4.0

DEFAULT_THRESHOLD = 0.35
"""Score at or above which a sample is called blocked. Around a tenth of the frame lost to a
dark, textureless, bottom-anchored blob — the point where a head stops being background."""

MAX_RANGE_SECONDS = 3600.0
"""An hour of samples in one scan. Past that this is a survey of the whole card, and the
answer wanted is per-song."""

GAP_SAMPLES = 1
"""How many clean samples a window tolerates before it is two windows. One, because a head
that bobs out of frame for a beat has not made the shot usable."""

INLINE_WINDOWS = 8
"""How many windows the gist carries, worst first. The file on disk has the rest."""

DISCRIMINATOR = {
    "classes": {
        "obstruction": (
            "something in the near field is covering a player the shot is framed on — the "
            "stretch to keep a cut out of"
        ),
        "scene": (
            "near-field mass the shot was framed to include: furniture, a parked audience "
            "head, the shot's own foreground subject, or the same furniture moved by a "
            "mid-take reframe. Flagged by the score, and not a reason to drop the shot"
        ),
    },
    "bounds": (
        "an obstruction window spans the samples that scored, not the body's own in and out: "
        "the detector loses a body once it stops moving, so a crossing can outlast its window "
        "by a second or more at either end"
    ),
}
"""What the classes mean and what they cannot say, carried on every result.

The limit is stated rather than implied because it is the one that costs an edit: a builder
reading a 0.96 s window as 'the body is there for 0.96 s' cuts into the second after it. The
gauntlet's one adjudicated crossing ran 3.7 s and scored across 0.96 of them (#189)."""


def analyze_occlusion(
    connection: ResolveConnection,
    clip: str,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    start: Any = None,
    end: Any = None,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    threshold: float = DEFAULT_THRESHOLD,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job scoring near-field obstruction across a range of a clip. Returns the record.

    ``start`` and ``end`` are dual time in the clip's own frame numbering, half-open, and
    default to the clip's whole media. ``runner`` is the subprocess seam.
    """
    config = config or get_config()
    source = locate(connection, clip, bin, OcclusionScanError)
    rate = _readable_rate(sample_fps)
    level = _readable_threshold(threshold)
    first, last = readable_range(start, end, source, MAX_RANGE_SECONDS, "an occlusion scan")

    params = {
        "clip": clip,
        "bin": source.bin_path,
        "start": first,
        "end": last,
        "sample_fps": rate,
        "threshold": level,
    }
    key = cache.cache_key(KIND, [cache.fingerprint(source.path)], params)

    def work(progress: Progress) -> JobOutput:
        return scan_occlusion(source, key, params, progress, config, runner)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def scan_occlusion(
    source: Source,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    config: Config | None = None,
    runner: Runner | None = None,
) -> JobOutput:
    """The worker: one sampled decode, then the curve on disk and the windows for the agent."""
    from . import blocking  # noqa: PLC0415 - numpy and scipy load here, not at server startup

    config = config or get_config()
    fps = source.fps or 0.0
    first = int(params["start"])
    last = int(params["end"])
    rate = float(params["sample_fps"])
    level = float(params["threshold"])
    duration = (last - first) / fps

    stem = f"{slug(source.name, 'occlusion')}-{key[:12]}"
    # The catalog is named for the key, because that is the artifact a cache hit points at.
    # The grey is named for the *run*: two scans of the same clip and range share a key, and
    # ffmpeg's -y would have the second truncate the first's file while the first was reading
    # it — a scan that answers CLEAN for footage it never decoded.
    raw = config.analysis_dir / f"{stem}-{os.getpid()}-{uuid4().hex[:8]}.gray"
    progress(0.1, f"decoding {duration:.0f}s of {source.name} at {rate:g} samples a second")
    try:
        decoded = ffmpeg.sample(
            source.path,
            raw,
            start_seconds=source.seek_seconds(first),
            duration_seconds=duration,
            rate=rate,
            width=blocking.GRID_WIDTH,
            height=blocking.GRID_HEIGHT,
            runner=runner,
            config=config,
        )
        progress(0.6, "scoring the samples for near-field blocking")
        frames = blocking.read_grid(raw.read_bytes())
        scan = blocking.measure(frames)
    finally:
        # The grey file is scratch: the catalog is the artifact, and a cache hit must not
        # depend on tens of megabytes of intermediate that nothing reads again.
        raw.unlink(missing_ok=True)

    progress(0.9, "assembling the unusable windows")
    samples = _samples(scan, first, rate, source)
    windows = _windows(samples, level, first, last, rate, source)
    target = config.analysis_dir / f"{stem}.occlusion.json"
    catalog = {
        "clip": source.name,
        "bin": source.bin_path,
        "source": source.path,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fps": source.fps,
        "sample_fps": rate,
        "threshold": level,
        # Which device decoded the range, and why not the GPU when it was the CPU (#202).
        "decode": decoded.decode,
        "grid": {"width": blocking.GRID_WIDTH, "height": blocking.GRID_HEIGHT},
        # What this shot covers even unobstructed: the scan's own floor, subtracted from
        # every score. A high baseline is worth seeing — it means the framing itself is tight.
        "baseline": scan.baseline,
        "range": {"in": dual_time(first, source.fps), "out": dual_time(last, source.fps)},
        "samples": samples,
        "windows": windows,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    log.info(
        "Scored %d occlusion sample(s) of %s, %d window(s), %d an obstruction, to %s",
        len(samples),
        source.name,
        len(windows),
        sum(1 for one in windows if one["kind"] == blocking.OBSTRUCTION),
        target,
    )
    return JobOutput(_gist(catalog, target, samples, windows), (target,))


def _gist(
    catalog: dict[str, Any],
    target: Path,
    samples: list[dict[str, Any]],
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """What comes back inline: the windows to avoid, and how bad the range is overall."""
    from . import blocking  # noqa: PLC0415 - numpy and scipy load here, not at server startup

    scores = [float(one["score"]) for one in samples]
    blocked = [one for one in samples if float(one["score"]) >= float(catalog["threshold"])]
    worst = max(samples, key=lambda one: float(one["score"])) if samples else None
    obstructions = [one for one in windows if one["kind"] == blocking.OBSTRUCTION]
    # Obstructions before scene, worst first inside each: the inline budget is small and a
    # veto the builder has to honour must never be crowded out by furniture it can ignore.
    ranked = sorted(
        windows,
        key=lambda one: (one["kind"] == blocking.OBSTRUCTION, float(one["peak_score"])),
        reverse=True,
    )
    return {
        "path": str(target),
        "clip": catalog["clip"],
        "range": catalog["range"],
        "sample_fps": catalog["sample_fps"],
        "threshold": catalog["threshold"],
        "decode": catalog["decode"],
        "baseline": catalog["baseline"],
        "samples": len(samples),
        "blocked_samples": len(blocked),
        "blocked_fraction": _rounded(len(blocked) / len(samples)) if samples else 0.0,
        "windows": len(windows),
        "obstructions": len(obstructions),
        "unusable_seconds": _rounded(sum(float(one["duration_seconds"]) for one in obstructions)),
        "discriminator": DISCRIMINATOR,
        "worst_windows": ranked[:INLINE_WINDOWS],
        "worst_sample": worst,
        "score": {
            "mean": _rounded(statistics.fmean(scores)) if scores else None,
            "max": _rounded(max(scores)) if scores else None,
        },
    }


def _samples(scan: Scan, first: int, rate: float, source: Source) -> list[dict[str, Any]]:
    """Every reading against the clip's own frame numbering.

    The sample times are derived from the rate rather than read back out of ffmpeg: the
    ``fps`` filter emits frames on an exact grid from the seek point, so the nth sample is
    the nth interval, and a decoder that dropped one would shift every later time.
    """
    return [
        {
            "time": dual_time(sample_frame(index, first, rate, source), source.fps),
            "score": reading.score,
            "coverage": reading.coverage,
            "largest": reading.largest,
            "blobs": reading.blobs,
            # What separates a body from the shot's own furniture. The score does not: a real
            # blocking has read below a drummer's arm in the same 90 seconds (#189).
            "novel": reading.novel,
            "hidden": reading.hidden,
        }
        for index, reading in enumerate(scan.readings)
    ]


def _windows(
    samples: list[dict[str, Any]],
    threshold: float,
    first: int,
    last: int,
    rate: float,
    source: Source,
) -> list[dict[str, Any]]:
    """The stretches to keep a cut out of: runs of blocked samples, one clean sample tolerated.

    A window runs to one sample interval past its last blocked sample — the sample stands for
    the second it was taken from, not for an instant — clipped to the range that was scanned.

    Both ends are clipped, and a window that clips to nothing is not published. The ``fps``
    filter can emit a boundary frame dated at the range's own end: it is a real reading and it
    stays in the curve, but the window it would make starts where the range stops. A
    zero-length or backwards window in the catalog is a stretch no cut can be kept out of.

    Every window is published, classed rather than filtered: a builder that disagrees with the
    discriminator needs to see the stretch it decided was scene, and a window that vanished
    would look exactly like a stretch that was never flagged.
    """
    from . import blocking  # noqa: PLC0415 - numpy and scipy load here, not at server startup

    found = runs([float(one["score"]) >= threshold for one in samples], GAP_SAMPLES)
    step = sample_step(rate, source)
    windows: list[dict[str, Any]] = []
    for begin, stop in found:
        run = samples[begin:stop]
        scores = [float(one["score"]) for one in run]
        novel = max(float(one["novel"]) for one in run)
        hidden = max(float(one["hidden"]) for one in run)
        start_frame = max(first, min(last, sample_frame(begin, first, rate, source)))
        end_frame = min(last, sample_frame(stop - 1, first, rate, source) + step)
        if end_frame <= start_frame:
            continue
        windows.append(
            {
                "in": dual_time(start_frame, source.fps),
                "out": dual_time(end_frame, source.fps),
                "duration_frames": end_frame - start_frame,
                "duration_seconds": _rounded((end_frame - start_frame) / (source.fps or 1.0)),
                "samples": stop - begin,
                "peak_score": _rounded(max(scores)),
                "mean_score": _rounded(statistics.fmean(scores)),
                # The window is classed on its worst sample, the way it is scored on it: one
                # second of a body crossing is what makes the stretch unusable, and the
                # seconds either side of it are the ones a cut would land on.
                "kind": blocking.verdict(novel, hidden),
                "peak_novel": _rounded(novel),
                "peak_hidden": _rounded(hidden),
            }
        )
    return windows


def _rounded(seconds: float) -> float:
    return round(seconds, SECONDS_PRECISION)


def _readable_rate(sample_fps: float) -> float:
    if not MIN_SAMPLE_FPS <= sample_fps <= MAX_SAMPLE_FPS:
        raise InvalidRequestError(
            cause=f"sample_fps={sample_fps} is not a sampling rate this scan runs at.",
            fix=(
                f"Ask for between {MIN_SAMPLE_FPS} and {MAX_SAMPLE_FPS} samples a second. "
                f"{DEFAULT_SAMPLE_FPS} is the default — a blocker takes a second or more to "
                "cross the frame, so finer sampling costs decode time and finds the same "
                "windows."
            ),
            detail={
                "requested": sample_fps,
                "minimum": MIN_SAMPLE_FPS,
                "maximum": MAX_SAMPLE_FPS,
            },
        )
    return float(sample_fps)


def _readable_threshold(threshold: float) -> float:
    if not 0.0 < threshold <= 1.0:
        raise InvalidRequestError(
            cause=f"threshold={threshold} is not an obstruction score.",
            fix=(
                "Pass a threshold above 0 and at most 1.0 — it is how much of the frame has "
                f"to be blocked before a sample is unusable. {DEFAULT_THRESHOLD} is the "
                "default; lower it to be warned about partial blocking."
            ),
            detail={"requested": threshold, "maximum": 1.0},
        )
    return float(threshold)
