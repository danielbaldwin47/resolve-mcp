"""The bar map: which beat is the "1", when the beat model will not say.

``beats`` reports what the model gave — beat times, and the subset it called downbeats. On
straight-ahead material the model commits and that is the whole story. On the corpus anchor
it does not: over a jazz set it tracks the *swung eighth* as the beat (0.28 s, "214 bpm")
and marks every one of them a downbeat, so ``beats.meter`` reads 1, ``beats.trust`` refuses
the grid whole, and nothing downstream can cut on the "1", hold a shot for a phrase, or
express a style rule about the top of a chorus (#180, gauntlet gap G2). A grid with no bars
in it is a metronome, not a form.

This module is a rule layer over that grid, the way ``phrases`` is over ``melody`` and
``fills`` is over ``drums``: no second model, no second beat detection. Two decisions, in
order, and each one is arithmetic over the grid plus one number per beat saying how loud
the music is there.

* **Fold to the tactus.** A grid running faster than anything a listener would tap is a
  subdivision, not the beat. Keeping every k-th beat, for the k that lands the pulse in the
  tapping range and sits on the accents, recovers the beat the band is playing. A grid
  already inside that range is left exactly as it is — the backbeat is the loudest thing in
  most of this music, and a fold chasing accents alone would report a half-note pulse for
  every rock tune in the corpus.

* **Find the bar line.** Over the folded pulse, try each meter and each phase, and take the
  one whose beats sit on the accents by the widest margin over the runner-up. The phase is
  half the answer and the easier half to get wrong: a four with the bar line on the "3" is
  worse than no bar map at all, because everything downstream would believe it.

**And refuse when neither reading is worth having.** The failure this exists to end is a
grid quietly reporting ``meter: 1`` and callers doing bar arithmetic on top of it. A map
that cannot be found is reported as ``source: "refused"`` with the grid's own reading
attached, and the confidence that fell short — never as a meter of one.

The accent reading is injectable for the reason the beat model is (ADR 0002): reading RMS
off a decoded file is I/O, and *which* beats are loud is the entire input to both decisions,
so the decisions are tested against readings written by hand. It defaults to the master mix
and can be pointed at a stem instead — on this idiom the bass walks quarters, which is the
strongest witness to the tactus there is, and the drums are brushes, which is the weakest.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..audio import wav
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from . import beats as beats_module
from . import halves, music

log = get_logger("analysis")

KIND = "detect_bars"
BARS = "bars"
PLACES = 3
BPM_PLACES = 2
"""Tempo carries two decimals, everything else three — ``beats.gist``'s precision for a bpm."""

TACTUS_LOW_BPM = 60.0
TACTUS_HIGH_BPM = 200.0
"""The range a pulse has to fall in to be the beat someone would tap.

Wide on purpose at both ends — a ballad head and a burner are both in here — and the top is
what makes the fold fire at all. Above 200 the reading calls a pulse a subdivision, which is
a corpus-fitted boundary rather than a law: a genuine 214 bpm quarter exists and would be
folded to 107 by this module. That trade is deliberate. In this material the fast reading is
wrong far more often than it is right, and the tempo the fold chose is reported beside the
grid's own so a reader can see the disagreement rather than inherit it.
"""

FOLD_CANDIDATES = (2, 3, 4)
"""How many grid beats might make one tactus beat. One is not a candidate — it is the answer
when no fold applies, and searching it would let a backbeat outscore the beat itself."""

METER_CANDIDATES = (4, 3, 2)
"""The meters looked for, commonest first.

Five and seven are left out. They alias badly against these three at the sample sizes a
single tune gives, and admitting them buys one correct reading per corpus at the price of
several confident wrong ones — the failure mode this module exists to prevent. A tune in
five that the *model* commits to still maps correctly: that path takes the grid at its word.
"""

MINIMUM_BEATS = 4
"""Beats needed before a fold is worth scoring — below this the intervals are noise."""

MINIMUM_BARS = 2
"""Bars a candidate barring must produce; one bar is a definition, not a reading."""

FOLD_MARGIN = 0.15
"""How far the accents must favour a fold before the reading credits them for it.

Below this the fold is on tempo alone — legitimate, since a pulse outside the tapping range
cannot be the beat whatever the accents say, but a weaker claim, and ``reason`` says which
of the two happened.

A floor, not the whole test: see ``_accent_floor``. On a short span this number is *under*
what noise scores, and a threshold below the noise floor is not a threshold.
"""

