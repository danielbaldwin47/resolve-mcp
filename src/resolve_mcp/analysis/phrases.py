"""Phrase boundaries: where the soloist stops talking, which is where a cut goes.

The #46 director round said this more clearly than any spec did: the phrase is the
cut-placement unit. Ten-plus of its fifty-five notes move a cut to "after the sax's
phrase" or "between phrases", and transient distance — the thing the stack could
already measure — turned out to be the *residue* of that rule rather than the rule. Nothing
in the analysis stack named a phrase, so an authoring agent read them by ear off the mix and
a reviewing agent could not check phrase placement at all. This is the measurement that makes
both possible.

A phrase ends when the player stops, and there are three ways to hear that:

* **The rest.** The plainest one and the strongest: the line goes quiet and comes back. The
  gap is measured in beats rather than seconds, because half a second is a breath at 80 and
  an entire bar at 200.
* **The held note.** A phrase very often ends on its longest note — the player leans on the
  last one and lets it ring. So a note much longer than the median note *of this solo* is
  evidence of an ending even when the next note comes straight in on top of it.
* **The contour reset.** A new phrase usually does not start where the last one left off; it
  jumps, most often up and back to the top of the range. A leap of a fifth or more across a
  note ending is the third cue, and it is the one that catches an ending with no rest and no
  ritard at all.

Any single cue nominates; all four factors (the three above plus where the ending lands on
the grid) then score it, exactly as ``fills`` scores a drum-fill candidate, and for the same
reason: this file reports a reading with a confidence, and whether a given ending is the end
of a chorus, a breath mid-sentence or the tenor stopping to let the piano answer is the
reading agent's call.

Two placements are reported, and the distinction matters for cutting. ``measured`` is where
the line actually stopped. ``seconds`` is where the cut is *called*: the first beat of the
grid that falls inside the rest, because that is the frame an editor puts the cut on and a
phrase ending three hundredths of a beat before it is the same event. When no beat falls in
the rest the nearest one within ``SNAP_BEATS`` stands in, and past that the measured time is
reported unsnapped and says so — the same rule ``solos`` uses for a change point, and for the
same reason.

What no seam here covers: whether these boundaries are the ones a *director* would name. The
fake tier proves the rules fire on audio whose phrasing is known because it was written; only
the #46 tune, whose phrase-motivated cut points are annotated record frames, can say whether
the reading agrees with a human ear. That evaluation is a live-ish pass over real media and
belongs on the ticket, not in this module's tests.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..audio import wav
from ..audio.stems import FOUR_STEMS
from ..config import Config, get_config
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from . import beats as beats_module
from . import halves, melody, music, records, solos

log = get_logger("analysis")

KIND = "detect_phrases"
PHRASES = "phrases"
PLACES = 3

DEFAULT_STEM = solos.RESIDUAL
"""``other`` — the stem the horns and the piano land in, which is where a solo is."""
MELODY_STEMS = (solos.RESIDUAL, "vocals")
"""The stems a line is plausibly in. Naming another is allowed and logged, not refused."""

REST_BEATS = 0.5
"""A gap this wide is the player breathing rather than tonguing the next note."""
REST_FULL_BEATS = 2.0
"""The gap at which the rest factor is already as convinced as it gets."""
HELD_MULTIPLE = 1.6
"""How much longer than the median note a note must be before its length nominates it."""
LONG_MULTIPLE = 3.0
"""The ratio at which the held factor is fully convinced."""
RESET_SEMITONES = 7.0
"""A fifth: the leap at which a contour reset is worth weighing at all."""
RESET_FULL_SEMITONES = 12.0
"""An octave, at which the contour factor is as convinced as it gets.

