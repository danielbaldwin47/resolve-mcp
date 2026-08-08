"""What the soloist played, and when — one record per note off the melodic stem.

Phrase placement (#143) is a question about the *line*, not about the mix. A director's "cut
after the sax's phrase" is a statement about where one player stopped, and at that moment the
mix is still full of band, so nothing measured on the master can see it. Separation (#36)
already hands over a stem the horns and the piano land in, and reading notes off that stem is
what turns "the line" into something a rule layer can count.

This is deliberately a **monophonic** reading, and the name says so: these are notes, not a
transcription. The residual stem is not one instrument — piano comping sits under the tenor
in the same file — so the pitch track follows whatever is loudest and a chord reads as one
note. That is enough for phrase boundaries, which are made of note *endings*, rests and
leaps, and it is not enough to write out a solo. Calling it a transcription would be claiming
the second thing.

Three choices are worth naming, because each is a place a different method would differ:

* **Pitch is autocorrelation with the taper divided out.** The plain FFT autocorrelation of an
  N-sample frame falls off as ``(N - lag) / N``, which makes a low note look less certain than
  a high one purely for being low. Dividing that out costs nothing and makes one clarity
  threshold mean the same thing across the range. The period is then the *first local peak*
  within ``OCTAVE_MARGIN`` of the strongest one — because the autocorrelation of anything
  periodic peaks again at twice the period, and taking the strongest peak outright is how a
  pitch tracker reports the octave below.

* **Gating is hysteresis on the stem's own peak, not an absolute level.** A stem is quiet or
  loud according to how the separation scaled it, and the question here is only "is the line
  sounding", so the note starts at ``ONSET_GATE`` of the stem's loudest frame and does not end
  until it falls under ``RELEASE_GATE`` — one threshold would chatter a note into pieces
  across every dip in its own sustain.

* **A frame is dated at its centre**, the way ``solos`` dates an energy window, and the gate
  therefore opens about half a window *before* the note and closes about half a window after
  it. That is a known bias with a known size, so the outer edges of a run are pulled back in
  by half a window; splits *inside* a run are already where the pitch changed and are left
  alone.

The seam is kept for the reason ``drums`` keeps one (ADR 0002): a real monophonic pitch
tracker is a plausible upgrade, and the rule layer downstream never knows the difference.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

from ..errors import AnalysisFailedError, ResolveMcpError
from ..logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - the worker imports this when it runs
    from .decode import Audio

log = get_logger("analysis")

FRAME_SECONDS = 0.043
"""Long enough to hold three periods of the lowest note looked for, short enough to sit inside a
quaver at any tempo a band actually plays."""
HOP_SECONDS = 0.01
MINIMUM_HZ = 80.0
MAXIMUM_HZ = 2_000.0
"""The range a horn, a voice, a guitar or a piano's right hand lives in."""
CLARITY = 0.6
"""How periodic a frame has to read before its peak is called a pitch rather than a coincidence."""
OCTAVE_MARGIN = 0.9
"""A peak this near the strongest is the same peak, and the earlier lag is the real period."""

ONSET_GATE = 0.15
RELEASE_GATE = 0.06
"""Fractions of the stem's own loudest frame — see the hysteresis note above."""
SILENCE = 1e-5
"""Below this the whole stem is silence, and its peak is no baseline to gate against."""
MINIMUM_SECONDS = 0.04
"""Shorter than this is a click or a gate chattering, not a note the line is made of."""
SPLIT_SEMITONES = 1.0
"""Wider than vibrato and narrower than a step: what separates two legato notes."""
CONFIRM_FRAMES = 2
"""Frames the new pitch has to hold before a split, so one confused frame is not a note."""

PLACES = 3


class Note(NamedTuple):
    """One note in the line: when it started, when it stopped, its pitch, how loud it read.

    ``hz`` is ``0.0`` for an event with no fundamental to find — a rimshot that leaked into
    the residual, a cymbal wash — which is an honest reading rather than a missing one: it
    still has a start, an end and a length, and those are what the rest and held cues need.
    """

    seconds: float
    end: float
    hz: float
    strength: float

    @property
    def held(self) -> float:
        """How long the note sounded for."""
        return round(self.end - self.seconds, PLACES)


Reader = Callable[[Path], "tuple[Note, ...]"]
"""Turn one melodic stem into the notes in it, in any order."""


