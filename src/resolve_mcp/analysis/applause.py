"""Where the room claps, and therefore where one tune ends and the next begins.

A jazz set has no verses and no choruses to segment; what it has is applause (#22). Between
two bursts of it is a tune, and that is the only structure boundary a concert recording
offers reliably — so tune boundaries are read off an audio tagger (PANNs) rather than off a
music-structure model that would look for a pop form that is not there.

The tagger is behind a callable (ADR 0002) for the same reasons the beat model is: it drags
in torch, no test in this repo can say whether it heard applause correctly, and everything
after it — which runs of probability are a burst, which gaps between bursts are a tune —
is ordinary arithmetic that a test can pin exactly. Three rules do that work:

* **A burst has to last.** A single frame over the threshold is a snare hit the model liked
  the look of; ``minimum_seconds`` is what separates that from a room.
* **A burst has holes.** Applause dips as it swells; two runs a beat apart are one burst,
  which is what ``gap_seconds`` closes.
* **A gap between bursts is not always a tune.** Announcing the band takes twenty seconds
  and is bounded by applause at both ends, so anything under ``minimum_seconds`` of music
  is banter and is left out of the boundaries rather than reported as a very short tune.

The length rule is not enough on its own, which the first live pass showed: three of the
thirteen calls on a real concert had no musical pulse under them and one of those was two
minutes of talking (#119 section D, #133). Length cannot separate those from a tune, so a
fourth rule reads the beat grid the music analysis already wrote:

* **A tune has a pulse.** Beats per second over the call, against ``minimum_density``.
  Talking and long announcements come back near zero; the sparsest real tune on the
  measured concert was 1.36. That is the whole check — the grid is somebody else's
  measurement (#37) and this module only divides by it.
"""

from __future__ import annotations

import importlib
import statistics
from bisect import bisect_left
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from ..errors import AnalysisDependencyError, AnalysisFailedError, ResolveMcpError
from ..logging_config import get_logger

log = get_logger("analysis")

MODULE = "panns_inference"
INSTALL = "uv pip install panns-inference"
LABELS = ("applause", "cheering", "clapping", "crowd")
"""The AudioSet classes that mean "the room", taken together rather than one at a time —
a hall reads as applause, a small club as cheering, and the boundary is the same event."""

MODEL_SAMPLE_RATE = 32_000
"""What PANNs was trained at. Anything else is resampled before it is tagged."""

CHUNK_SECONDS = 60.0
"""How much audio goes through the model at once. A concert does not fit in GPU memory."""

DEFAULT_THRESHOLD = 0.3
DEFAULT_MINIMUM_SECONDS = 3.0
DEFAULT_GAP_SECONDS = 1.5
DEFAULT_TUNE_SECONDS = 60.0
"""Under a minute between two bursts of applause is an announcement, not a tune."""

DEFAULT_DENSITY_PER_SECOND = 0.5
"""Beats per second a call needs before it counts as music.

Measured, not guessed — the sweep and the live run are recorded on #133. On the concert
master the tagger called thirteen tunes, and the beat grid puts the ten real ones between
1.36 and 2.70 beats per second while the three the ear rejected sit at 0.07, 0.00 and 0.00.
Every floor from 0.1 to 1.25 calls that concert the same way, so 0.5 is not on a cliff but
in the middle of a plateau an order of magnitude wide. It is also below any tempo a band
plays — 0.5 beats per second is 30 bpm — which is the reason the number should hold on
material this concert cannot speak for.

Set it to 0.0 to turn the check off and get the unfiltered set back, which is also what
avoids needing a beat grid at all (see ``structure``)."""


class Curve(NamedTuple):
    """How likely the room is clapping, frame by frame. ``seconds`` is each frame's start."""

    seconds: tuple[float, ...]
    probability: tuple[float, ...]


class Span(NamedTuple):
    """One burst of applause."""

    start: float
    end: float
    peak: float

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


