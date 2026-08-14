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
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..errors import AnalysisDependencyError, AnalysisFailedError, ResolveMcpError
from ..logging_config import get_logger
from . import device

log = get_logger("analysis")

MODULE = "beat_this.inference"
INSTALL = "uv pip install 'beat_this @ git+https://github.com/CPJKU/beat_this'"
DOWNBEAT_TOLERANCE = 0.005
"""How far a downbeat may sit from the beat it marks before it is a different beat."""

UNSTEADY_FRACTION = 0.15
"""How far a beat-to-beat interval may sit from its neighbours before the grid is guessing.

Fifteen percent is roughly a swung eighth against a straight one: wide enough that ordinary
human time-keeping and the model's own rounding stay inside it, narrow enough that a rubato
ballad head — where the interval wanders by half again — falls outside on the first beat.
"""

MINIMUM_METER = 2
"""The smallest meter that is a meter at all.

A grid reporting bars one beat long has not found a slow tune; it has marked every beat a
downbeat, which is the failure the anchor timeline shows at 214bpm over a jazz set (#112).
Such a grid is refused whole rather than filtered against its own meter: keeping only the
beats whose position is ``1`` would leave a histogram that is 100% beat one by construction
and would read as the strongest possible evidence for the very skew being tested.
"""

STEADINESS_WINDOW = 9
"""Intervals used as the local reference for one interval.

The comparison is local rather than against the whole mix because a two-hour set has tunes
at different tempos, and a grid measured against the set-wide median would call the fast
tune untrustworthy for no better reason than that it is fast. Nine intervals is a little
over two bars in four: long enough to have a median worth the name, short enough that the
reference moves with the music.
"""


class BeatGrid(NamedTuple):
    """Beat times and the subset of them that start a bar, both in seconds."""

    beats: tuple[float, ...]
    downbeats: tuple[float, ...]


class GridTrust(NamedTuple):
    """Which beats the grid describes well enough to draw a statistic from, and why not.

    ``trusted`` is parallel to the beat records it was computed from. ``reasons`` counts the
    beats each check refused, so a result can say what it dropped rather than quietly
    returning a smaller n (#112).
    """

    trusted: tuple[bool, ...]
    meter: int | None
    reasons: dict[str, int]


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
    device.announce("beat_this")
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
        found = nearest(grid.beats, downbeat)
        if found is not None and abs(grid.beats[found] - downbeat) <= DOWNBEAT_TOLERANCE:
            flags[found] = True
    return tuple(flags)


def nearest(beats: Sequence[float], seconds: float) -> int | None:
    """Index of the beat — or downbeat — closest to a time, or ``None`` if there is no grid.

    Public because snapping to the grid is not only this module's business: a solo change
    measured off an energy curve is called on the bar it lands nearest (#38), correlation
    measures every cut against it (#40), and one bisect over a sorted list is the whole of
    that operation.
    """
    if not beats:
        return None
    after = bisect.bisect_left(beats, seconds)
    candidates = [one for one in (after - 1, after) if 0 <= one < len(beats)]
    return min(candidates, key=lambda one: abs(beats[one] - seconds))


GROUP_BARS = 4
"""Bars to the group.

Four rather than eight, settled rather than inherited (#125 asked for the disagreement
between this constant and its old docstring to be resolved deliberately). Phrases in this
material run four, eight or twelve bars, and every eight- and twelve-bar boundary is also a
four-bar one — so four is the divisor that marks all of them, at the cost of also marking
the bar line halfway through a longer phrase. That cost is affordable because callers weigh
this as one factor among several, never as a gate.
"""
AT_BAR_LINE = 0.6
MID_BAR = 0.15


def bar_line_strength(row: Mapping[str, Any] | None) -> float:
    """How strong a place to put something this beat is: the top of a four-bar group, an
    ordinary bar line, or the middle of a bar.

    Public for the same reason ``nearest`` is: more than one reading places an event against
    the grid and they must agree about what a strong placement is. Drum fills score the beat
    they resolve into (#39) and phrase boundaries score the beat the ending is called on
    (#143); the question — where does this beat sit in a four-bar group — is the grid's, not
    either detector's, and scoring it twice is how the two would drift apart.

    Note this is *hypermeter*, not a phrase boundary: it says a beat is a plausible place for
    a phrase to turn over, never that one did. ``analysis.phrases`` answers that.
    """
    if row is None or not row["downbeat"]:
        return MID_BAR
    return 1.0 if (int(row["bar"]) - 1) % GROUP_BARS == 0 else AT_BAR_LINE


