"""Where the front of the band changes hands.

No separator ships a horn stem or a piano stem, so nothing here can name the soloist (#22:
"who-is-soloing = timbre/activity analysis on the residual stem"). What it can do is say
*when* the front changed, which is what a cut needs: an editor goes wide on the head, tight
on the soloist, and the wrong frame to make that move on is anywhere but the change.

Two signals, because a jazz set hands the tune over in two different ways:

* **Across the stems.** The vocal takes it, then the horns; the bass takes a chorus. That
  is one stem lifting while the others sit, and it is read off the four energy curves.
* **Inside the residual.** Tenor out, piano in — both live in ``other``, the energy barely
  moves, and the only thing that changes is the timbre. That is a step in the residual
  stem's brightness, and it is the reason this module reads the residual twice.

The measurement that makes the first signal work is **prominence over a stem's own quiet
baseline**, not its level. Drums are the loudest stem in almost every mix and are almost
never the soloist; a stem that never varies is a stem that is accompanying. So each curve is
measured against its own 5th percentile — how far this stem has lifted over how quiet it
gets — which is comparable across stems in a way that dB in the mix is not. The baseline is
held within ``RANGE_DB`` of the stem's own peak so that a stem which falls silent for part
of a set does not read as leading the whole of it on the strength of a -120 LUFS floor.

Change points are then snapped to the nearest downbeat, because a solo change that is going
to be cut on is cut on the bar, and the energy window that found it is seconds wide.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from . import beats as beats_module

if TYPE_CHECKING:  # pragma: no cover - the worker imports these when it runs
    from .decode import Audio

DEFAULT_WINDOW_SECONDS = 4.0
DEFAULT_HOP_SECONDS = 1.0
DEFAULT_MINIMUM_SECONDS = 12.0
"""Shorter than this and it is a stab, a fill or a trade, not the front changing hands."""
DEFAULT_MARGIN_DB = 3.0
"""How far clear of the next stem a stem has to be before it is out front rather than level."""
DEFAULT_SNAP_SECONDS = 2.0
DEFAULT_SEMITONES = 4.0
"""How far the residual's brightness has to step before it is a different instrument."""

QUIET_PERCENTILE = 5.0
RANGE_DB = 40.0

RESIDUAL = "other"
"""The stem the horns and the piano land in — everything no model has a stem for."""

LEAD = "lead"
TIMBRE = "timbre"

BRIGHTNESS_WINDOW = 2048
BRIGHTNESS_HOP = 1024
BRIGHTNESS_CHUNK = 4096
BRIGHTNESS_FLOOR = 1e-9


class Voice(NamedTuple):
    """One stem's loudness curve. ``seconds`` is where each window starts."""

    name: str
    seconds: tuple[float, ...]
    lufs: tuple[float, ...]


class Run(NamedTuple):
    """A stretch of the set with one stem out front."""

    name: str
    start: float
    end: float
    margin_db: float

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


class Step(NamedTuple):
    """A step in the residual stem's brightness — a handover inside one stem."""

    seconds: float
    before: float
    after: float

    @property
    def semitones(self) -> float:
        return round(12.0 * math.log2(self.after / self.before), 2)


class Change(NamedTuple):
    """One change point. ``seconds`` is where it is called; ``measured`` is where it was seen."""

    seconds: float
    measured: float
    downbeat: bool
    signal: str
    left: str | None
    entered: str
    detail: float


def voices(
    stems: Mapping[str, Path],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
) -> tuple[Voice, ...]:
    """Read each stem once and keep its loudness curve. The rest of this module is arithmetic.

    Each window is dated at its **centre**, not where it starts. An energy window is seconds
    of audio and a handover inside it moves both curves from the moment it happens, so the
    window where one stem passes the other is the window straddling the change — dating it
    by its leading edge would report every solo change half a window early, every time.
    """
    from . import decode
    from . import energy as energy_module

    middle = window_seconds / 2.0
    found: list[Voice] = []
    for name in sorted(stems):
        curve = energy_module.curve(decode.read(stems[name]), window_seconds, hop_seconds)
        found.append(
            Voice(
                name=name,
                seconds=tuple(round(point.seconds + middle, 3) for point in curve),
                lufs=tuple(point.lufs for point in curve),
            )
        )
    return tuple(found)