class Tune(NamedTuple):
    """Music between two bursts of applause — or between a burst and the end of the file.

    ``beats`` and ``beats_per_second`` are ``None`` until ``counted`` reads a grid over the
    call. None is not zero: no grid was read, so nothing is known about the pulse, and a
    call in that state is never dropped for want of one.
    """

    start: float
    end: float
    applause_before: float | None
    applause_after: float | None
    beats: int | None = None
    beats_per_second: float | None = None

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


class Calls(NamedTuple):
    """The tune set after the density check: what has a pulse under it, and what does not."""

    kept: tuple[Tune, ...]
    dropped: tuple[Tune, ...]


Tagger = Callable[[Path], Curve]


def tag(path: Path, tagger: Tagger | None = None) -> Curve:
    """Run the tagger and hand back its curve, or say that the tagging failed.

    Same contract as beat detection: a model that falls over is an ``analysis_failed`` the
    agent can act on, not an internal error.
    """
    chosen = tagger or panns_tagger
    try:
        curve = chosen(Path(path))
    except ResolveMcpError:
        raise
    except Exception as exc:
        raise AnalysisFailedError(
            cause=f"Applause tagging failed on {Path(path).name}: {type(exc).__name__}: {exc}.",
            detail={"path": str(path)},
        ) from exc
    return Curve(
        seconds=tuple(float(one) for one in curve.seconds),
        probability=tuple(float(one) for one in curve.probability),
    )


def panns_tagger(path: Path) -> Curve:
    """The real thing: PANNs' frame-wise tags, reduced to one applause probability per frame.

    The file goes through the model a minute at a time. An hour of concert at the model's
    own 32 kHz is a hundred million samples, and handing that to a CNN in one call is how a
    job dies on the GPU rather than returning a boundary list.
    """
    module = _loaded()
    detector = module.SoundEventDetection(checkpoint_path=None)
    wanted = _wanted(module)
    log.info("Tagging %s for applause with PANNs", path.name)

    seconds: list[float] = []
    probability: list[float] = []
    for offset, chunk in _chunks(path):
        framewise = np.asarray(detector.inference(chunk[None, :]))[0]
        best = framewise[:, wanted].max(axis=1)
        step = len(chunk) / MODEL_SAMPLE_RATE / max(len(best), 1)
        seconds.extend(offset + index * step for index in range(len(best)))
        probability.extend(float(one) for one in best)
    return Curve(seconds=tuple(seconds), probability=tuple(probability))


def _loaded() -> Any:
    """Import the tagger only when a job needs it — it is a torch stack, not a dependency."""
    try:
        return importlib.import_module(MODULE)
    except ImportError as exc:
        raise AnalysisDependencyError(
            cause="panns_inference is not installed, so applause cannot be detected.",
            fix=(
                f"Install it on the machine running the server ({INSTALL}), or run the job "
                "with tunes=false for solo changes only."
            ),
            detail={"module": MODULE},
        ) from exc


def _wanted(module: Any) -> list[int]:
    """Which of AudioSet's 527 classes count as the room."""
    found = [
        index
        for index, label in enumerate(module.labels)
        if any(one in str(label).lower() for one in LABELS)
    ]
    if not found:
        raise AnalysisFailedError(
            cause="PANNs reported no applause-like class, so its labels are not what we expect.",
            fix="Check the installed panns_inference version.",
            detail={"module": MODULE, "wanted": list(LABELS)},
        )
    return found


def _chunks(path: Path) -> list[tuple[float, np.ndarray[Any, Any]]]:
    """The file as mono at the model's sample rate, in chunks, each with its offset."""
    from scipy.signal import resample_poly

    from . import decode

    audio = decode.read(path)
    mono = audio.mono()
    if audio.sample_rate != MODEL_SAMPLE_RATE:
        divisor = np.gcd(MODEL_SAMPLE_RATE, audio.sample_rate)
        mono = resample_poly(
            mono,
            MODEL_SAMPLE_RATE // divisor,
            audio.sample_rate // divisor,
        ).astype(np.float32)
    size = int(CHUNK_SECONDS * MODEL_SAMPLE_RATE)
    return [
        (first / MODEL_SAMPLE_RATE, np.ascontiguousarray(mono[first : first + size]))
        for first in range(0, max(len(mono), 1), size)
    ]


