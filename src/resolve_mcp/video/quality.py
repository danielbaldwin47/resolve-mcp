"""Image-quality scanning: whether the picture an angle got is worth cutting to.

The sibling of the occlusion scan, and the same shape. That one asks whether anything is
standing in front of the camera; this one asks whether what the camera got is soft, blown out
or shaky. Both are the same kind of answer — a stretch of an angle to keep a cut out of —
and both are decisions a builder can otherwise only make by eye, one contact sheet at a time.

Like a scene scan it decodes, so it is a job and it is cached against the media. Unlike the
occlusion scan it samples several times a second: three of the four readings would survive a
sample a second, and the fourth would not. Stability is measured *between* neighbouring
frames, and a sample a second says nothing about a wobble that takes half of one — a camera
resampled that coarsely reads as either locked off or panning, whichever way it happened to
be leaning when the shutter opened. Sampling costs almost nothing next to the decode itself:
ffmpeg decodes every frame of the range either way and the ``fps`` filter drops the rest.

The arithmetic is ``picture``; what lives here is the range, the sampling, the floors, the
windows and what comes back inline. The windows are the answer the agent wants — the
stretches to keep a cut out of, each saying *which* floor it missed — so those come back
inline, and the per-sample curve goes to disk beside them for the builder who has to justify
skipping a favourite take.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..config import Config, get_config
from ..errors import InvalidRequestError, QualityScanError
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
    from .picture import Floors, Reading

log = get_logger("video")

KIND = "analyze_quality"

DEFAULT_SAMPLE_FPS = 4.0
"""Samples a second. Enough that a handheld sway shows up as a change of direction between
neighbouring samples rather than as one long drift; fine enough sampling to catch a true
micro-jitter would need the whole frame rate and answer the same question about the takes
anybody is choosing between."""

MIN_SAMPLE_FPS = 0.5
MAX_SAMPLE_FPS = 12.0

DEFAULT_MIN_SHARPNESS = 0.40
DEFAULT_MAX_CLIPPED = 0.015
DEFAULT_MIN_STABILITY = 0.75
"""What makes a sample unusable: soft, blown, or moving in a way nothing about the shot
predicted. All three are derived rather than chosen, by one rule — each sits midway between
the delivered corpus's 5th percentile and the median of the mildest version of that failure
applied to the same footage, moved to the neighbouring grid point that vetoes less of the
corpus (0.05 for the two 0-to-1 readings, 0.005 for clipping, which lives near zero).

Percentiles rather than extremes on both sides: the tails of a 3600-sample scan are whip pans
and strobe frames, and a floor set on those is a floor set on the estimator's worst moment.
What the rule buys, measured: not one sample of the 3600 is vetoed by any of the three, while
a one-sigma defocus is caught in full, a stop-and-a-half of overexposure 94% of the time and a
1%-of-frame wobble 99.9%. The whole sweep either side — including what each candidate floor
would have let past — is in `gauntlet/recon/image_quality_calib.json`, read in
`docs/reference/image-quality-calibration.md`."""

MAX_RANGE_SECONDS = 900.0
"""Fifteen minutes of samples in one scan: this answers 'is this angle usable through this
song', and a song is what it is sized for."""

MAX_SAMPLES = 2400
"""How many frames one scan holds at once. The grey is read whole and measured whole, so this
is the memory ceiling in disguise: 2400 samples of the quality grid is about 140 MB of raw
grey. A range that needs more is a survey, and the answer wanted from a survey is per-song."""

GAP_SAMPLES = 1
"""How many clean samples a window tolerates before it is two windows — one, as with
occlusion: a shot that comes good for a quarter of a second has not come good."""

INLINE_WINDOWS = 8
"""How many windows the gist carries, worst first. The file on disk has the rest."""