NOISE_SIGMAS = 3.0
"""How far above what noise would give a contrast has to sit to count as evidence.

A z-scored contrast between two halves of a reading has a standard error of roughly
``sqrt(1/n + 1/m)`` even when there is nothing there — 0.18 over a 128-beat fixture, which is
*above* ``FOLD_MARGIN``, and 0.02 over a seventy-four-minute set, which is far below it. A
fixed threshold therefore means two different things at the two ends of that range: on the
long span it is a real bar, and on the short one it admits pure noise. Three standard errors
is the usual place to put "this is not chance", and taking the larger of the two floors keeps
both ends honest.
"""

FULL_CONTRAST = 1.0
FULL_MARGIN = 0.5
"""What counts as a sure reading, in standard deviations of the accent curve.

A candidate whose beats average a full standard deviation above the rest, and lead the
runner-up by half of one, is as certain as this reading gets; the two are averaged into the
confidence. Standard deviations rather than decibels because the question is comparative —
whether *these* beats are the loud ones here — and a quiet tune and a loud one must answer
it on the same scale.
"""

CONTRAST_WEIGHT = 0.35
MARGIN_WEIGHT = 0.25
AGREEMENT_WEIGHT = 0.4
"""How the three pieces of evidence are weighed, agreement heaviest.

Contrast and margin are both taken over the whole span at once, and the first real run showed
what that misses: over sixty-second windows of one tune, the mix scored a confident-looking
0.3–0.6 and the windows *disagreed with each other* — fold 2 then 3 then 1, meter 4 then 2
then 3, inside a single tune at one tempo. Every one of those readings cleared a floor set on
contrast alone. Agreement is the check that catches it, so it carries the most weight: a
meter that holds for eight bars and not the next eight is not the meter, whatever its
contrast, and this material is where that distinction decides.
"""

AGREEMENT_WINDOW_BARS = 4
"""Bars per window when the barring is checked against itself.

Four is the group (``beats.GROUP_BARS``), and the smallest window that can hold a bar line in
every position it might occupy. Longer windows would agree more often by averaging the
disagreement away, which is the failure being checked for.
"""

DEFAULT_MINIMUM_CONFIDENCE = 0.3
"""Below this the reading is refused rather than written. See ``_confidence`` for the parts."""

DEFAULT_STEM = "bass"
"""The stem asked for when a separation is passed and no stem is named.

The bass, because on this idiom it is the one instrument that states the pulse on every beat
of it: a walking line plays a note per quarter whether the drummer is on brushes or the room
swallowed the kit. It is a witness to the *tactus* far more than to the bar — where the root
falls is a harmonic question this reading does not ask — which is why it is a default and not
a requirement.
"""

ACCENT_WINDOW_SECONDS = 0.12
"""How much audio after a beat is read as that beat's accent.

Long enough to hold the attack and the body of a note at any tempo in range, short enough
that at 200 bpm it stays inside its own beat.
"""

ACCENT_WINDOW_BEATS = 33
"""Beats used as the local reference an accent is read against.

The reading is comparative — is *this* beat the loud one around here — and "around here" has
to be local for the reason ``beats.STEADINESS_WINDOW`` is: a two-hour set has quiet tunes and
loud ones, an applause gap between every pair of them, and a solo that builds over four
minutes. Scored set-wide, all of that lands in the standard deviation and the bar-level
accent it is supposed to measure disappears under it — the first real run over the Zinc set
returned a contrast of 0.03 against grids the arithmetic reads perfectly on a fixture, which
is that swamping and nothing else (#180).

Thirty-three of the grid's own beats, which on a subdivision-scale grid is about two bars in
four: long enough to have a median worth the name, short enough that the reference moves with
the music. Odd so the window is centred on the beat it judges.
"""

MODEL_SHARE = 0.8
"""The share of bars that must be the modal length before the model's own barring is used.

A tracker that keeps the form for a chorus and then loses it produces a grid that is part
bar map and part noise, and averaging the two would report the form as the answer for the
whole tune. Either it holds nearly throughout, or the map is inferred from scratch.
"""

MODEL = "model"
INFERRED = "inferred"
REFUSED = "refused"

GIVEN = "given"
ACCENT = "accent"
TEMPO = "tempo"
"""Why the pulse is what it is: the grid as handed over, the accents, or the rate alone.

``MODEL`` is a fourth: a map taken from the model's own downbeats never consulted the fold,
and reporting ``given`` there would read as a fold verdict on a fold that never ran.
"""


