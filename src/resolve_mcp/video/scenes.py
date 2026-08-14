"""Scene-cut detection: a catalog of where the picture changes on a piece of b-roll.

Unlike a grab, this decodes the whole file, so it is a job — a job the agent starts and
polls while it keeps working, and one whose answer is worth caching for as long as the media
is unchanged.

The catalog goes to disk and only a gist comes back inline, the shape #22 fixes for every
analysis job: a minute of fast-cut b-roll has hundreds of cuts, and the agent needs the
count and the shot lengths to decide whether to look at all — then it greps the file for the
span it cares about. Everything on disk is dual time, so nothing downstream re-derives a
frame number from a float.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import InvalidRequestError, SceneDetectionError
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from ..timing import IN_POINT, SECONDS_PRECISION, dual_time, frames_from_seconds
from . import ffmpeg
from .source import Source, locate

log = get_logger("video")

KIND = "detect_scene_cuts"

DEFAULT_THRESHOLD = 0.4
"""ffmpeg's scene score, 0 to 1. Around 0.4 catches a shot change without calling a fast pan
one; lower it for footage that cuts between similar frames, raise it for handheld."""

MIN_THRESHOLD = 0.05
"""Below this every compressed frame differs from the last one and every frame is a cut."""

INLINE_CUTS = 12
"""How many cuts the gist carries. Enough to see the shape of the clip; the file has the rest."""


def detect_scene_cuts(
    connection: ResolveConnection,
    clip: str,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    threshold: float = DEFAULT_THRESHOLD,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job that catalogs every scene cut in a clip. Returns the job record.

    ``runner`` is the subprocess seam: the default shells out for real.
    """
    config = config or get_config()
    source = locate(connection, clip, bin, SceneDetectionError)
    sensitivity = _readable_threshold(threshold)

    params = {"clip": clip, "bin": source.bin_path, "threshold": sensitivity}
    key = cache.cache_key(KIND, [cache.fingerprint(source.path)], params)

    def work(progress: Progress) -> JobOutput:
        return scan_scene_cuts(source, key, params, progress, config, runner)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def scan_scene_cuts(
    source: Source,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    config: Config | None = None,
    runner: Runner | None = None,
) -> JobOutput:
    """The worker: one decode pass, then the catalog on disk and the gist for the agent."""
    config = config or get_config()
    threshold = float(params["threshold"])

    progress(0.1, "decoding the clip to find scene cuts")
    scanned = ffmpeg.scan(source.path, threshold, runner=runner, config=config)

    progress(0.9, "cataloguing the cuts")
    cuts = _cut_frames(scanned.printed, source)
    shots = _shots(cuts, source)
    target = config.analysis_dir / f"{slug(source.name, 'scenes')}-{key[:12]}.scenes.json"
    catalog = {
        "clip": source.name,
        "bin": source.bin_path,
        "source": source.path,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "threshold": threshold,
        "fps": source.fps,
        # Which device decoded the pass, and why not the GPU when it was the CPU (#202):
        # the whole file is decoded here, so a silent software pass is minutes, not noise.
        "decode": scanned.decode,
        # The bounds the shots below are cut against. An out of null is why the last shot is
        # missing: Resolve reported no End for this clip, so where the tail stops is unknown.
        "bounds": source.bounds,
        "cuts": [dual_time(frame, source.fps) for frame in cuts],
        "shots": shots,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

    log.info("Catalogued %d scene cut(s) in %s to %s", len(cuts), source.name, target)
    return JobOutput(_gist(catalog, target, shots), (target,))


def _gist(catalog: dict[str, Any], target: Path, shots: list[dict[str, Any]]) -> dict[str, Any]:
    """What comes back inline: enough to decide whether the file is worth reading."""
    lengths = [float(one["duration_seconds"]) for one in shots]
    return {
        "path": str(target),
        "clip": catalog["clip"],
        "threshold": catalog["threshold"],
        "decode": catalog["decode"],
        "cuts": len(catalog["cuts"]),
        "shots": len(shots),
        "first_cuts": catalog["cuts"][:INLINE_CUTS],
        "shot_seconds": {
            "mean": _rounded(statistics.fmean(lengths)) if lengths else None,
            "median": _rounded(statistics.median(lengths)) if lengths else None,
            "min": _rounded(min(lengths)) if lengths else None,
            "max": _rounded(max(lengths)) if lengths else None,
        },
    }


def _cut_frames(printed: str, source: Source) -> list[int]:
    """Every reported cut as one of this clip's own frame numbers, in order, deduplicated.

    ``pts_time`` counts from the start of the file; the clip counts from its own ``Start``,
    so the offset goes back on. A cut lands on the first frame of the new shot — the moment
    the picture has changed — which is a floor, the same snap an in point takes.
    """
    if not source.fps:
        return []
    frames: list[int] = []
    for seconds in ffmpeg.selected_seconds(printed):
        frame = source.start + frames_from_seconds(seconds, source.fps, IN_POINT)
        if frame == source.start or not source.holds(frame):
            continue  # the first frame of the file is not a cut, and neither is one past its end
        if not frames or frame != frames[-1]:
            frames.append(frame)
    return frames


def _shots(cuts: list[int], source: Source) -> list[dict[str, Any]]:
    """The spans between the cuts, head and tail included — what a cut list is actually for.

    A clip Resolve reports no ``End`` for still has every shot but the last one: the tail
    runs to an out point nothing knows, so it is left out rather than guessed at, and what
    is left is a real catalog instead of an empty one.
    """
    if not source.fps:
        return []
    tail = [source.out] if source.out is not None else []
    boundaries = [source.start, *cuts, *tail]
    return [
        {
            "in": dual_time(start, source.fps),
            "out": dual_time(end, source.fps),
            "duration_frames": end - start,
            "duration_seconds": _rounded((end - start) / source.fps),
        }
        for start, end in zip(boundaries, boundaries[1:], strict=False)
        if end > start
    ]


def _rounded(seconds: float) -> float:
    return round(seconds, SECONDS_PRECISION)


def _readable_threshold(threshold: float) -> float:
    if not MIN_THRESHOLD <= threshold <= 1.0:
        raise InvalidRequestError(
            cause=f"threshold={threshold} is not a scene score.",
            fix=(
                f"Pass a threshold between {MIN_THRESHOLD} and 1.0 — it is how different two "
                f"frames have to be to count as a cut. {DEFAULT_THRESHOLD} is the default; "
                "lower finds more, higher finds only hard changes."
            ),
            detail={"requested": threshold, "minimum": MIN_THRESHOLD, "maximum": 1.0},
        )
    return float(threshold)