def prominence(voice: Voice) -> NDArray[np.float64]:
    """How far this stem has lifted over its own quiet baseline, in dB, floored at zero.

    The baseline is the stem's 5th percentile, held within ``RANGE_DB`` of its own peak: a
    stem that is digital silence for part of a set has a percentile down at the silence
    floor, and without the clamp its every note would read as a hundred dB of lift.
    """
    values = np.asarray(voice.lufs, dtype=np.float64)
    if values.size == 0:
        return values
    baseline = max(float(np.percentile(values, QUIET_PERCENTILE)), float(values.max()) - RANGE_DB)
    return np.clip(values - baseline, 0.0, RANGE_DB)


def runs(
    found: Sequence[Voice],
    margin_db: float = DEFAULT_MARGIN_DB,
    minimum_seconds: float = DEFAULT_MINIMUM_SECONDS,
) -> tuple[Run, ...]:
    """Which stem is out front, window by window, collapsed into stretches.

    A window where no stem is clear of the others by ``margin_db`` does not end the stretch
    it falls in: the front does not change hands because two players were level for a bar.
    It takes a *different* stem leading to end one, and a stretch too short to be a solo is
    folded back into the one before it.
    """
    shared = min((len(one.lufs) for one in found), default=0)
    if not found or shared == 0:
        return ()
    seconds = found[0].seconds[:shared]
    lifts = {one.name: prominence(one)[:shared] for one in found}

    built: list[Run] = []
    for index, when in enumerate(seconds):
        name, margin = _front(lifts, index, margin_db)
        if name is None:
            continue
        if built and built[-1].name == name:
            continue
        built.append(Run(name, round(float(when), 3), 0.0, round(margin, 2)))
    return _sieved(_closed(built, seconds), minimum_seconds)


def _front(
    lifts: Mapping[str, NDArray[np.float64]],
    index: int,
    margin_db: float,
) -> tuple[str | None, float]:
    """The stem out front in this window and by how much, or nobody and how close it was.

    A lone stem is measured against a stem sitting at its own baseline, so a single-stem
    reading still has to lift to lead rather than leading by default.
    """
    ranked = sorted(((float(one[index]), name) for name, one in lifts.items()), reverse=True)
    best, name = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    return (name if best - second >= margin_db else None), best - second


def _closed(built: Sequence[Run], seconds: Sequence[float]) -> list[Run]:
    """Every stretch ends where the next one starts; the last ends with the audio."""
    if not built:
        return []
    hop = seconds[1] - seconds[0] if len(seconds) > 1 else 0.0
    ends = [*(one.start for one in built[1:]), round(float(seconds[-1] + hop), 3)]
    return [
        Run(one.name, one.start, end, one.margin_db)
        for one, end in zip(built, ends, strict=True)
    ]


def _sieved(built: Sequence[Run], minimum_seconds: float) -> tuple[Run, ...]:
    """Drop stretches too short to be a solo, and join what that leaves touching."""
    kept: list[Run] = []
    for one in built:
        if one.seconds < minimum_seconds and kept:
            kept[-1] = Run(kept[-1].name, kept[-1].start, one.end, kept[-1].margin_db)
            continue
        if kept and kept[-1].name == one.name:
            margin = max(kept[-1].margin_db, one.margin_db)
            kept[-1] = Run(kept[-1].name, kept[-1].start, one.end, margin)
            continue
        kept.append(one)
    return tuple(one for one in kept if one.seconds >= minimum_seconds)