def spans(
    curve: Curve,
    threshold: float = DEFAULT_THRESHOLD,
    minimum_seconds: float = DEFAULT_MINIMUM_SECONDS,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
) -> tuple[Span, ...]:
    """The bursts of applause in a probability curve: runs over the threshold, joined, sieved."""
    if not curve.seconds:
        return ()
    steps = _steps(curve.seconds)
    runs = _runs(curve, threshold, steps)
    return tuple(one for one in _joined(runs, gap_seconds) if one.seconds >= minimum_seconds)


def _steps(seconds: Sequence[float]) -> list[float]:
    """How long each frame lasts. The last one lasts as long as the frame before it."""
    gaps = [later - earlier for earlier, later in zip(seconds, seconds[1:], strict=False)]
    return [*gaps, gaps[-1] if gaps else 0.0]


def _runs(curve: Curve, threshold: float, steps: Sequence[float]) -> list[Span]:
    """Every unbroken run of frames at or over the threshold, in the order they happen."""
    found: list[Span] = []
    start: float | None = None
    peak = 0.0
    for seconds, probability in zip(curve.seconds, curve.probability, strict=True):
        if probability >= threshold:
            start = seconds if start is None else start
            peak = max(peak, probability)
            continue
        if start is not None:
            found.append(Span(round(start, 3), round(seconds, 3), round(peak, 4)))
            start, peak = None, 0.0
    if start is not None:
        last = curve.seconds[-1] + steps[-1]
        found.append(Span(round(start, 3), round(last, 3), round(peak, 4)))
    return found


def _joined(runs: Sequence[Span], gap_seconds: float) -> list[Span]:
    """Two runs closer together than ``gap_seconds`` are one burst with a hole in it."""
    joined: list[Span] = []
    for one in runs:
        if joined and one.start - joined[-1].end < gap_seconds:
            previous = joined[-1]
            joined[-1] = Span(previous.start, one.end, max(previous.peak, one.peak))
            continue
        joined.append(one)
    return joined


def tunes(
    applause: Sequence[Span],
    duration_seconds: float,
    minimum_seconds: float = DEFAULT_TUNE_SECONDS,
) -> tuple[Tune, ...]:
    """The music between the bursts: one record per tune, with the applause on either side.

    A set with no applause found in it is one tune, which is the honest answer — the file is
    music from end to end as far as anything measured here can tell.
    """
    found: list[Tune] = []
    opened = 0.0
    before: float | None = None
    for span in applause:
        found.append(Tune(round(opened, 3), round(span.start, 3), before, span.seconds))
        opened, before = span.end, span.seconds
    found.append(Tune(round(opened, 3), round(duration_seconds, 3), before, None))
    return tuple(one for one in found if one.seconds >= minimum_seconds)


def counted(found: Sequence[Tune], beats: Sequence[float]) -> tuple[Tune, ...]:
    """The same tunes with the grid's beats counted inside each one, and the density that is.

    The spans are half-open: a beat landing exactly on a boundary belongs to the call that
    starts there, so consecutive tunes never count the same downbeat twice. The grid is
    sorted here rather than assumed sorted — the bisect below needs it, and this module does
    not own the measurement it is dividing by.
    """
    ordered = sorted(beats)
    return tuple(_over(one, ordered) for one in found)


def _over(one: Tune, ordered: Sequence[float]) -> Tune:
    """One call with the beats between its boundaries counted, and the density they make."""
    inside = bisect_left(ordered, one.end) - bisect_left(ordered, one.start)
    seconds = one.seconds
    return one._replace(
        beats=inside,
        beats_per_second=round(inside / seconds, 3) if seconds > 0 else 0.0,
    )


