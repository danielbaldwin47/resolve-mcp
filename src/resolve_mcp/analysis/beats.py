"""Where the beat is, and which beat starts the bar.

The model is beat_this (#22), and it is behind a callable rather than called directly
(ADR 0002): it drags in torch, it is the one part of this file no test can check the answers
of, and the numbering that turns its two lists of times into bars *is* checkable. So the
detector is injectable, the default one loads beat_this when a job actually runs, and
everything downstream of the grid is ordinary arithmetic under test.
"""

from __future__ import annotations

import bisect
import importlib
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..errors import AnalysisDependencyError, AnalysisFailedError, ResolveMcpError
from ..logging_config import get_logger

log = get_logger("analysis")

MODULE = "beat_this.inference"
INSTALL = "uv pip install 'beat_this @ git+https://github.com/CPJKU/beat_this'"
DOWNBEAT_TOLERANCE = 0.005
"""How far a downbeat may sit from the beat it marks before it is a different beat."""


class BeatGrid(NamedTuple):
    """Beat times and the subset of them that start a bar, both in seconds."""

    beats: tuple[float, ...]
    downbeats: tuple[float, ...]


Detector = Callable[[Path], BeatGrid]


def detect(path: Path, detector: Detector | None = None) -> BeatGrid:
    """Run the detector and hand back a sorted grid, or say which half failed.

    A model that falls over is an ``analysis_failed``, not an internal error: the agent can
    act on it (try the other route, check the audio) and it is not a bug in this server.
    """
    chosen = detector or beat_this_detector
    try:
        grid = chosen(Path(path))
    except ResolveMcpError:
        raise
    except Exception as exc:
        raise AnalysisFailedError(
            cause=f"Beat detection failed on {Path(path).name}: {type(exc).__name__}: {exc}.",
            detail={"path": str(path)},
        ) from exc
    return BeatGrid(beats=tuple(sorted(grid.beats)), downbeats=tuple(sorted(grid.downbeats)))


def beat_this_detector(path: Path) -> BeatGrid:
    """The real thing: beat_this over the whole file, beats and downbeats in seconds."""
    module = _loaded()
    log.info("Running beat_this over %s", path.name)
    beats, downbeats = module.File2Beats()(str(path))
    return BeatGrid(
        beats=tuple(float(one) for one in beats),
        downbeats=tuple(float(one) for one in downbeats),
    )


def _loaded() -> Any:
    """Import the model only when a job needs it — it is a torch stack, not a dependency."""
    try:
        return importlib.import_module(MODULE)
    except ImportError as exc:
        raise AnalysisDependencyError(
            cause="beat_this is not installed, so beats and downbeats cannot be detected.",
            fix=(
                f"Install it on the machine running the server ({INSTALL}), or run the job "
                "with beats=false for energy only."
            ),
            detail={"module": MODULE},
        ) from exc


def numbered(grid: BeatGrid) -> tuple[dict[str, Any], ...]:
    """One record per beat: its time, its number, its bar, and where it sits in the bar.

    Bars are counted from the downbeats the model gave, so a tune in five and a tune in four
    both number correctly, and a pickup before the first downbeat counts as bar one.
    """
    marks = _downbeat_flags(grid)
    records: list[dict[str, Any]] = []
    bar = 0
    in_bar = 0
    for index, (seconds, downbeat) in enumerate(zip(grid.beats, marks, strict=True), start=1):
        if downbeat or bar == 0:
            bar += 1
            in_bar = 1
        else:
            in_bar += 1
        records.append(
            {
                "t": round(seconds, 3),
                "beat": index,
                "bar": bar,
                "in_bar": in_bar,
                "downbeat": downbeat,
            }
        )
    return tuple(records)


def _downbeat_flags(grid: BeatGrid) -> tuple[bool, ...]:
    """Match each downbeat to the nearest beat rather than trusting float equality."""
    flags = [False] * len(grid.beats)
    for downbeat in grid.downbeats:
        nearest = _nearest(grid.beats, downbeat)
        if nearest is not None and abs(grid.beats[nearest] - downbeat) <= DOWNBEAT_TOLERANCE:
            flags[nearest] = True
    return tuple(flags)


def _nearest(beats: Sequence[float], seconds: float) -> int | None:
    if not beats:
        return None
    after = bisect.bisect_left(beats, seconds)
    candidates = [one for one in (after - 1, after) if 0 <= one < len(beats)]
    return min(candidates, key=lambda one: abs(beats[one] - seconds))


def gist(grid: BeatGrid, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The stats worth returning inline: how many, how fast, and in what meter."""
    intervals = [
        later - earlier
        for earlier, later in zip(grid.beats, grid.beats[1:], strict=False)
        if later > earlier
    ]
    tempos = sorted(60.0 / interval for interval in intervals)
    lengths = list(Counter(record["bar"] for record in records).values())
    return {
        "count": len(grid.beats),
        "downbeat_count": sum(1 for record in records if record["downbeat"]),
        "tempo_bpm": round(statistics.median(tempos), 2) if tempos else None,
        "tempo_min_bpm": round(tempos[0], 2) if tempos else None,
        "tempo_max_bpm": round(tempos[-1], 2) if tempos else None,
        "meter": statistics.mode(lengths) if lengths else None,
        "first_seconds": round(grid.beats[0], 3) if grid.beats else None,
        "last_seconds": round(grid.beats[-1], 3) if grid.beats else None,
    }
