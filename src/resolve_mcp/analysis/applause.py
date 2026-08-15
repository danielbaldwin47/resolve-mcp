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

Those four rules are enough on a room mic and blind on a board mix, which is the case #179
was filed for. A desk feed carries no crowd bleed, so the same clapping the room mic reads
at 0.99 reaches the tagger at 0.30 — under the threshold for a whole 74-minute set, one
tune found where five were played. Two more rules close that, and neither invents a signal:

* **A file the threshold finds nothing in is read at its own scale.** The model's
  confidence is set by how much room is in the mix, but the *shape* of the curve is not:
  applause is still a rare, tall excursion over a floor that is nearly zero (the board
  mix's median is 0.00003, its 90th percentile 0.0003, and its applause peaks 0.03 to
  0.30). So when the ceiling turns up less than ``QUIET_SECONDS`` of clapping in a whole
  set, ``reading`` derives the threshold from the file's own peak instead, and shortens the
  burst minimum with it. A fallback and not a recalibration, deliberately: dropping the
  threshold on a mix that *does* clear it turns every burst of mid-tune applause after a
  solo into a tune boundary, which is the regression this rule is shaped to avoid. Set
  ``scale`` to zero and there is no fallback at all.

* **A tune starts when the band does, not when the clapping stops.** Between the applause
  and the downbeat is an announcement, a re-tune, a count-in — 2 s of it on the measured
  set's first tune and 66 s on its second, all of it 20-40 dB under the music. Reading the
  boundary off the applause alone therefore lands up to a minute early, which is a miss at
  any tolerance an editor cares about. So each call's start walks forward to where the mix
  comes up to playing level and stays there, and a call the music never comes up in at all
  is not a tune. Playing level is the file's own median loudness less ``settle_db`` — the
  loudness curve is ``analyze_music``'s measurement (#37), and this module only compares
  against it. Set ``settle_seconds`` to zero and the boundary is the end of the applause,
  which is what it was before #179.
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
from . import device

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
"""How sure the tagger has to be. A ceiling since #179 rather than the only threshold:
``reading`` falls back to the file's own scale when this one finds nothing, and never
raises it, so material with an audible crowd is read exactly as it was."""

DEFAULT_SCALE = 0.09
"""How much of the file's own applause peak counts as applause, when the fallback fires.

Measured on the Zinc Set 2 board mix, whose five human-established tune starts are the only
ground truth this rule has (`gauntlet/recon/board_boundary_sweep.json`: 35 of 140 settings
clean, worst error 2.01 s across those 35).

At the shipped margin and hold, every fraction from 0.06 to 0.10 finds all five and invents
nothing, and 0.12 and 0.15 each lose one. The bottom of that range is not really a range —
``MINIMUM_THRESHOLD`` floors 0.06 and 0.07 onto the same 0.02 — so the span that is
actually about this number is 0.08, 0.09, 0.10, and 0.09 is the middle of it. It lands on a
threshold of 0.023 here: the peak it scales is the one that lasts (see
``_peak_that_lasts``), 0.26 rather than the single-frame 0.298.

What is lost at 0.12 is a burst rather than a level: 0.12 scales to 0.031, still well under
the 0.066 the quietest burst on that mix peaks at, so the burst is still over the line —
just not for the ``QUIET_BURST_SECONDS`` it has to hold it for.

The Scullers room mic is why this is a fallback and not the rule: 0.09 of its peak is
0.059, and reading it there turns the clapping after each solo into a boundary — 19 calls
where the ceiling finds 13. Zero turns the fallback off."""

QUIET_SECONDS = 10.0
"""How little clapping over the ceiling means the ceiling is unusable on this file.

Under one burst's worth in a whole set. The measured board mix has none at all — its peak
is 0.298 against a ceiling of 0.3 — and the Scullers room mic has 146 seconds, so the two
cases are three orders apart and this number is nowhere near either of them. A threshold
that finds this little in an hour of a room that applauds between tunes is not a threshold
that file can be read at."""