def gist(grid: BeatGrid, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The stats worth returning inline: how many, how fast, and in what meter."""
    tempos = sorted(60.0 / one for one in _intervals(grid.beats) if one > 0)
    return {
        "count": len(grid.beats),
        "downbeat_count": sum(1 for record in records if record["downbeat"]),
        "tempo_bpm": round(statistics.median(tempos), 2) if tempos else None,
        "tempo_min_bpm": round(tempos[0], 2) if tempos else None,
        "tempo_max_bpm": round(tempos[-1], 2) if tempos else None,
        "meter": meter(records),
        "first_seconds": round(grid.beats[0], 3) if grid.beats else None,
        "last_seconds": round(grid.beats[-1], 3) if grid.beats else None,
    }


def trust(records: Sequence[dict[str, Any]]) -> GridTrust:
    """How far the grid may be trusted, beat by beat.

    A grid is fitted over the whole mix, including the stretches that are not in time at all
    — free intros, rubato heads, out-of-time codas — and the fit succeeds there anyway. Cuts
    scored against those stretches are measuring the detector rather than the director, so
    #112 gates them out of the beat statistics instead of averaging them in.

    Two checks, deliberately independent. The first is free: a bar position outside
    ``1..meter`` is the grid contradicting its own meter, and needs no confidence signal to
    spot. The second is the one that matters, because rubato carries perfectly legal bar
    positions and only the timing gives it away. beat_this exposes no per-beat confidence of
    its own (it returns two lists of times), so steadiness is derived from the intervals.
    """
    bar_length = meter(records)
    describes_bars = bar_length is not None and bar_length >= MINIMUM_METER
    steady = _steady_flags([float(record["t"]) for record in records])
    reasons: Counter[str] = Counter()
    trusted: list[bool] = []
    for record, holds_time in zip(records, steady, strict=True):
        in_bar = record.get("in_bar")
        placed = (
            describes_bars
            and bar_length is not None
            and isinstance(in_bar, int)
            and 1 <= in_bar <= bar_length
        )
        if not placed:
            reasons["bar_position"] += 1
        if not holds_time:
            reasons["tempo"] += 1
        trusted.append(placed and holds_time)
    return GridTrust(tuple(trusted), bar_length, dict(reasons))


def meter(records: Sequence[Mapping[str, Any]]) -> int | None:
    """The meter the grid behaves as if it is in: the commonest bar length it produced.

    One definition, shared by the gist that reports it, the gate that judges bar positions
    against it, and the fill detector sizing a window in bars (#125), so a grid can never be
    described as one meter and measured against another.
    """
    lengths = list(Counter(record["bar"] for record in records).values())
    return statistics.mode(lengths) if lengths else None


def spacing(times: Sequence[float]) -> tuple[float | None, ...]:
    """How wide a beat is at each beat, in seconds — the local tempo, parallel to ``times``.

    Public for the reason ``nearest`` is: more than one reading has to ask how far a beat is
    around here, and two answers to that would be two grids. It reads the same window the
    steadiness check judges an interval against; the cut side asks whether a cut sits further
    from its beat than a beat is wide, which is how a cut the grid does not reach is told from
    one it does (#160).

    Local rather than set-wide for the same reason the steadiness window is: a two-hour set
    has tunes at different tempos, and a beat in the fast one is not half a beat of the slow
    one. A beat is read from the window of the interval that *follows* it, and the last beat
    borrows the one before it — an asymmetry the steadiness check does not have, because that
    check is looking for the interval that does not belong and needs both sides to convict,
    while this one is only asking how fast the music runs around here and both sides answer
    the same. ``None`` where the grid cannot say: a lone beat has no interval, and inventing a
    width for it would be a verdict rather than a reading.
    """
    intervals = _intervals(times)
    if not intervals:
        return tuple(None for _ in times)
    return tuple(_local(intervals, min(index, len(intervals) - 1)) for index in range(len(times)))


def _intervals(times: Sequence[float]) -> list[float]:
    """The grid as the gaps between its beats — the one form every reading here is taken over."""
    return [later - earlier for earlier, later in zip(times, times[1:], strict=False)]


def _steady_flags(times: Sequence[float]) -> tuple[bool, ...]:
    """Whether each beat sits between intervals that match the tempo around them.

    A beat is judged by the intervals either side of it, so an interval that does not belong
    discredits both of the beats it joins rather than one arbitrary end of it.
    """
    intervals = _intervals(times)
    if len(intervals) < 2:
        # One interval has no neighbours to be judged against; refusing it would be inventing
        # a verdict rather than reaching one.
        return tuple(True for _ in times)
    unsteady = [
        _wanders(interval, _local(intervals, index)) for index, interval in enumerate(intervals)
    ]
    return tuple(
        not any(unsteady[near] for near in (position - 1, position) if 0 <= near < len(unsteady))
        for position in range(len(times))
    )


def _local(intervals: Sequence[float], index: int) -> float:
    """The tempo around one interval, as the median of the window it sits in."""
    start = max(0, index - STEADINESS_WINDOW // 2)
    return statistics.median(intervals[start : start + STEADINESS_WINDOW])


def _wanders(interval: float, local: float) -> bool:
    """Whether one interval is too far from the tempo around it to be the same tempo.

    A window with no length at all — a grid reporting the same time twice — is treated as
    wandering rather than divided by, since nothing can be judged against it.
    """
    return local <= 0 or abs(interval - local) > UNSTEADY_FRACTION * local