class Frame(NamedTuple):
    """One analysis frame, dated at its centre: how loud it was and what pitch it held."""

    seconds: float
    level: float
    hz: float


def read(stem: Path | str, reader: Reader | None = None) -> tuple[Note, ...]:
    """Every note in the stem, earliest first.

    A reader that falls over is an ``analysis_failed`` rather than an internal error, for the
    same reason a beat model that falls over is: the agent can act on it, and it is not a bug
    in this server.
    """
    chosen = reader or pitched_reader
    target = Path(stem)
    try:
        notes = chosen(target)
    except ResolveMcpError:
        raise
    except Exception as exc:
        raise AnalysisFailedError(
            cause=f"Reading the melodic line failed: {type(exc).__name__}: {exc}.",
            detail={"stem": str(target)},
        ) from exc
    return tuple(sorted(notes, key=lambda one: (one.seconds, one.end)))


def pitched_reader(stem: Path) -> tuple[Note, ...]:
    """The default: frame the stem, gate it on its own peak, and pitch what is sounding."""
    from . import decode

    audio = decode.read(stem)
    found = notes(framed(audio))
    log.info("Read %d notes off the %s stem", len(found), stem.name)
    return found


def pitch(samples: NDArray[np.floating[Any]], sample_rate: int) -> float:
    """The fundamental of one frame in Hz, or ``0.0`` if it does not read as pitched."""
    frame = np.asarray(samples, dtype=np.float64)
    if frame.size < 2 or sample_rate <= 0:
        return 0.0
    frame = frame - frame.mean()

    padded = 1 << int(math.ceil(math.log2(frame.size * 2)))
    spectrum = np.fft.rfft(frame, padded)
    correlation = np.fft.irfft(spectrum * np.conjugate(spectrum), padded)[: frame.size]
    if correlation[0] <= 0.0:
        return 0.0

    lowest = max(int(sample_rate / MAXIMUM_HZ), 1)
    highest = min(int(sample_rate / MINIMUM_HZ), frame.size - 2)
    if highest <= lowest:
        return 0.0

    lags = np.arange(lowest, highest + 1)
    # The taper divided out, so one clarity threshold means the same thing across the range.
    normalised = correlation[lowest : highest + 1] / (frame.size - lags)

    peaks = _peaks(normalised)
    if peaks.size == 0:
        return 0.0
    strongest = float(normalised[peaks].max())
    if strongest < CLARITY * (float(correlation[0]) / frame.size):
        return 0.0

    qualifying = peaks[normalised[peaks] >= OCTAVE_MARGIN * strongest]
    return _interpolated(correlation, int(qualifying[0]) + lowest, sample_rate)


def _peaks(normalised: NDArray[np.float64]) -> NDArray[np.intp]:
    """The local maxima, which are the only lags that are candidate periods.

    Peaks and not merely "the first lag above the margin": below about a fifth of the way up
    the range, the shoulder of the lag-zero peak has not fallen off yet by the time the search
    starts, so the very first lag looked at is already within the margin of the real peak
    further out. Taking it reports the bottom of the search range as the pitch of every low
    note — which is how a G below the stave read as unpitched before this was a rule.
    """
    rising = normalised[1:-1] > normalised[:-2]
    falling = normalised[1:-1] >= normalised[2:]
    return np.flatnonzero(rising & falling) + 1


def _interpolated(correlation: NDArray[np.float64], peak: int, sample_rate: int) -> float:
    """The peak read off the parabola through it and its neighbours, not off the sample it fell on.

    A lag is a whole sample, which at the top of the range is a semitone wide; the three
    points around the peak place it far more finely than that for the cost of one division.
    """
    left, middle, right = correlation[peak - 1], correlation[peak], correlation[peak + 1]
    bend = 2.0 * (2.0 * middle - left - right)
    period = peak + (float(right - left) / bend if bend else 0.0)
    return float(sample_rate / period) if period > 0.0 else 0.0