def brightness(
    audio: Audio,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The residual stem's spectral centroid per window, in hertz — its brightness curve.

    Short frames averaged into the analysis window rather than one transform per window: a
    four-second FFT of a concert is both slow and smeared, and what is wanted is the timbre
    of the window's notes, not its lowest partial. Windows are dated at their centre, for
    the same reason the loudness curves are.
    """
    centroids, times = _centroids(audio)
    if centroids.size == 0:
        return (), ()
    last_start = max(float(audio.duration_seconds) - window_seconds, 0.0)
    starts = np.arange(0.0, last_start + 1e-9, hop_seconds)
    opened = np.searchsorted(times, starts, side="left")
    closed = np.searchsorted(times, starts + window_seconds, side="left")
    kept = [
        (float(start) + window_seconds / 2.0, float(np.median(centroids[first:last])))
        for start, first, last in zip(starts, opened, closed, strict=True)
        if last > first
    ]
    return tuple(round(one, 3) for one, _ in kept), tuple(round(one, 1) for _, one in kept)


def _centroids(audio: Audio) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Centre of gravity of each short-time spectrum, and when each frame starts."""
    mono = audio.mono()
    if mono.size < BRIGHTNESS_WINDOW:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)

    window = np.hanning(BRIGHTNESS_WINDOW).astype(np.float32)
    count = 1 + (mono.size - BRIGHTNESS_WINDOW) // BRIGHTNESS_HOP
    frames = np.lib.stride_tricks.as_strided(
        mono,
        shape=(count, BRIGHTNESS_WINDOW),
        strides=(mono.strides[0] * BRIGHTNESS_HOP, mono.strides[0]),
    )
    hertz = np.fft.rfftfreq(BRIGHTNESS_WINDOW, 1.0 / audio.sample_rate)

    found = np.empty(count, dtype=np.float64)
    for first in range(0, count, BRIGHTNESS_CHUNK):
        last = min(first + BRIGHTNESS_CHUNK, count)
        magnitudes = np.abs(np.fft.rfft(frames[first:last] * window, axis=1)).astype(np.float64)
        totals = magnitudes.sum(axis=1)
        found[first:last] = (magnitudes @ hertz) / np.maximum(totals, BRIGHTNESS_FLOOR)
    times = np.arange(count, dtype=np.float64) * BRIGHTNESS_HOP / audio.sample_rate
    return found, times


def steps(
    seconds: Sequence[float],
    hertz: Sequence[float],
    semitones: float = DEFAULT_SEMITONES,
    minimum_seconds: float = DEFAULT_MINIMUM_SECONDS,
) -> tuple[Step, ...]:
    """Where the brightness curve steps rather than drifts.

    Each candidate is judged by the median brightness of the ``minimum_seconds`` before it
    against the ``minimum_seconds`` after it, in semitones so that a step means the same
    thing at 400 Hz and at 4 kHz. A drift fails that test by construction — its two medians
    are only half a window apart however far the curve has travelled.

    That test is deliberately blunt about *where* the step is: a median stops moving once
    half its window is past the step, so every index for a window either side of one reads
    the same. So detection is one pass and localisation is another — inside each run of
    candidates the step is called where a short median either side of it disagrees most,
    which is the step itself and nowhere else.
    """
    values = np.asarray(hertz, dtype=np.float64)
    hop = seconds[1] - seconds[0] if len(seconds) > 1 else 0.0
    span = max(int(round(minimum_seconds / hop)), 1) if hop > 0 else 0
    if span == 0 or values.size < span * 2:
        return ()

    found = [
        index
        for index in range(span, values.size - span + 1)
        if abs(_shift(values, index, span)) >= semitones
    ]
    return tuple(
        Step(
            round(float(seconds[index]), 3),
            round(float(np.median(values[index - span : index])), 1),
            round(float(np.median(values[index : index + span])), 1),
        )
        for index in [_peak(values, run, span) for run in _grouped(found)]
    )


def _shift(values: NDArray[np.float64], index: int, span: int) -> float:
    """How far the curve steps at this index, in semitones, over ``span`` windows either side."""
    before = float(np.median(values[max(index - span, 0) : index]))
    after = float(np.median(values[index : index + span]))
    if before <= 0.0 or after <= 0.0:
        return 0.0
    return 12.0 * math.log2(after / before)


def _grouped(found: Sequence[int]) -> list[list[int]]:
    """Consecutive candidate indices are one step seen from several windows."""
    runs: list[list[int]] = []
    for index in found:
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
            continue
        runs.append([index])
    return runs