class Tactus(NamedTuple):
    """The pulse the bars are counted in: which grid beats it kept, and why those."""

    fold: int
    phase: int
    beats: tuple[int, ...]
    contrast: float
    reason: str


class Barring(NamedTuple):
    """A meter and the beat of the pulse the bar starts on, with the evidence for it."""

    meter: int
    phase: int
    contrast: float
    margin: float


class Bar(NamedTuple):
    """One bar: where it starts, how long it is, and where it sits in the four-bar group."""

    bar: int
    t: float
    seconds: float | None
    beats: int
    beat: int
    in_group: int


class BarMap(NamedTuple):
    """The whole reading — the bars, how they were arrived at, and how sure that is."""

    bars: tuple[Bar, ...]
    meter: int | None
    source: str
    confidence: float
    tactus: Tactus
    reasons: dict[str, Any]


Accent = Callable[[Path, Sequence[float]], tuple[float, ...]]
"""How loud the music is at each of these times, on any scale, one value per time."""


# --- the two decisions ------------------------------------------------------------------


def tactus(times: Sequence[float], salience: Sequence[float]) -> Tactus:
    """The pulse to count bars in: the grid as given, or every k-th beat of it.

    A grid whose own rate is already inside the tapping range is returned untouched, without
    consulting the accents at all. That is not caution, it is the correct reading: on a
    four-four grid at 120 the loudest beats are two and four, and a fold that followed the
    accents would return the backbeat as the pulse and put every bar line half a bar late.
    The fold exists for a grid that *cannot* be the tactus because it is too fast, and only
    then does the question "which of its beats are the real ones" arise.
    """
    _parallel(times, salience)
    everything = tuple(range(len(times)))
    given = _bpm(times)
    if len(times) < MINIMUM_BEATS or given is None:
        return Tactus(1, 0, everything, 0.0, GIVEN)
    if TACTUS_LOW_BPM <= given <= TACTUS_HIGH_BPM:
        return Tactus(1, 0, everything, 0.0, GIVEN)

    scored: list[Tactus] = []
    for fold in FOLD_CANDIDATES:
        for phase in range(fold):
            picked = tuple(range(phase, len(times), fold))
            if len(picked) < MINIMUM_BEATS:
                continue
            rate = _bpm([times[one] for one in picked])
            if rate is None or not TACTUS_LOW_BPM <= rate <= TACTUS_HIGH_BPM:
                continue
            found = _contrast(salience, picked)
            floor = _accent_floor(len(picked), len(times) - len(picked))
            reason = ACCENT if found >= floor else TEMPO
            scored.append(Tactus(fold, phase, picked, found, reason))
    if not scored:
        return Tactus(1, 0, everything, 0.0, GIVEN)
    # Nothing clearing the margin means the accents did not choose, and where more than one
    # fold lands in the tapping range the *rate* has not chosen either — so ordering those
    # candidates by a contrast of 0.01 against 0.05 would be picking the pulse out of noise
    # and reporting it as a reading. The least aggressive fold wins instead: halving a grid
    # is the smaller claim than thirding it, and a deterministic prior beats a coin flip
    # dressed as evidence. This is the octave error every tempo tracker has, answered the
    # way the rest of this module answers a question it cannot settle — by assuming least
    # and saying so in ``reason``.
    if all(one.reason == TEMPO for one in scored):
        return min(scored, key=lambda one: (one.fold, one.phase))
    # Ties go to the smallest fold: two candidates the accents cannot separate are the same
    # claim, and the one that throws away fewer beats is the one that assumed less.
    return max(scored, key=lambda one: (round(one.contrast, 6), -one.fold))


def barring(salience: Sequence[float]) -> Barring | None:
    """Which meter and which phase put the bar line on the accents, over one pulse.

    Every candidate is scored the same way and the winner carries its lead over the runner-up
    — over *any* runner-up, not just the ones in another meter, because a four with the bar
    line on the wrong beat is the failure that matters and it competes with the right one
    inside its own meter.
    """
    if len(salience) < MINIMUM_BARS * min(METER_CANDIDATES):
        return None
    scored: list[Barring] = []
    for meter in METER_CANDIDATES:
        for phase in range(meter):
            picked = tuple(range(phase, len(salience), meter))
            if len(picked) < MINIMUM_BARS:
                continue
            scored.append(Barring(meter, phase, _contrast(salience, picked), 0.0))
    if not scored:
        return None
    ranked = sorted(scored, key=lambda one: (-round(one.contrast, 6), one.meter, one.phase))
    best = ranked[0]
    runner_up = ranked[1].contrast if len(ranked) > 1 else 0.0
    return best._replace(margin=max(best.contrast - runner_up, 0.0))