def framed(audio: Audio) -> tuple[Frame, ...]:
    """The stem as overlapping frames, each dated at its centre, pitched where it is sounding.

    Pitch is only estimated for frames loud enough to be part of a note, because it is the
    expensive half and a concert is mostly not this stem playing.
    """
    mono = np.asarray(audio.mono(), dtype=np.float64)
    width = max(int(FRAME_SECONDS * audio.sample_rate), 2)
    hop = max(int(HOP_SECONDS * audio.sample_rate), 1)
    if mono.size < width or audio.sample_rate <= 0:
        return ()

    middle = width / 2.0 / audio.sample_rate
    starts = range(0, mono.size - width + 1, hop)
    blocks = [mono[first : first + width] for first in starts]
    levels = [float(np.sqrt(np.mean(block * block))) for block in blocks]
    peak = max(levels, default=0.0)
    if peak <= SILENCE:
        return ()

    floor = peak * RELEASE_GATE
    return tuple(
        Frame(
            seconds=first / audio.sample_rate + middle,
            level=level,
            hz=pitch(block, audio.sample_rate) if level >= floor else 0.0,
        )
        for first, block, level in zip(starts, blocks, levels, strict=True)
    )


def notes(frames: Sequence[Frame]) -> tuple[Note, ...]:
    """The frames grouped into notes: gated into runs, then split where the pitch moves."""
    peak = max((frame.level for frame in frames), default=0.0)
    if peak <= SILENCE:
        return ()

    found: list[Note] = []
    for first, last in _sounding(frames, peak * ONSET_GATE, peak * RELEASE_GATE):
        spans = _split(frames, first, last)
        for index, (begin, end) in enumerate(spans):
            note = _note(
                frames,
                begin,
                end,
                opens=index == 0,
                closes=index == len(spans) - 1,
            )
            if note is not None:
                found.append(note)
    return tuple(found)


def _sounding(
    frames: Sequence[Frame],
    start_gate: float,
    stop_gate: float,
) -> list[tuple[int, int]]:
    """Runs of frames the line is sounding in — opened at one gate, closed at the lower one."""
    runs: list[tuple[int, int]] = []
    first: int | None = None
    for index, frame in enumerate(frames):
        if first is None:
            if frame.level >= start_gate:
                first = index
        elif frame.level < stop_gate:
            runs.append((first, index - 1))
            first = None
    if first is not None:
        runs.append((first, len(frames) - 1))
    return runs


def _split(frames: Sequence[Frame], first: int, last: int) -> list[tuple[int, int]]:
    """One sounding run cut where the pitch settles somewhere new — legato notes are still notes.

    The reference is the first pitched frame of the span and is not re-read as the span goes
    on, so vibrato cannot walk it: a note that wanders half a semitone stays one note, and a
    line that steps stays two.
    """
    spans: list[tuple[int, int]] = []
    begin = first
    reference = 0.0
    moved = 0
    for index in range(first, last + 1):
        hertz = frames[index].hz
        if hertz <= 0.0:
            moved = 0
            continue
        if reference <= 0.0:
            reference = hertz
            continue
        if abs(semitones(reference, hertz)) < SPLIT_SEMITONES:
            moved = 0
            continue
        moved += 1
        if moved >= CONFIRM_FRAMES:
            spans.append((begin, index - moved))
            begin = index - moved + 1
            reference = hertz
            moved = 0
    spans.append((begin, last))
    return spans


def _note(
    frames: Sequence[Frame],
    first: int,
    last: int,
    opens: bool,
    closes: bool,
) -> Note | None:
    """One span of frames as a note, with the gate's half-window overhang taken back off.

    ``opens`` and ``closes`` say whether this span is at the outer edge of a sounding run. Only
    there does the gate overhang apply — a split inside a run sits where the pitch changed,
    which is already the right time.
    """
    span = frames[first : last + 1]
    if not span:
        return None
    reach = FRAME_SECONDS / 2.0
    start = span[0].seconds + (reach if opens else 0.0)
    end = span[-1].seconds - (reach if closes else 0.0)
    if end - start < MINIMUM_SECONDS:
        return None
    pitched = [one.hz for one in span if one.hz > 0.0]
    return Note(
        seconds=round(start, PLACES),
        end=round(end, PLACES),
        hz=round(statistics.median(pitched), 2) if pitched else 0.0,
        strength=round(min(max(one.level for one in span), 1.0), 4),
    )


def semitones(before: float, after: float) -> float:
    """How far the line moved, in semitones. ``0.0`` when either end has no pitch to move from."""
    if before <= 0.0 or after <= 0.0:
        return 0.0
    return 12.0 * math.log2(after / before)