def _peak(values: NDArray[np.float64], run: Sequence[int], span: int) -> int:
    """Where inside a run of candidates the step actually is, by a short median either side."""
    close = max(span // 4, 1)
    return max(run, key=lambda index: (abs(_shift(values, index, close)), -index))


def changes(
    built: Sequence[Run],
    stepped: Sequence[Step] = (),
    together_seconds: float = DEFAULT_WINDOW_SECONDS,
    opened: float = 0.0,
) -> tuple[Change, ...]:
    """Every point where the front changed, from both signals, each event reported once.

    Whoever is out front in the first window did not take it there — that is the state the
    measurement opened in, and ``opened`` is when that was, so a set that starts mid-solo
    reports its first *change* rather than its first soloist.

    A stem taking the front and the residual's timbre stepping at the same moment is one
    event seen twice — the horn leaving ``other`` is why the vocal is now clear of it — so
    the timbre reading is dropped when a lead change is already within ``together_seconds``.
    Only against the lead changes: two timbre steps close together are two handovers inside
    the residual, and ``steps`` has already decided how close two of those may be.
    """
    found = [
        Change(
            seconds=one.start,
            measured=one.start,
            downbeat=False,
            signal=LEAD,
            left=(built[index - 1].name if index else None),
            entered=one.name,
            detail=one.margin_db,
        )
        for index, one in enumerate(built)
        if one.start > opened
    ]
    leads = tuple(found)
    found.extend(
        Change(
            seconds=one.seconds,
            measured=one.seconds,
            downbeat=False,
            signal=TIMBRE,
            left=RESIDUAL,
            entered=RESIDUAL,
            detail=one.semitones,
        )
        for one in stepped
        if all(abs(one.seconds - lead.measured) >= together_seconds for lead in leads)
    )
    return tuple(sorted(found, key=lambda one: one.measured))


def snapped(
    found: Sequence[Change],
    downbeats: Sequence[float],
    tolerance: float = DEFAULT_SNAP_SECONDS,
) -> tuple[Change, ...]:
    """Call each change on the nearest downbeat, unless the nearest one is too far to mean it.

    An energy window is seconds wide, so the measured time is approximate and the downbeat
    inside it is the frame worth cutting on. A downbeat half a chorus away is not: past
    ``tolerance`` the measured time stands, and the record says it was not snapped.
    """
    called: list[Change] = []
    for one in found:
        index = beats_module.nearest(downbeats, one.measured)
        near = index is not None and abs(downbeats[index] - one.measured) <= tolerance
        when = round(downbeats[index], 3) if near and index is not None else one.measured
        called.append(one._replace(seconds=when, downbeat=near))
    return tuple(called)


def numbered(found: Sequence[Change]) -> tuple[dict[str, Any], ...]:
    """One record per change: when it is called, when it was seen, and what changed."""
    return tuple(
        {
            "change": index,
            "t": one.seconds,
            "measured_t": one.measured,
            "downbeat": one.downbeat,
            "signal": one.signal,
            "from": one.left,
            "to": one.entered,
            "detail": one.detail,
        }
        for index, one in enumerate(found, start=1)
    )


def gist(built: Sequence[Run], found: Sequence[Change]) -> dict[str, Any]:
    """How many changes, how many landed on a downbeat, and who held the front longest."""
    longest = max(built, key=lambda one: one.seconds, default=None)
    seconds_in_front: dict[str, float] = {}
    for one in built:
        seconds_in_front[one.name] = round(seconds_in_front.get(one.name, 0.0) + one.seconds, 3)
    held = (
        None
        if longest is None
        else {"stem": longest.name, "t": longest.start, "seconds": longest.seconds}
    )
    return {
        "count": len(found),
        "snapped": sum(1 for one in found if one.downbeat),
        "timbre_changes": sum(1 for one in found if one.signal == TIMBRE),
        "runs": len(built),
        "seconds_in_front": seconds_in_front,
        "longest_lead": held,
    }