def agreement(salience: Sequence[float], read: Barring) -> float | None:
    """How much of the span reaches the same barring on its own — or ``None`` if too short.

    The span-wide score says the bar line is where the accents are *on average*, and average
    is exactly the wrong reading when a tracker loses the form halfway or the meter was never
    there: a span whose first half says four-on-one and whose second says three-on-two scores
    respectably and describes nothing. This asks each window the same question in isolation
    and counts the ones that answer the way the whole span did — same meter, and a phase that
    continues the same bar line rather than merely sharing a number.

    A window that found no contrast at all is left out of the count rather than counted as
    agreeing. It has nothing to agree *about*: every candidate ties, the first one wins by
    the sort order, and a flat reading would otherwise come back as unanimous — the most
    confident answer this module can give, on the evidence that says the least.

    ``None`` where fewer than two windows are left, because one window agreeing with itself
    is not evidence and scoring it as agreement would be inventing a verdict.
    """
    width = AGREEMENT_WINDOW_BARS * read.meter
    starts = range(0, len(salience) - width + 1, width)
    windows = [(start, barring(salience[start : start + width])) for start in starts]
    checked = [
        (start, found) for start, found in windows if found is not None and found.contrast > 0
    ]
    if len(checked) < 2:
        return None
    return sum(
        1
        for start, found in checked
        if found.meter == read.meter and (start + found.phase) % read.meter == read.phase
    ) / len(checked)


def _accent_floor(inside: int, outside: int) -> float:
    """The contrast a reading has to beat here before it is evidence rather than arithmetic.

    ``FOLD_MARGIN`` is how much of a lean is musically worth acting on; this is how much of a
    lean a reading with nothing in it produces anyway, at *these* sample sizes. The larger of
    the two is the real threshold, and which one dominates flips with the length of the span:
    a fixture of a hundred beats is all noise floor and a set of ten thousand is all margin.
    """
    if inside <= 0 or outside <= 0:
        return FOLD_MARGIN
    return max(FOLD_MARGIN, NOISE_SIGMAS * math.sqrt(1.0 / inside + 1.0 / outside))


def _contrast(salience: Sequence[float], chosen: Sequence[int]) -> float:
    """How far the chosen beats sit above the rest, in standard deviations of the whole.

    Zero when nothing is excluded and zero when the reading is flat: both are "this says
    nothing", and inventing a number for either is how a refusal turns into a verdict.
    """
    picked = set(chosen)
    inside = [value for index, value in enumerate(salience) if index in picked]
    outside = [value for index, value in enumerate(salience) if index not in picked]
    if not inside or not outside:
        return 0.0
    spread = statistics.pstdev(salience)
    if spread <= 0:
        return 0.0
    return (statistics.mean(inside) - statistics.mean(outside)) / spread


def levelled(
    salience: Sequence[float],
    window: int = ACCENT_WINDOW_BEATS,
) -> tuple[float, ...]:
    """The accent reading against the music around it, so a whole set scores like one bar.

    Each beat divided by the median of its window: a beat twice as loud as its neighbours
    reads as 2 whether the band is playing a ballad or a shout chorus. Without this the two
    decisions here are taken over a standard deviation dominated by the arc of the set, and
    the bar-level accent they are looking for is a rounding error inside it.

    A window with no level at all — a stretch of true silence — is left as zeros rather than
    divided by, since a silent bar has no accent and inventing one for it would be a verdict.
    """
    if not salience:
        return ()
    half = window // 2
    scaled: list[float] = []
    for index in range(len(salience)):
        start = max(0, index - half)
        local = statistics.median(salience[start : start + window])
        scaled.append(salience[index] / local if local > 0 else 0.0)
    return tuple(scaled)