def sifted(
    found: Sequence[Tune],
    minimum_density: float = DEFAULT_DENSITY_PER_SECOND,
) -> Calls:
    """Split the calls into the ones with a pulse under them and the ones without.

    A call whose density was never measured is kept: there is no grid, so there is no
    evidence to drop it on, and inventing one by treating "unknown" as "zero" would delete
    tunes on any path that skipped the beat model.
    """
    kept: list[Tune] = []
    dropped: list[Tune] = []
    for one in found:
        pulseless = one.beats_per_second is not None and one.beats_per_second < minimum_density
        (dropped if pulseless else kept).append(one)
    return Calls(tuple(kept), tuple(dropped))


def numbered(found: Sequence[Tune]) -> tuple[dict[str, Any], ...]:
    """One record per tune, numbered from one — the rows a songs.json author reads."""
    return tuple(
        {
            "tune": index,
            "t": one.start,
            "end": one.end,
            "seconds": one.seconds,
            "applause_before": one.applause_before,
            "applause_after": one.applause_after,
            "beats": one.beats,
            "beats_per_second": one.beats_per_second,
        }
        for index, one in enumerate(found, start=1)
    )


def dropped_calls(dropped: Sequence[Tune], minimum_density: float) -> tuple[dict[str, Any], ...]:
    """What the density check took out, with the measurement that took it out.

    On disk beside the tune set rather than in the gist, for the reason every boundary is:
    a tool result is stats. But it has to be somewhere a caller can read, because a filter
    whose rejects cannot be inspected is one nobody can check (#38) — this is the record
    that says the eighth call was two minutes of talking rather than a lost tune.

    It rides in the file's header, which puts it on one line, and that is affordable because
    it is bounded by the applause: there is at most one call per burst, so a whole concert
    is tens of these and never the thousands a beat grid or an energy curve would be.
    """
    return tuple(
        {
            "t": one.start,
            "end": one.end,
            "seconds": one.seconds,
            "beats": one.beats,
            "beats_per_second": one.beats_per_second,
            "reason": f"no pulse: under the beat-density floor of {minimum_density} per second",
        }
        for one in dropped
    )


def gist(curve: Curve, applause: Sequence[Span], calls: Calls) -> dict[str, Any]:
    """How many tunes, how much clapping, and which tune is the long one — no lists.

    The boundaries themselves are on disk. A set is a dozen records; what belongs in a tool
    result is the shape of the set, and "the file has 12 tunes, the longest starts at 41:12".

    The density check reports the same way, and deliberately does not list what it dropped —
    that goes on disk with the boundaries, in ``dropped_calls``. What a caller needs inline
    is whether the floor was anywhere near a decision, so it gets the two shoulders: the
    densest call that was dropped and the sparsest that was kept. A wide gap between them
    says the filter did not have to choose; a narrow one says the threshold wants looking at.
    """
    longest = max(calls.kept, key=lambda one: one.seconds, default=None)
    shortest = min(calls.kept, key=lambda one: one.seconds, default=None)
    return {
        "count": len(calls.kept),
        "applause_count": len(applause),
        "applause_seconds": round(sum(one.seconds for one in applause), 3),
        "peak_probability": round(max(curve.probability), 4) if curve.probability else None,
        "median_probability": (
            round(statistics.median(curve.probability), 4) if curve.probability else None
        ),
        "longest": _summary(longest),
        "shortest": _summary(shortest),
        "dropped": len(calls.dropped),
        "dropped_seconds": round(sum(one.seconds for one in calls.dropped), 3),
        "densest_dropped": _summary(max(calls.dropped, key=_pulse, default=None)),
        "sparsest_kept": _summary(min(calls.kept, key=_pulse, default=None)),
    }


def _pulse(one: Tune) -> float:
    """A call's density for ordering, with "never measured" sorting above every measured one."""
    return float("inf") if one.beats_per_second is None else one.beats_per_second


def _summary(one: Tune | None) -> dict[str, Any] | None:
    if one is None:
        return None
    return {"t": one.start, "seconds": one.seconds, "beats_per_second": one.beats_per_second}