QUIET_BURST_SECONDS = 2.0
"""How long a burst has to last on a file read at its own scale.

Shorter than ``DEFAULT_MINIMUM_SECONDS`` and for a reason that only applies there: a
compressed curve clears its own threshold at the burst's peak and nowhere else, so what is
measured is the peak of the burst rather than the burst. Measured in
`gauntlet/recon/board_curve_sweep.json`: at 3.0 the measured board mix never has a burst
near all five of its boundaries at any threshold in the sweep, and 1.5, 2.0 and 2.5 all
do. 2.0 is the middle of the three."""

MINIMUM_THRESHOLD = 0.02
"""Below this a scaled threshold is chasing the model's own noise, not a quiet room.

A file with no applause anywhere still has a peak, and a fraction of it is meaningless —
the board mix's 90th percentile is 0.0003, so anything at this level is three decimal
orders above the curve's own baseline and still an order under its quietest real burst."""

DEFAULT_MINIMUM_SECONDS = 3.0
DEFAULT_GAP_SECONDS = 1.5
DEFAULT_TUNE_SECONDS = 60.0
"""Under a minute between two bursts of applause is an announcement, not a tune."""

DEFAULT_SETTLE_DB = 6.0
"""How far under the file's median loudness still counts as the band playing.

Measured on the same set (`gauntlet/recon/board_boundary_sweep.json`, read
``clean_settings`` and not the per-axis lists): its music sits between -12 and -25 LUFS
against a median of -17.5, and everything between the tunes — talking, tuning, the room —
between -27 and -55.

At the shipped scale and hold, 4 and 6 dB both call the set exactly and 8 dB invents a
sixth tune, because the floor has reached down into the talk; 10 dB and wider invent one at
every hold tested. 6.0 rather than 4.0 because it is the wider of the two across holds: 6.0
is clean at 5, 10 and 20 seconds and 4.0 only at 5 and 10. Neither is clean at 15 — the
axes are not independent, and no margin here is safe at every hold."""

DEFAULT_SETTLE_SECONDS = 10.0
"""How long the mix has to stay at playing level before it is the tune starting.

Long enough that a shouted introduction or one loud chord of tuning is not a downbeat,
short enough to sit well inside the shortest tune anyone plays.

At the shipped scale and margin, 5, 10 and 20 seconds all call the measured set exactly and
15 does not — it puts the third start at 1926.0 against a human 1920.1, 5.9 s out
(`gauntlet/recon/board_boundary_sweep.json`). That is not a plateau with a hole in it so
much as a reminder of what the hold does: a longer window has to clear ``SETTLE_SHARE`` of
itself, so it steps over a start whose first seconds are ragged and finds the next steady
one. 10 is the middle of the three that work, and far enough from 15 to be worth trusting
over the ones that happen to sit either side of it.

Zero turns the whole step off, and then a boundary is the end of the applause — which is
what it was before #179, and the way to run the tune half with no loudness curve at all."""

SETTLE_SHARE = 0.75
"""How much of the hold window has to be over the floor. Music dips inside a phrase and
between them; it does not stop. Requiring an unbroken run instead makes the start depend
on how deep the first dip is, which is not a boundary anyone hears."""

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


class Reading(NamedTuple):
    """The two numbers a curve is read with, and whether they came off the file itself.

    ``own_scale`` is the finding, not a setting: it says the ceiling found almost no
    clapping in this file and the numbers beside it were derived from the curve instead. It
    rides out in the gist, because "these boundaries were read at 0.027 rather than 0.3" is
    the first thing to know about a set that came back with more tunes than expected.
    """

    threshold: float
    burst_seconds: float
    own_scale: bool