def committed(grid: Sequence[Mapping[str, Any]]) -> int | None:
    """The meter the model itself committed to, or ``None`` if it did not commit.

    ``beats.meter`` answers "what bar length did this grid produce most often", which is a
    reading of any grid at all — including the one that called every beat a downbeat and
    reads as a meter of one. This asks the stricter question a bar map needs: did the model
    hold that length nearly throughout, or is the modal bar just the commonest kind of noise?
    """
    if not grid:
        return None
    lengths = Counter(row["bar"] for row in grid)
    if not lengths:
        return None
    sizes = list(lengths.values())
    modal = statistics.mode(sizes)
    if modal < beats_module.MINIMUM_METER:
        return None
    share = sum(1 for one in sizes if one == modal) / len(sizes)
    return modal if share >= MODEL_SHARE else None


# --- the map ------------------------------------------------------------------------------


def mapped(
    grid: Sequence[Mapping[str, Any]],
    salience: Sequence[float],
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
) -> BarMap:
    """The bar map for one grid: the model's barring, an inferred one, or a refusal.

    The reading is levelled against the music around it before either decision is taken —
    see ``levelled``. Once, here, rather than inside each decision, so the two are always
    scoring the same numbers.
    """
    _parallel(grid, salience)
    salience = levelled(salience)
    reasons = _reading(grid)
    whole = tuple(range(len(grid)))
    if not grid:
        return BarMap((), None, REFUSED, 0.0, Tactus(1, 0, whole, 0.0, GIVEN), reasons)

    found = committed(grid)
    if found is not None:
        reasons = {**reasons, "meter_source": MODEL}
        # The fold was never consulted, so its reason must not read as a fold verdict: this
        # pulse is the grid's own beats because the model's own downbeats number them.
        pulse = Tactus(1, 0, whole, 0.0, MODEL)
        sure = _held(grid, found)
        # The floor gates this path too. A model that held its meter for four bars in five is
        # a real reading and a weak one, and a caller who said 0.9 asked not to be handed it —
        # "taken at the model's word" is about where the barring came from, not about being
        # exempt from the question every other map answers.
        if sure < minimum_confidence:
            return BarMap((), None, REFUSED, sure, pulse, reasons)
        return BarMap(_from_model(grid), found, MODEL, sure, pulse, reasons)

    pulse = tactus([float(row["t"]) for row in grid], salience)
    pulsed = [salience[one] for one in pulse.beats]
    read = barring(pulsed)
    held = agreement(pulsed, read) if read is not None else None
    reasons = {
        **reasons,
        "meter_source": INFERRED,
        "tactus_bpm": _rounded(_bpm([float(grid[one]["t"]) for one in pulse.beats])),
        "fold": pulse.fold,
        "fold_phase": pulse.phase,
        "fold_reason": pulse.reason,
        "fold_contrast": round(pulse.contrast, PLACES),
        "meter_contrast": round(read.contrast, PLACES) if read else 0.0,
        "meter_margin": round(read.margin, PLACES) if read else 0.0,
        "meter_agreement": round(held, PLACES) if held is not None else None,
    }
    if read is None:
        return BarMap((), None, REFUSED, 0.0, pulse, reasons)

    sure = _confidence(read, held)
    if sure < minimum_confidence:
        return BarMap((), None, REFUSED, sure, pulse, reasons)
    return BarMap(_inferred(grid, pulse, read), read.meter, INFERRED, sure, pulse, reasons)


def _confidence(read: Barring, held: float | None) -> float:
    """How sure the barring is: above the rest, above the runner-up, and holding throughout.

    A span too short to check against itself is scored on the first two alone and rescaled,
    rather than treated as disagreeing: eight bars is a real reading and refusing it for
    being eight bars would be answering a different question. The span *is* shorter evidence,
    which is what the rescale says — the same two numbers can no longer be corroborated.
    """
    scored = CONTRAST_WEIGHT * _clamp(read.contrast / FULL_CONTRAST) + MARGIN_WEIGHT * _clamp(
        read.margin / FULL_MARGIN
    )
    if held is None:
        return _clamp(scored / (CONTRAST_WEIGHT + MARGIN_WEIGHT))
    return _clamp(scored + AGREEMENT_WEIGHT * _clamp(held))