def analyze_quality(
    connection: ResolveConnection,
    clip: str,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    start: Any = None,
    end: Any = None,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    min_sharpness: float = DEFAULT_MIN_SHARPNESS,
    max_clipped: float = DEFAULT_MAX_CLIPPED,
    min_stability: float = DEFAULT_MIN_STABILITY,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job scoring image quality across a range of a clip. Returns the record.

    ``start`` and ``end`` are dual time in the clip's own frame numbering, half-open, and
    default to the clip's whole media. The three floors are what makes a sample unusable, and
    they are separate numbers rather than one score on purpose: a style rule that refuses to
    cut to a blown-out shot is a different rule from one that refuses a shaky one, and a
    builder ranking takes wants to know which of the two it is looking at. ``runner`` is the
    subprocess seam.
    """
    config = config or get_config()
    source = locate(connection, clip, bin, QualityScanError)
    rate = _readable_rate(sample_fps)
    floors = _readable_floors(min_sharpness, max_clipped, min_stability)
    first, last = readable_range(start, end, source, MAX_RANGE_SECONDS, "a quality scan")
    _readable_length(first, last, source, rate)

    params = {
        "clip": clip,
        "bin": source.bin_path,
        "start": first,
        "end": last,
        "sample_fps": rate,
        "min_sharpness": floors.sharpness,
        "max_clipped": floors.clipped,
        "min_stability": floors.stability,
    }
    key = cache.cache_key(KIND, [cache.fingerprint(source.path)], params)

    def work(progress: Progress) -> JobOutput:
        return scan_quality(source, key, params, progress, config, runner)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def scan_quality(
    source: Source,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    config: Config | None = None,
    runner: Runner | None = None,
) -> JobOutput:
    """The worker: one sampled decode, then the curve on disk and the windows for the agent."""
    from . import picture  # noqa: PLC0415 - numpy and scipy load here, not at server startup

    config = config or get_config()
    fps = source.fps or 0.0
    first = int(params["start"])
    last = int(params["end"])
    rate = float(params["sample_fps"])
    floors = picture.Floors(
        sharpness=float(params["min_sharpness"]),
        clipped=float(params["max_clipped"]),
        stability=float(params["min_stability"]),
    )
    duration = (last - first) / fps

    stem = f"{slug(source.name, 'quality')}-{key[:12]}"
    # Named the way the occlusion scratch is, and for the same reason: the catalog is named
    # for the key because that is what a cache hit points at, while the grey is named for the
    # run, so two scans of one range cannot have ffmpeg's -y truncate each other's file.
    raw = config.analysis_dir / f"{stem}-{os.getpid()}-{uuid4().hex[:8]}.gray"
    progress(0.1, f"decoding {duration:.0f}s of {source.name} at {rate:g} samples a second")
    try:
        sampled = ffmpeg.sample(
            source.path,
            raw,
            start_seconds=source.seek_seconds(first),
            duration_seconds=duration,
            rate=rate,
            width=picture.GRID_WIDTH,
            height=picture.GRID_HEIGHT,
            runner=runner,
            config=config,
            failure=QualityScanError,
        )
        progress(0.6, "scoring the samples for focus, exposure and stability")
        frames = picture.read_grid(raw.read_bytes())
        scan = picture.measure(frames)
    finally:
        # Scratch: the catalog is the artifact, and a cache hit must not depend on tens of
        # megabytes of intermediate nothing reads again.
        raw.unlink(missing_ok=True)

    progress(0.9, "assembling the unusable windows")
    # The verdicts are taken here, where ``picture`` is already imported, rather than inside
    # each helper: numpy and scipy load with that module, and the worker is the one place in
    # this file that has paid for them.
    verdicts = [picture.failures(one, floors) for one in scan.readings]
    whole = picture.summarize(list(scan.readings), floors)
    samples = _samples(scan.readings, verdicts, floors, first, rate, source)
    windows = _windows(samples, first, last, rate, source)
    target = config.analysis_dir / f"{stem}.quality.json"
    catalog = {
        "clip": source.name,
        "bin": source.bin_path,
        "source": source.path,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fps": source.fps,
        "sample_fps": rate,
        # Named for the arguments that set them rather than for the readings they bound: this
        # header is read back by a builder deciding what to pass next, and by
        # ``analysis/correlate`` deciding which shots missed the rule this scan actually ran.
        "floors": {
            "min_sharpness": floors.sharpness,
            "max_clipped": floors.clipped,
            "min_stability": floors.stability,
        },
        # Which device decoded the range, and why not the GPU when it was the CPU (#202).
        "decode": sampled.decode,
        "grid": {"width": picture.GRID_WIDTH, "height": picture.GRID_HEIGHT},
        "range": {"in": dual_time(first, source.fps), "out": dual_time(last, source.fps)},
        "samples": samples,
        "windows": windows,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    log.info(
        "Scored %d quality sample(s) of %s, %d unusable window(s), to %s",
        len(samples),
        source.name,
        len(windows),
        target,
    )
    return JobOutput(_gist(catalog, target, whole, samples, windows), (target,))


def _gist(
    catalog: dict[str, Any],
    target: Path,
    whole: dict[str, Any],
    samples: list[dict[str, Any]],
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """What comes back inline: the windows to avoid, and how the range reads overall."""
    unusable = [one for one in samples if one["reasons"]]
    ranked = sorted(windows, key=lambda one: float(one["severity"]), reverse=True)
    worst = min(samples, key=_severity) if samples else None
    return {
        "path": str(target),
        "clip": catalog["clip"],
        "range": catalog["range"],
        "sample_fps": catalog["sample_fps"],
        "floors": catalog["floors"],
        "decode": catalog["decode"],
        "samples": len(samples),
        "unusable_samples": len(unusable),
        "unusable_fraction": _rounded(len(unusable) / len(samples)) if samples else 0.0,
        # The whole range as one block — what a builder ranking two angles reads first.
        "quality": whole,
        "windows": len(windows),
        "worst_windows": ranked[:INLINE_WINDOWS],
        "worst_sample": worst,
    }


def _samples(
    readings: tuple[Reading, ...],
    verdicts: list[tuple[str, ...]],
    floors: Floors,
    first: int,
    rate: float,
    source: Source,
) -> list[dict[str, Any]]:
    """Every reading against the clip's own frame numbering.

    Sample times are derived from the rate rather than read back out of ffmpeg, as in the
    occlusion scan: the ``fps`` filter emits frames on an exact grid from the seek point.

    ``t`` is carried beside the dual time, in seconds, because this catalog is joined against
    other clocks — a scan of a render of a cut is joined onto that cut's own shots — and a
    joiner should not have to reach into a dual-time block to find the number it matches on.
    """
    rows: list[dict[str, Any]] = []
    for index, reading in enumerate(readings):
        frame = sample_frame(index, first, rate, source)
        missed = verdicts[index]
        rows.append(
            {
                "time": dual_time(frame, source.fps),
                "t": _rounded(frame / (source.fps or 1.0)),
                "sharpness": reading.sharpness,
                "exposure": reading.exposure,
                "contrast": reading.contrast,
                "clipped": reading.clipped,
                "crushed": reading.crushed,
                "stability": reading.stability,
                # Said out loud rather than left to be inferred from a None: a sample across a
                # cut and a sample of a black hold are unmeasurable, not unstable (#182).
                "discontinuity": reading.discontinuity,
                "usable": not missed,
                "reasons": list(missed),
                "severity": _missed_by(reading, floors),
            }
        )
    return rows


def _missed_by(reading: Reading, floors: Floors) -> float:
    """How badly the worst-missed floor was missed, 0 to 1 — what ranks one window over another.

    Normalised per floor so the three are comparable: a sharpness of zero against a floor of
    0.3 and a frame entirely blown out both read as 1.0, because both are as unusable as that
    reading gets.

    Every floor is skipped at the value that switches it off, and a floor of zero switches a
    veto off — which is exactly how a style rule that cares only about clipping is written.
    Dividing by it would end the scan on its first sample, and a normalised distance from a
    rule nobody is enforcing is not a number anyway.
    """
    misses = [0.0]
    if floors.sharpness > 0.0:
        misses.append(max(0.0, (floors.sharpness - reading.sharpness) / floors.sharpness))
    if floors.clipped < 1.0:
        misses.append(max(0.0, (reading.clipped - floors.clipped) / (1.0 - floors.clipped)))
    if reading.stability is not None and floors.stability > 0.0:
        misses.append(max(0.0, (floors.stability - reading.stability) / floors.stability))
    return _rounded(min(1.0, max(misses)))


def _severity(sample: dict[str, Any]) -> float:
    """Sort key that puts the worst sample first — negated so ``min`` finds it."""
    return -float(sample["severity"])


def _windows(
    samples: list[dict[str, Any]],
    first: int,
    last: int,
    rate: float,
    source: Source,
) -> list[dict[str, Any]]:
    """The stretches to keep a cut out of: runs of unusable samples, one good one tolerated.

    Shaped exactly like an occlusion window — a window runs to one sample interval past its
    last unusable sample, clipped to the range that was scanned, and one that clips to nothing
    is not published. A lone unusable sample is a window a quarter of a second long, which is
    the right answer: a single blown frame is a camera flash, and a flash is a real thing to
    keep a cut off. What it adds is ``reasons``: a window a builder can act on has to say
    whether the shot is soft, blown or shaky, because those are three different fixes and only
    one of them is 'use another angle'.
    """
    found = runs([bool(one["reasons"]) for one in samples], GAP_SAMPLES)
    step = sample_step(rate, source)
    windows: list[dict[str, Any]] = []
    for begin, stop in found:
        inside = samples[begin:stop]
        start_frame = max(first, min(last, sample_frame(begin, first, rate, source)))
        end_frame = min(last, sample_frame(stop - 1, first, rate, source) + step)
        if end_frame <= start_frame:
            continue
        reasons = sorted({name for one in inside for name in one["reasons"]})
        windows.append(
            {
                "in": dual_time(start_frame, source.fps),
                "out": dual_time(end_frame, source.fps),
                "duration_frames": end_frame - start_frame,
                "duration_seconds": _rounded((end_frame - start_frame) / (source.fps or 1.0)),
                "samples": stop - begin,
                "reasons": reasons,
                "severity": _rounded(max(float(one["severity"]) for one in inside)),
                # Carried as the readings themselves, not re-rounded: a window's worst
                # sharpness and the sample it came from have to be the same number.
                "worst_sharpness": min(float(one["sharpness"]) for one in inside),
                "worst_clipped": max(float(one["clipped"]) for one in inside),
                "worst_stability": _worst_stability(inside),
            }
        )
    return windows


def _worst_stability(samples: list[dict[str, Any]]) -> float | None:
    measured = [float(one["stability"]) for one in samples if one["stability"] is not None]
    return min(measured) if measured else None


def _rounded(value: float) -> float:
    """Seconds and fractions of a range, at the precision every time in this repo carries.

    Never the readings themselves — those arrive from ``picture`` already rounded, and a
    second rounding here would leave the gist and the curve disagreeing in the last digit.
    """
    return round(value, SECONDS_PRECISION)


def _readable_rate(sample_fps: float) -> float:
    if not MIN_SAMPLE_FPS <= sample_fps <= MAX_SAMPLE_FPS:
        raise InvalidRequestError(
            cause=f"sample_fps={sample_fps} is not a sampling rate this scan runs at.",
            fix=(
                f"Ask for between {MIN_SAMPLE_FPS} and {MAX_SAMPLE_FPS} samples a second. "
                f"{DEFAULT_SAMPLE_FPS} is the default — stability is read between neighbouring "
                "samples, so a coarser rate reads a wobble as a move, and a finer one costs "
                "memory to say the same thing."
            ),
            detail={
                "requested": sample_fps,
                "minimum": MIN_SAMPLE_FPS,
                "maximum": MAX_SAMPLE_FPS,
            },
        )
    return float(sample_fps)


def _readable_floors(sharpness: float, clipped: float, stability: float) -> Floors:
    from .picture import Floors  # noqa: PLC0415 - numpy loads with this module, not at startup

    for name, value in (
        ("min_sharpness", sharpness),
        ("max_clipped", clipped),
        ("min_stability", stability),
    ):
        if not 0.0 <= value <= 1.0:
            raise InvalidRequestError(
                cause=f"{name}={value} is not a floor on a 0-to-1 reading.",
                fix=(
                    "Every quality reading runs 0 to 1, so every floor has to sit inside it. "
                    f"The defaults are min_sharpness={DEFAULT_MIN_SHARPNESS}, "
                    f"max_clipped={DEFAULT_MAX_CLIPPED}, min_stability={DEFAULT_MIN_STABILITY}."
                ),
                detail={"floor": name, "requested": value},
            )
    return Floors(sharpness=float(sharpness), clipped=float(clipped), stability=float(stability))


def _readable_length(first: int, last: int, source: Source, rate: float) -> None:
    """Refuse a range this scan cannot hold in memory at once.

    The occlusion scan needs no such check: it samples once a second onto a grid a sixth the
    area, so an hour of it is megabytes. This one samples several times a second onto a grid
    wide enough to see focus, and the whole sampled range is read and measured at once — so
    the ceiling is on the *decode*, not on the answer, and it is a separate refusal from the
    range length because it is a different thing going wrong.
    """
    seconds = (last - first) / (source.fps or 1.0)
    if seconds * rate <= MAX_SAMPLES:
        return
    raise InvalidRequestError(
        cause=(
            f"{seconds:.0f}s at {rate:g} samples a second is "
            f"{round(seconds * rate)} frames to hold at once."
        ),
        fix=(
            f"Scan at most {MAX_SAMPLES} samples in one job — shorten the range, or lower "
            f"sample_fps (the default is {DEFAULT_SAMPLE_FPS})."
        ),
        detail={
            "clip": source.name,
            "seconds": round(seconds, 1),
            "sample_fps": rate,
            "samples": round(seconds * rate),
            "maximum": MAX_SAMPLES,
        },
    )