Apart from ``RESET_SEMITONES`` on purpose, the way ``HELD_MULTIPLE`` is apart from
``LONG_MULTIPLE`` and ``REST_BEATS`` from ``REST_FULL_BEATS``. One threshold doing both jobs
makes the factor binary — every leap that nominates also saturates — and a cue that is only
ever 0 or 1 cannot be turned down by any floor. A bare fifth should read as the marginal
evidence it is.
"""
MINIMUM_PHRASE_BEATS = 2.0
"""Half a bar in four. Two endings closer than this are one ending, heard twice."""
SNAP_BEATS = 1.0
"""How far the called placement may move to reach the grid before it stops being the same event."""

CUES = ("rest", "held", "contour")
"""The three ways of hearing an ending. Each stands alone; see ``_scored``."""
CUE_WEIGHT = 0.55
"""What the strongest cue is worth by itself — enough to clear any floor worth having."""
AGREEMENT_WEIGHT = 0.2
"""What the other two cues add when they agree with it."""
GRID_WEIGHT = 0.25
"""What the placement is worth. Scored apart from the cues, because it is not evidence."""
DEFAULT_MINIMUM_CONFIDENCE = 0.35
"""Below this a boundary is counted but not written — the floor on what is worth reading."""


class Boundary(NamedTuple):
    """One phrase ending. ``seconds`` is where it is called; ``measured`` is where it was heard."""

    seconds: float
    measured: float
    resumes: float | None
    snapped: bool
    beat: int
    bar: int
    in_bar: int
    downbeat: bool
    rest: float | None
    held: float
    held_ratio: float
    interval: float | None
    notes: int
    factors: dict[str, float]
    confidence: float


class Detection(NamedTuple):
    """What was kept, what was weighed, and the note length everything was weighed against."""

    boundaries: tuple[Boundary, ...]
    considered: int
    dropped: int
    baseline: float


class Reading(NamedTuple):
    """The solo as the rules see it: the line, the grid, and the two norms they are measured on.

    Every rule needs several of these and all of them come off the same two inputs, so they
    travel as one thing rather than as five arguments in the same order.
    """

    notes: Sequence[melody.Note]
    grid: Sequence[Mapping[str, Any]]
    times: Sequence[float]
    beat: float
    baseline: float


# --- the rule layer -----------------------------------------------------------------


def boundaries(
    notes: Sequence[melody.Note],
    grid: Sequence[Mapping[str, Any]],
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
) -> Detection:
    """Phrase boundaries over ``notes``, placed against the numbered beat ``grid``.

    ``grid`` is what ``beats.numbered`` produces and the beats half writes: one record per
    beat carrying its time, bar and whether it starts one.
    """
    played = sorted(notes, key=lambda one: (one.seconds, one.end))
    rows = list(grid)
    if len(played) < 2 or len(rows) < 2:
        return Detection((), 0, 0, 0.0)

    times = [float(row["t"]) for row in rows]
    steps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    usable = [one for one in steps if one > 0]
    baseline = statistics.median([one.held for one in played])
    if not usable or baseline <= 0:
        # No tempo to measure a rest in, or no note that lasted: nothing here can be read.
        return Detection((), 0, 0, 0.0)

    reading = Reading(played, rows, times, statistics.median(usable), baseline)
    kept: list[Boundary] = []
    considered = 0
    dropped = 0
    anchor = -1
    for index in range(len(played)):
        if not _nominated(reading, index):
            continue
        considered += 1
        candidate = _weighed(reading, index, anchor)
        if candidate.confidence < minimum_confidence:
            continue
        if kept and candidate.measured - kept[-1].measured < MINIMUM_PHRASE_BEATS * reading.beat:
            dropped += 1
            continue
        kept.append(candidate)
        anchor = index
    return Detection(tuple(kept), considered, dropped, baseline)


def _nominated(reading: Reading, index: int) -> bool:
    """Whether this note ending is worth weighing. Any one of the three cues is enough.

    The last note of the line is always nominated: the player stopped, and whatever else that
    is, it is the end of a phrase.
    """
    rest = _rest(reading, index)
    if rest is None:
        return True
    note = reading.notes[index]
    leap = _interval(reading, index)
    return (
        rest >= REST_BEATS * reading.beat
        or note.held >= HELD_MULTIPLE * reading.baseline
        or (leap is not None and abs(leap) >= RESET_SEMITONES)
    )


def _weighed(reading: Reading, index: int, anchor: int) -> Boundary:
    """One note ending turned into a boundary: where it is called, and how sure the rules are.

    ``anchor`` is the note index the previous boundary closed on, or ``-1`` before there is
    one — the only thing that knows where the phrase this ending closes began.
    """
    note = reading.notes[index]
    rest = _rest(reading, index)
    resumes = reading.notes[index + 1].seconds if index + 1 < len(reading.notes) else None
    leap = _interval(reading, index)
    called, snapped = _placed(reading, note.end, resumes)
    row = _at(reading, called)

    factors = {
        "rest": 1.0 if rest is None else _clamp(rest / (REST_FULL_BEATS * reading.beat)),
        "held": _clamp((note.held / reading.baseline - 1.0) / (LONG_MULTIPLE - 1.0)),
        "contour": 0.0 if leap is None else _clamp(abs(leap) / RESET_FULL_SEMITONES),
        "grid": beats_module.bar_line_strength(row),
    }
    return Boundary(
        seconds=round(called, PLACES),
        measured=round(note.end, PLACES),
        resumes=round(resumes, PLACES) if resumes is not None else None,
        snapped=snapped,
        beat=int(row["beat"]) if row is not None else 0,
        bar=int(row["bar"]) if row is not None else 0,
        in_bar=int(row["in_bar"]) if row is not None else 0,
        downbeat=bool(row["downbeat"]) if row is not None else False,
        rest=round(rest, PLACES) if rest is not None else None,
        held=note.held,
        held_ratio=round(note.held / reading.baseline, PLACES),
        interval=round(leap, 2) if leap is not None else None,
        notes=index - anchor,
        factors={name: round(value, PLACES) for name, value in factors.items()},
        confidence=round(scored(factors), PLACES),
    )


def _rest(reading: Reading, index: int) -> float | None:
    """Silence between this note's end and the next one's start. ``None`` at the last note."""
    if index + 1 >= len(reading.notes):
        return None
    return max(reading.notes[index + 1].seconds - reading.notes[index].end, 0.0)


def _interval(reading: Reading, index: int) -> float | None:
    """How far the line jumps across this ending, in semitones.

    ``None`` when either side is unpitched: there is no interval to measure, and a made-up one
    would nominate a cymbal wash as the start of a phrase.
    """
    if index + 1 >= len(reading.notes):
        return None
    before, after = reading.notes[index].hz, reading.notes[index + 1].hz
    if before <= 0.0 or after <= 0.0:
        return None
    return melody.semitones(before, after)


def _placed(reading: Reading, measured: float, resumes: float | None) -> tuple[float, bool]:
    """Where the cut is called: the first beat inside the rest, or the nearest beat to the ending.

    Inside the rest by preference, because that is the only stretch of the timeline where
    nothing is playing and a cut there lands between the phrases rather than over one of them.
    """
    index = bisect.bisect_left(reading.times, measured)
    if index < len(reading.times) and (resumes is None or reading.times[index] <= resumes):
        return reading.times[index], True
    near = beats_module.nearest(reading.times, measured)
    if near is not None and abs(reading.times[near] - measured) <= SNAP_BEATS * reading.beat:
        return reading.times[near], True
    return measured, False


def _at(reading: Reading, seconds: float) -> Mapping[str, Any] | None:
    """The beat the called placement sits in, so a boundary can be named by bar and beat."""
    index = bisect.bisect_right(reading.times, seconds) - 1
    return reading.grid[index] if 0 <= index < len(reading.grid) else None


def scored(factors: Mapping[str, float]) -> float:
    """The confidence. The three cues stand in for each other, so they are not added up.

    ``fills`` sums four weighted factors, and that is right for a drum fill: its density, its
    tom share and its resolution all describe the same burst *at once*, so a burst missing one
    of them really is a weaker candidate. The cues here are alternatives. A phrase can end on a
    rest with no leap and no held note and it has still ended — and under a weighted sum a
    single saturated cue is divided by the three that were never going to fire, landing below
    any floor worth having. That is not a timid reading; it is a detector that only hears
    rests, which is two thirds of what this ticket asked for silently missing.

    So the strongest cue carries the reading, the other two add on top when they corroborate
    it, and the grid is scored separately because it is not evidence that a phrase ended at
    all — only that this is a frame worth cutting on.

    The grid's quarter cannot manufacture a boundary out of nothing, because scoring only ever
    happens to an ending some cue already nominated (``_nominated``). What it can do is carry a
    marginal cue that lands on a bar line over the floor, and that is the intended reading: a
    breath at the top of a four-bar group is likelier to be a phrase than the same breath in
    the middle of bar three.
    """
    ranked = sorted((factors[name] for name in CUES), reverse=True)
    agreement = sum(ranked[1:]) / (len(CUES) - 1)
    return _clamp(
        CUE_WEIGHT * ranked[0] + AGREEMENT_WEIGHT * agreement + GRID_WEIGHT * factors["grid"]
    )


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def rows(detection: Detection) -> tuple[dict[str, Any], ...]:
    """One flat record per boundary — both placements, so a cut can use either.

    The record keys are ``solos.numbered``'s and not this module's field names: ``t`` for the
    time an event is called on and ``measured_t`` for where it was seen is what every record
    file in this stack already says, and a boundary is the same kind of thing as a solo change.
    A reader who greps one analysis document should not have to learn a second vocabulary.
    """
    return tuple(
        {
            "t": one.seconds,
            "measured_t": one.measured,
            "resumes_t": one.resumes,
            "snapped": one.snapped,
            "beat": one.beat,
            "bar": one.bar,
            "in_bar": one.in_bar,
            "downbeat": one.downbeat,
            "rest_seconds": one.rest,
            "held_seconds": one.held,
            "held_ratio": one.held_ratio,
            "interval_semitones": one.interval,
            "notes": one.notes,
            "confidence": one.confidence,
            "factors": one.factors,
        }
        for one in detection.boundaries
    )


def gist(
    detection: Detection,
    minimum_confidence: float,
    stem: str,
    notes: int,
) -> dict[str, Any]:
    """The stats worth returning inline: how many phrases, how long, how sure, and on what."""
    kept = detection.boundaries
    strongest = max(kept, key=lambda one: one.confidence) if kept else None
    spans = [
        later.measured - earlier.measured for earlier, later in zip(kept, kept[1:], strict=False)
    ]
    return {
        "count": len(kept),
        "considered": detection.considered,
        "below_confidence": detection.considered - len(kept) - detection.dropped,
        "too_soon_dropped": detection.dropped,
        "minimum_confidence": round(minimum_confidence, PLACES),
        "median_note_seconds": round(detection.baseline, PLACES),
        "median_phrase_seconds": round(statistics.median(spans), PLACES) if spans else None,
        "notes": notes,
        "stem": stem,
        "snapped": sum(1 for one in kept if one.snapped),
        "mean_confidence": (
            round(statistics.mean(one.confidence for one in kept), PLACES) if kept else None
        ),
        "strongest": (
            {"t": strongest.seconds, "confidence": strongest.confidence} if strongest else None
        ),
    }


# --- the job ------------------------------------------------------------------------


def detect_phrases(
    stems: Mapping[str, str | Path] | str | Path,
    audio: str | Path,
    stem: str = DEFAULT_STEM,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
    detector: beats_module.Detector | None = None,
    reader: melody.Reader | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start the phrase job. Returns the job record, not the boundaries."""
    config = config or get_config()
    source = _readable(audio)
    found = _stem(stems, stem)
    halves.sane_floor(minimum_confidence, DEFAULT_MINIMUM_CONFIDENCE, writes="boundary")

    settings = {"stem": stem, "minimum_confidence": float(minimum_confidence)}
    identity = halves.identity(source, config)
    key = _key(identity, stem, found, settings)

    def work(progress: Progress) -> JobOutput:
        return detect(
            source,
            found,
            settings,
            progress,
            key=key,
            identity=identity,
            detector=detector,
            reader=reader,
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


def _readable(audio: str | Path) -> Path:
    return halves.readable(
        audio,
        "Pass the master mix the stems were separated from — the beat grid comes from it, "
        "and phrase boundaries are placed against that grid.",
    )


def _stem(stems: Mapping[str, str | Path] | str | Path, wanted: str) -> Path:
    """The one stem the line is read off, from a separation's directory or from named paths.

    One stem and not the kit: a phrase belongs to a player, and mixing two stems back together
    to look for it would undo exactly what separation was for.
    """
    chosen = halves.stem_named(
        stems,
        wanted,
        "to read the line off",
        (
            "Pass the directory a separate_stems job reported — its first pass writes "
            f"{', '.join(FOUR_STEMS)} — or name one of the stems that is there with the "
            "stem argument."
        ),
    )
    if wanted not in MELODY_STEMS:
        # Not refused: a director with a real horn stem should not have to argue with this.
        # But bass and drums hold a line only in the sense that everything does, and a document
        # read back weeks later should say which stem produced these phrases.
        log.warning(
            "Reading phrases off the %s stem, which is not one of %s — expect a busy reading",
            wanted,
            ", ".join(MELODY_STEMS),
        )
    return chosen


def _key(
    identity: Mapping[str, Any],
    stem: str,
    path: Path,
    settings: Mapping[str, Any],
) -> str:
    """The job's key. One function, because the starter and the worker must agree on it.

    The stem is fingerprinted rather than hashed, which is the one place this pillar departs
    from "hash what the server wrote" — see ADR 0003.
    """
    inputs = [dict(identity), {"stem": stem, **cache.fingerprint(path)}]
    return cache.cache_key(KIND, inputs, dict(settings))


def detect(
    source: Path,
    stem: Path,
    settings: Mapping[str, Any],
    progress: Progress,
    key: str | None = None,
    identity: Mapping[str, Any] | None = None,
    detector: beats_module.Detector | None = None,
    reader: melody.Reader | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: the grid, then the line, then the rules over both.

    The grid comes from the half ``analyze_music`` writes, under that half's own key: a master
    the agent already ran music analysis over does not pay for the beat model a second time
    (#22, story 26).
    """
    config = config or get_config()
    config.analysis_dir.mkdir(parents=True, exist_ok=True)
    described = wav.describe(source)
    known = dict(identity) if identity is not None else halves.identity(source, config)
    label = str(settings["stem"])
    key = key or _key(known, label, stem, settings)

    progress(0.05, "reading the beat grid")
    grid = music.numbered_beats(source, described, known, detector, refresh, config)

    progress(0.25, "reading the melodic line")
    notes = melody.read(stem, reader)

    progress(0.8, "looking for phrase boundaries")
    floor = float(settings["minimum_confidence"])
    detection = boundaries(notes, grid, floor)
    summary = gist(detection, floor, label, len(notes))

    progress(0.9, "writing the boundaries")
    target = config.analysis_dir / f"{slug(source.stem, 'analysis')}-{key[:12]}-{PHRASES}.json"
    header = {
        "kind": PHRASES,
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **summary,
    }
    records.write(target, header, PHRASES, rows(detection))

    progress(0.95, "written")
    return JobOutput({"path": str(target), **summary}, (target,))