def _reading(grid: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the grid said about itself, carried whatever this module concludes.

    A refusal that did not say ``grid_meter: 1`` would be the same silence the ticket is
    about, one level further out.
    """
    return {
        "beats": len(grid),
        "grid_meter": beats_module.meter(grid),
        "grid_bpm": _rounded(_bpm([float(row["t"]) for row in grid])),
    }


def _held(grid: Sequence[Mapping[str, Any]], meter: int) -> float:
    """The share of the model's own bars that came out the length it mostly used."""
    sizes = list(Counter(row["bar"] for row in grid).values())
    return round(sum(1 for one in sizes if one == meter) / len(sizes), PLACES) if sizes else 0.0


def _from_model(grid: Sequence[Mapping[str, Any]]) -> tuple[Bar, ...]:
    """The bars the grid already draws, as bar records — the model's downbeats, renumbered."""
    starts: list[int] = []
    seen: int | None = None
    for index, row in enumerate(grid):
        if row["bar"] != seen:
            starts.append(index)
            seen = int(row["bar"])
    ends = starts[1:] + [len(grid)]
    return _built(grid, starts, [one - other for other, one in zip(starts, ends, strict=True)])


def _inferred(
    grid: Sequence[Mapping[str, Any]],
    pulse: Tactus,
    read: Barring,
) -> tuple[Bar, ...]:
    """The bars a fold and a barring imply, back in the grid's own beat numbering.

    A phase past zero leaves beats in front of the first bar line; they are a pickup and get
    bar one, which is what ``beats.rows`` does with the beats before the model's first
    downbeat. Numbering them zero, or dropping them, would leave the first real bar line
    somewhere other than the top of a group.
    """
    lines = list(range(read.phase, len(pulse.beats), read.meter))
    if read.phase > 0:
        lines.insert(0, 0)
    spans = [one - other for other, one in zip(lines, lines[1:] + [len(pulse.beats)], strict=True)]
    return _built(grid, [pulse.beats[one] for one in lines], spans)


def _built(
    grid: Sequence[Mapping[str, Any]],
    starts: Sequence[int],
    spans: Sequence[int],
) -> tuple[Bar, ...]:
    """Bar records from the grid indices each bar starts on and how many beats each holds.

    The last bar's length is ``None`` rather than "to the end of the grid": the grid stops
    where the analysed audio stops, and reporting that distance as a bar length would make
    the shortest bar of every tune an artefact of where the file was cut.
    """
    built: list[Bar] = []
    for number, (start, span) in enumerate(zip(starts, spans, strict=True), start=1):
        seconds = (
            round(float(grid[starts[number]]["t"]) - float(grid[start]["t"]), PLACES)
            if number < len(starts)
            else None
        )
        built.append(
            Bar(
                bar=number,
                t=round(float(grid[start]["t"]), PLACES),
                seconds=seconds,
                beats=span,
                beat=int(grid[start]["beat"]),
                in_group=(number - 1) % beats_module.GROUP_BARS + 1,
            )
        )
    return tuple(built)


def _parallel(grid: Sequence[Any], salience: Sequence[float]) -> None:
    if len(grid) != len(salience):
        raise ValueError(
            f"The accent reading has {len(salience)} values for {len(grid)} beats; "
            "it is read at the beat times and must be parallel to them."
        )


def _bpm(times: Sequence[float]) -> float | None:
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    usable = [one for one in gaps if one > 0]
    return 60.0 / statistics.median(usable) if usable else None


def _rounded(value: float | None) -> float | None:
    """A tempo, at the precision ``beats.gist`` already reports one — not ``PLACES``.

    Two decimals rather than three because that is what every other bpm in this stack says,
    and a reader comparing this module's tempo against the grid's should not have to notice
    that one of them carries a digit the other does not.
    """
    return round(value, BPM_PLACES) if value is not None else None


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


# --- what goes to disk ---------------------------------------------------------------------


def rows(bar_map: BarMap) -> tuple[dict[str, Any], ...]:
    """One flat record per bar — a downbeat time, and what that bar is."""
    return tuple(one._asdict() for one in bar_map.bars)


def gist(bar_map: BarMap, minimum_confidence: float, stem: str | None) -> dict[str, Any]:
    """The stats worth returning inline: what meter, from what, and how sure.

    Scalars only, like every gist here — the bars themselves are the file. ``grid_meter`` and
    ``grid_bpm`` ride home beside the answer rather than staying on disk, because the reading
    a caller most needs to see is the disagreement: 214 and a meter of one against 107 in four
    is the whole of what this pass did.
    """
    first = bar_map.bars[0] if bar_map.bars else None
    last = bar_map.bars[-1] if bar_map.bars else None
    return {
        "count": len(bar_map.bars),
        "meter": bar_map.meter,
        "source": bar_map.source,
        "confidence": round(bar_map.confidence, PLACES),
        "minimum_confidence": round(minimum_confidence, PLACES),
        # Reported inline rather than left in the reasons: it is the number that says whether
        # the span agreed with itself, and a reader deciding whether to trust a map at all
        # needs it beside the confidence rather than in the file.
        "agreement": bar_map.reasons.get("meter_agreement"),
        "tempo_bpm": bar_map.reasons.get("tactus_bpm", bar_map.reasons.get("grid_bpm")),
        "grid_bpm": bar_map.reasons.get("grid_bpm"),
        "grid_meter": bar_map.reasons.get("grid_meter"),
        "fold": bar_map.tactus.fold,
        "fold_reason": bar_map.tactus.reason,
        "group_bars": beats_module.GROUP_BARS,
        "stem": stem,
        "first_seconds": first.t if first else None,
        "last_seconds": last.t if last else None,
    }


def accents(
    path: Path,
    times: Sequence[float],
    window_seconds: float = ACCENT_WINDOW_SECONDS,
) -> tuple[float, ...]:
    """How loud this audio is at each of these times, scaled so the loudest beat is one.

    The reading is deliberately the crudest thing that answers the question — RMS over a
    short window starting at the beat, no filtering, no onset detection. Both decisions here
    are comparative and z-scored, so an accent curve only has to rank the beats correctly;
    everything a fuller reading would add is precision the ranking does not use.

    Scaled by the loudest beat so a document written weeks apart from two masters at
    different levels reads the same, and zero throughout for silence rather than a division
    by nothing.
    """
    import numpy as np

    from . import decode

    audio = decode.read(path)
    mono = audio.mono()
    width = max(int(window_seconds * audio.sample_rate), 1)
    raw: list[float] = []
    for seconds in times:
        start = max(int(seconds * audio.sample_rate), 0)
        chunk = mono[start : start + width]
        raw.append(float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0)
    loudest = max(raw, default=0.0)
    return tuple(round(one / loudest, 6) if loudest > 0 else 0.0 for one in raw)


# --- the job --------------------------------------------------------------------------------


def detect_bars(
    audio: str | Path,
    stems: Mapping[str, str | Path] | str | Path | None = None,
    stem: str = DEFAULT_STEM,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
    detector: beats_module.Detector | None = None,
    accent: Accent | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start the bar-map job. Returns the job record, not the map.

    **Map one tune, not one set.** The whole reading is a single fold, a single meter and a
    single phase, and a set has a different tempo and a different form every tune with a
    gap of applause between them: run over all of it at once, the answer is wrong by
    construction and the evidence averages to nothing. The first run over the Zinc set scored
    0.04 across seventy-four minutes and 0.1–0.6 over sixty-second windows inside one tune of
    it. ``start_seconds`` and ``end_seconds`` bound the span; the tune boundaries
    ``analyze_structure`` writes are where they come from.

    The cache identity is the audio (hashed if this server wrote it, fingerprinted if the
    director handed it over — ``halves.identity``), plus the accent source when one is named:
    the stem's own fingerprint under its label, exactly as the phrase job keys its stem. The
    settings in the key are the stem label, the span, and the confidence floor, because each
    changes what lands on disk — a floor is what turns a weak reading into a refusal, and two
    floors over one master are two different documents. Nothing else is in the key: the beat
    grid this reads is cached under its own half key, so re-asking for another span re-runs
    the arithmetic and never the beat model.
    """
    config = config or get_config()
    source = _readable(audio)
    chosen = _stem(stems, stem) if stems is not None else None
    halves.sane_floor(minimum_confidence, DEFAULT_MINIMUM_CONFIDENCE, writes="bar map")
    _sane_span(start_seconds, end_seconds)

    settings = {
        "stem": stem if chosen is not None else None,
        "start_seconds": None if start_seconds is None else float(start_seconds),
        "end_seconds": None if end_seconds is None else float(end_seconds),
        "minimum_confidence": float(minimum_confidence),
    }
    identity = halves.identity(source, config)
    key = _key(identity, chosen, settings)

    def work(progress: Progress) -> JobOutput:
        return detect(
            source,
            chosen,
            settings,
            progress,
            key=key,
            identity=identity,
            detector=detector,
            accent=accent,
            refresh=refresh,
            config=config,
        )

    return start_job(
        KIND,
        {"audio": source.name, **settings},
        work,
        cache_key=key,
        refresh=refresh,
        config=config,
    )


def _sane_span(start_seconds: float | None, end_seconds: float | None) -> None:
    """Refuse a span that is not one, before a job exists to carry it."""
    if start_seconds is not None and start_seconds < 0:
        raise InvalidRequestError(
            cause=f"The span starts at {start_seconds} s, which is before the audio does.",
            fix="Leave start_seconds off for the whole file, or pass a time at or after zero.",
            detail={"start_seconds": start_seconds},
        )
    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise InvalidRequestError(
            cause=f"The span ends at {end_seconds} s and starts at {start_seconds} s.",
            fix="Pass end_seconds after start_seconds — a tune's boundaries, in that order.",
            detail={"start_seconds": start_seconds, "end_seconds": end_seconds},
        )


def _within(
    grid: Sequence[Mapping[str, Any]],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict[str, Any]]:
    """The beats inside the span, with their grid numbering intact.

    The ``beat`` on each record still counts from the first beat of the *file*, so a bar map
    over one tune and a beat grid over the set say the same thing about the same beat. Only
    the bar numbering is span-local, and it has to be: it is the form of this tune.
    """
    return [
        dict(row)
        for row in grid
        if (start_seconds is None or float(row["t"]) >= start_seconds)
        and (end_seconds is None or float(row["t"]) < end_seconds)
    ]


def _readable(audio: str | Path) -> Path:
    return halves.readable(
        audio,
        "Pass the master mix — the beat grid the bars are counted over comes from it, and "
        "an accent read off one file against a grid from another would be measuring nothing.",
    )


def _stem(stems: Mapping[str, str | Path] | str | Path, wanted: str) -> Path:
    """The stem the accents are read off, from a separation's directory or from named paths.

    Optional, unlike the phrase job's, and the fix says so: the master mix answers this
    question adequately when the drums are loud, and only a capture where they are not —
    brushes, a quiet room — needs the bass line to hear the pulse at all.
    """
    return halves.stem_named(
        stems,
        wanted,
        "to read the accents off",
        (
            "Pass the directory a separate_stems job reported, name one of the stems that "
            "is there with the stem argument, or leave stems off and the master mix is read."
        ),
    )


def _key(
    identity: Mapping[str, Any],
    stem: Path | None,
    settings: Mapping[str, Any],
) -> str:
    """The job's key. One function, because the starter and the worker must agree on it."""
    inputs: list[Mapping[str, Any]] = [dict(identity)]
    if stem is not None:
        inputs.append({"stem": settings["stem"], **cache.fingerprint(stem)})
    return cache.cache_key(KIND, inputs, dict(settings))


def detect(
    source: Path,
    stem: Path | None,
    settings: Mapping[str, Any],
    progress: Progress,
    key: str | None = None,
    identity: Mapping[str, Any] | None = None,
    detector: beats_module.Detector | None = None,
    accent: Accent | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: the grid, then the accents, then the two decisions over both.

    The grid comes from the half ``analyze_music`` writes, under that half's own key, so a
    master the agent already ran music analysis over does not pay for the beat model again.
    """
    config = config or get_config()
    config.analysis_dir.mkdir(parents=True, exist_ok=True)
    described = wav.describe(source)
    known = dict(identity) if identity is not None else halves.identity(source, config)
    label = settings["stem"]
    key = key or _key(known, stem, settings)

    progress(0.05, "reading the beat grid")
    whole = music.numbered_beats(source, described, known, detector, refresh, config)
    grid = _within(whole, settings["start_seconds"], settings["end_seconds"])

    progress(0.5, "reading the accents")
    read = accent or accents
    salience = read(stem or source, [float(row["t"]) for row in grid])

    progress(0.8, "looking for the bar line")
    floor = float(settings["minimum_confidence"])
    bar_map = mapped(grid, salience, floor)
    stats = gist(bar_map, floor, label)
    log.info(
        "Bar map over %s: %s, meter %s, %s bars (grid said meter %s at %s bpm)",
        source.name,
        bar_map.source,
        bar_map.meter,
        len(bar_map.bars),
        bar_map.reasons.get("grid_meter"),
        bar_map.reasons.get("grid_bpm"),
    )

    progress(0.9, "writing the bars")
    target = config.analysis_dir / f"{slug(source.stem, 'analysis')}-{key[:12]}-{BARS}.json"
    result = halves.written(
        target,
        BARS,
        described,
        stats,
        rows(bar_map),
        {
            "start_seconds": settings["start_seconds"],
            "end_seconds": settings["end_seconds"],
            "reasons": bar_map.reasons,
        },
    )

    progress(0.95, "written")
    return JobOutput(result, (target,))
