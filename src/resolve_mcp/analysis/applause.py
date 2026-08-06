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
"""

from __future__ import annotations

import importlib
import statistics
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
    """Music between two bursts of applause — or between a burst and the end of the file."""

    start: float
    end: float
    applause_before: float | None
    applause_after: float | None

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


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
        }
        for index, one in enumerate(found, start=1)
    )


def gist(curve: Curve, applause: Sequence[Span], found: Sequence[Tune]) -> dict[str, Any]:
    """How many tunes, how much clapping, and which tune is the long one — no lists.

    The boundaries themselves are on disk. A set is a dozen records; what belongs in a tool
    result is the shape of the set, and "the file has 12 tunes, the longest starts at 41:12".
    """
    longest = max(found, key=lambda one: one.seconds, default=None)
    shortest = min(found, key=lambda one: one.seconds, default=None)
    return {
        "count": len(found),
        "applause_count": len(applause),
        "applause_seconds": round(sum(one.seconds for one in applause), 3),
        "peak_probability": round(max(curve.probability), 4) if curve.probability else None,
        "median_probability": (
            round(statistics.median(curve.probability), 4) if curve.probability else None
        ),
        "longest": _summary(longest),
        "shortest": _summary(shortest),
    }


def _summary(one: Tune | None) -> dict[str, Any] | None:
    return None if one is None else {"t": one.start, "seconds": one.seconds}