class Loudness(NamedTuple):
    """How loud the mix is, window by window — ``analyze_music``'s energy curve, as read.

    ``seconds`` is each window's start and ``lufs`` its loudness. Only the two columns this
    module compares against: the curve on disk carries onset density and RMS too, and
    neither is a boundary.
    """

    seconds: tuple[float, ...]
    lufs: tuple[float, ...]


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

    ``talk_seconds`` is the same kind of None, for the same reason: how long the applause
    had been over before the band came in, once ``settled`` has read a loudness curve over
    the call, and nothing at all before that.
    """

    start: float
    end: float
    applause_before: float | None
    applause_after: float | None
    beats: int | None = None
    beats_per_second: float | None = None
    talk_seconds: float | None = None

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


class Calls(NamedTuple):
    """The tune set after the density check: what has a pulse under it, and what does not."""

    kept: tuple[Tune, ...]
    dropped: tuple[Tune, ...]


class Settled(NamedTuple):
    """The tune set after the loudness curve has been read over it.

    Two ways to lose a call here rather than one, because they are different findings and a
    reader of the rejects has to be able to tell them apart: ``silent`` never came up to
    playing level anywhere inside itself — a stretch of talking bounded by clapping, the
    thing the density check catches after the fact and this one catches before. ``brief``
    did come up, so late that what is left is under a tune's worth of music: the applause
    was a minute and a half apart and eighty seconds of that was announcing the band.
    """

    kept: tuple[Tune, ...]
    silent: tuple[Tune, ...]
    brief: tuple[Tune, ...]


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
    note = device.announce("PANNs")
    # PANNs defaults to "cuda" and falls back to the CPU without saying so, which reads
    # exactly like a run that reached the card. Naming the device — off the note just
    # announced — makes the record answerable for where the curve was computed (#245).
    detector = module.SoundEventDetection(
        checkpoint_path=None, device=device.inference_device(note)
    )
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


def reading(
    curve: Curve,
    ceiling: float = DEFAULT_THRESHOLD,
    scale: float = DEFAULT_SCALE,
    burst_seconds: float = DEFAULT_MINIMUM_SECONDS,
) -> Reading:
    """How to read this file's curve: the threshold to use, and the shortest burst that counts.

    Both, together, because on a file the tagger was never sure about they come down for the
    same reason. The fallback fires when the whole file holds less than ``QUIET_SECONDS``
    over the ceiling — less clapping than one burst, in a recording of a room that applauded
    between every tune — and that is the evidence that the ceiling is not a threshold here
    but a wall. It is deliberately not "the peak is under the ceiling": a board mix with one
    lucky burst over the line is still a board mix, and a set read at the ceiling should not
    change its mind over a single frame.

    A file that does clear the ceiling is read exactly as it was before #179, threshold and
    burst both, which is what keeps material with an audible crowd where it was. So is any
    file when ``scale`` is zero, and so is a curve too short to say how long anything in it
    lasted — one frame has no duration, and a rule that reads seconds off it would fire the
    fallback on every degenerate curve there is.

    ``own_scale`` says the fallback fired, not that the threshold came out lower than the
    ceiling: at a ``scale`` steep enough for the file's own peak to ask for more than the
    ceiling, the ceiling still binds and only the burst minimum moves. The gist carries both
    numbers, so a reader is never left inferring one from the other.
    """
    if scale <= 0 or len(curve.probability) < 2 or len(curve.seconds) < 2:
        return Reading(ceiling, burst_seconds, False)
    if _seconds_over(curve, ceiling) >= QUIET_SECONDS:
        return Reading(ceiling, burst_seconds, False)
    peak = _peak_that_lasts(curve, min(burst_seconds, QUIET_BURST_SECONDS))
    threshold = min(ceiling, max(MINIMUM_THRESHOLD, peak * scale))
    return Reading(threshold, min(burst_seconds, QUIET_BURST_SECONDS), True)


def _seconds_over(curve: Curve, threshold: float) -> float:
    """How much of the file the tagger put at or over a threshold, frames added up."""
    steps = _steps(curve.seconds)
    return sum(
        step
        for step, probability in zip(steps, curve.probability, strict=True)
        if probability >= threshold
    )


def _peak_that_lasts(curve: Curve, seconds: float) -> float:
    """The highest level the curve holds for ``seconds`` in total — the peak, made to last.

    The plain maximum is one frame, and one frame is exactly what the first rule in this
    module refuses to call applause: a shout into a vocal mic or a cymbal the model liked
    would set the threshold for a whole set, and set it far too high, because everything
    else is scaled off it. So the level the fallback scales is the one that at least a
    burst's worth of the file reaches, which is the same evidence ``spans`` asks for and
    costs one sort of the curve.
    """
    ordered = sorted(
        zip(curve.probability, _steps(curve.seconds), strict=True),
        key=lambda one: one[0],
        reverse=True,
    )
    held = 0.0
    for probability, step in ordered:
        held += step
        if held >= seconds:
            return probability
    return ordered[-1][0] if ordered else 0.0


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


def settled(
    found: Sequence[Tune],
    loudness: Loudness,
    margin_db: float = DEFAULT_SETTLE_DB,
    hold_seconds: float = DEFAULT_SETTLE_SECONDS,
    minimum_seconds: float = DEFAULT_TUNE_SECONDS,
) -> Settled:
    """Each call's start moved forward to where the band comes in, and the ones with no band.

    The applause says a tune ended; it does not say the next one started. What says that is
    the mix coming up to playing level and staying there, so every call is walked forward
    from the end of its applause to the first window that does — see ``_came_up`` for what
    "staying there" means, and ``DEFAULT_SETTLE_DB`` for what playing level is.

    A start that does not move is the ordinary case on a room mic and on any set where the
    band counts straight in: the first window of the call is already at playing level, so
    the boundary is where it always was and ``talk_seconds`` is zero.

    A call the curve cannot answer for is kept exactly as it came, with ``talk_seconds``
    left at None — too short to hold the window, or outside what the curve covers. That is
    the same rule ``sifted`` follows for a call with no beat grid over it: unknown is not
    zero, and a call is never dropped for want of a measurement. Dropping those as silent
    would file "shorter than ``hold_seconds``" under "the band never came in", which is a
    different finding and a wrong one.
    """
    if hold_seconds <= 0 or not loudness.lufs:
        return Settled(tuple(found), (), ())
    floor = statistics.median(loudness.lufs) - margin_db
    kept: list[Tune] = []
    silent: list[Tune] = []
    brief: list[Tune] = []
    for one in found:
        inside = _inside(loudness, one.start, one.end)
        width = _width(inside, hold_seconds)
        if width is None:
            kept.append(one)
            continue
        came = _came_up(inside, width, floor)
        if came is None:
            silent.append(one)
            continue
        moved = one._replace(
            start=round(came, 3),
            talk_seconds=round(came - one.start, 3),
        )
        (kept if moved.seconds >= minimum_seconds else brief).append(moved)
    return Settled(tuple(kept), tuple(silent), tuple(brief))


def _inside(loudness: Loudness, start: float, end: float) -> list[tuple[float, float]]:
    """The loudness windows that fall inside one call, in the order they were measured."""
    return [
        (seconds, level)
        for seconds, level in zip(loudness.seconds, loudness.lufs, strict=True)
        if start <= seconds < end
    ]


def _width(inside: Sequence[tuple[float, float]], hold_seconds: float) -> int | None:
    """How many windows ``hold_seconds`` is, or None if this call cannot hold that many.

    None is "no measurement here", not "nothing found": a call shorter than the hold, or
    one the curve does not reach, has no window to judge and is left alone by ``settled``.
    """
    if len(inside) < 2:
        return None
    step = statistics.median(
        later - earlier for (earlier, _), (later, _) in zip(inside, inside[1:], strict=False)
    )
    if step <= 0:
        return None
    width = max(int(round(hold_seconds / step)), 1)
    return width if len(inside) >= width else None


def _came_up(inside: Sequence[tuple[float, float]], width: int, floor: float) -> float | None:
    """The first window in the call that is over the floor and mostly stays over it.

    "Mostly" is ``SETTLE_SHARE`` of the next ``width`` windows, counted off a running sum
    rather than a window per frame — a set is tens of thousands of windows and this runs
    once per call. The window that starts the hold has to be over the floor itself, so the
    boundary lands on the music rather than on the last quiet second before it.
    """
    over = [1 if level >= floor else 0 for _, level in inside]
    running = 0
    counts: list[int] = []
    for index, one in enumerate(over):
        running += one
        if index >= width:
            running -= over[index - width]
        if index >= width - 1:
            counts.append(running)
    for index, count in enumerate(counts):
        if over[index] and count >= SETTLE_SHARE * width:
            return inside[index][0]
    return None


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


def rows(found: Sequence[Tune]) -> tuple[dict[str, Any], ...]:
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
            "talk_seconds": one.talk_seconds,
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


def quiet_calls(
    found: Settled,
    margin_db: float,
    minimum_seconds: float,
) -> tuple[dict[str, Any], ...]:
    """What the settle step took out, each with the reason it went — same shape as a drop.

    Beside ``dropped_calls`` on disk and for the same reason (#38): these are the calls the
    applause proposed and the loudness curve refused, and a boundary set nobody can audit
    is one nobody can trust. Bounded the same way too — at most one per burst.
    """
    return tuple(
        [
            {
                "t": one.start,
                "end": one.end,
                "seconds": one.seconds,
                "talk_seconds": one.talk_seconds,
                "reason": (
                    "no music: never came up to within "
                    f"{margin_db} dB of the file's median loudness"
                ),
            }
            for one in found.silent
        ]
        + [
            {
                "t": one.start,
                "end": one.end,
                "seconds": one.seconds,
                "talk_seconds": one.talk_seconds,
                "reason": (
                    f"too little music: {one.seconds}s of playing after "
                    f"{one.talk_seconds}s of talk, under the {minimum_seconds}s a tune takes"
                ),
            }
            for one in found.brief
        ]
    )


def gist(
    curve: Curve,
    applause: Sequence[Span],
    calls: Calls,
    found: Settled | None = None,
) -> dict[str, Any]:
    """How many tunes, how much clapping, and which tune is the long one — no lists.

    The boundaries themselves are on disk. A set is a dozen records; what belongs in a tool
    result is the shape of the set, and "the file has 12 tunes, the longest starts at 41:12".

    The density check reports the same way, and deliberately does not list what it dropped —
    that goes on disk with the boundaries, in ``dropped_calls``. What a caller needs inline
    is whether the floor was anywhere near a decision, so it gets the two shoulders: the
    densest call that was dropped and the sparsest that was kept. A wide gap between them
    says the filter did not have to choose; a narrow one says the threshold wants looking at.

    The settle step reports the same three numbers for the same reason — how many starts it
    moved, how much announcement it moved them past, and how many calls it refused
    outright. A set where nothing moved was read off the applause alone; one where every
    start moved a minute is a board mix, and that is worth knowing without opening the file.
    """
    longest = max(calls.kept, key=lambda one: one.seconds, default=None)
    shortest = min(calls.kept, key=lambda one: one.seconds, default=None)
    moved = [one for one in calls.kept if one.talk_seconds]
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
        "settled": len(moved),
        "settled_seconds": round(sum(one.talk_seconds or 0.0 for one in moved), 3),
        "no_music": (len(found.silent) + len(found.brief)) if found is not None else 0,
    }


def _pulse(one: Tune) -> float:
    """A call's density for ordering, with "never measured" sorting above every measured one."""
    return float("inf") if one.beats_per_second is None else one.beats_per_second


def _summary(one: Tune | None) -> dict[str, Any] | None:
    if one is None:
        return None
    return {"t": one.start, "seconds": one.seconds, "beats_per_second": one.beats_per_second}
