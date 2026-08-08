"""Drum-fill candidates: where the kit stops keeping time and starts announcing something.

The cut reacts to fills (#22, story 45), so the edit needs to know where they are. This is
the rule layer over two readings that already exist — drum-stem hits (#36 separation, then
``drums``) and the beat grid (#37) — and it is deliberately rules rather than a model:

* **A fill is a local departure, not an absolute density.** A brushed ballad and a burning
  up-tempo differ by an order of magnitude in hits per second, and neither is filling all
  the time. So everything is measured against the median beat of this performance: busy
  means busy *for this drummer, tonight*.

* **No instrument is eventful by decree.** A beat is nominated two ways: it is busier than
  the median beat of the performance, or it carries hits some stem does not usually play
  *around here*, measured against that stem's own median over a window of bars. Which
  instruments announce something is a fact about the tune, not about the kit — the toms are
  reserved for fills in rock and are ordinary comping in jazz, and a rule that knew that in
  advance found the real fills in a jazz tune and then discarded them (#125). Tom *share*
  is still a quarter of the confidence, because once a beat is nominated the toms are
  evidence about what kind of event it is.

* **The grid decides the edges.** A candidate starts at a beat and ends at the beat it
  resolves into, because that resolution point is what a cut is placed against. Nothing
  here is reported off-grid.

The output is candidates with confidence, and the word is meant: this file reports a
reading, and whether a given burst is a fill, a solo trading four, or a drummer covering a
mistake is the reading agent's call. That is also why a run longer than two bars is dropped
rather than reported at low confidence — thirty seconds of continuous kit is a drum solo,
which is a different question (#38), and calling it a long fill would be a wrong answer
rather than an unsure one.
"""

from __future__ import annotations

import bisect
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..audio import separator, wav
from ..audio.stems import DRUM_PASS, DRUM_STEMS
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from . import beats as beats_module
from . import drums, halves, music, records

log = get_logger("analysis")

KIND = "detect_drum_fills"
FILLS = "fills"
PLACES = 3

TOM_STEM = "toms"
CYMBAL_STEM = "cymbals"
CYMBAL_SOURCES = ("ride", "crash")
"""Ride and crash are tallied as one stem, because in this idiom they are one gesture.

The ride *becomes* crashy at a phrase end. Counted apart, that reads as a ride dropping out
and a crash appearing — two half-signals that partly cancel — putting a hole in exactly the
moment the cymbals were carried in to catch (#125).
"""
COUNTED_STEMS = tuple(
    dict.fromkeys(CYMBAL_STEM if one in CYMBAL_SOURCES else one for one in DRUM_STEMS)
)
"""The stems as this file counts them: the separated ones, with the cymbals merged into one."""
BUSY_MULTIPLE = 1.6
"""How much busier than the median beat a beat must be before it is part of a fill."""
STRONG_MULTIPLE = 3.0
"""The ratio at which the density factor is already as convinced as it gets."""
MINIMUM_HITS = 3
"""Hits below which "busier than usual" is noise, not a fill.

Read two ways, one per gate: hits in the beat for the density gate, and hits *beyond the
local baseline* for the departure gate. One number rather than two, because it is answering
one question — how small a difference this file is willing to call a difference.
"""
MAXIMUM_BEATS = 8
"""Two bars in four. Longer is a solo, and this file does not answer that question."""
MAXIMUM_GAP_BEATS = 1
"""One beat the detector found nothing in does not end a fill that carries on after it."""
BASELINE_BARS = 16
"""Bars of context a stem's local baseline is taken over.

Deliberately not anchored to the phrase (#125). The only phrase length here is
``PHRASE_BARS``, a constant, so keying the window to it would hard-code phrase length one
level up — the very mistake that ticket exists to undo. The window does not need to know
phrase length; it needs to be wide enough to hold several phrases, and a median over sixteen
bars behaves the same whether they run four, eight or twelve.
"""
EXCLUDED_BEATS = MAXIMUM_BEATS
"""How far either side of a beat is left out of its own baseline.

The longest run this file will call a fill, so a fill can never be a large part of the window
it is judged against. Without this a fill inflates the very median meant to reveal it, which
is this ticket's failure arriving by another door.
"""
DEFAULT_METER = 4
"""Beats to the bar when the grid cannot say.

The guard on a type rather than a state the detector reaches: ``beats.meter`` is typed
``int | None`` and answers ``None`` only for an empty record sequence, which ``candidates``
has already turned away by the time the window is sized.
"""
TOM_SHARE = 0.5
"""The tom share of a run at which the tom factor is fully convinced."""
RESOLUTION_BEATS = 0.25
"""How near the resolution point a hit must land to count as landing on it."""
PHRASE_BARS = 4
"""Bars to the phrase. A fill into bar 5 is doing more work than one into bar 4.

Four rather than eight, settled rather than inherited (#125 asked for the disagreement
between this constant and its old docstring to be resolved deliberately). Phrases in this
material run four, eight or twelve bars, and every eight- and twelve-bar boundary is also a
four-bar one — so four is the divisor that marks all of them, at the cost of also marking
the bar line halfway through a longer phrase. That cost is affordable because this is a
quarter of the confidence and not a gate.
"""
PHRASE_AT_BAR_LINE = 0.6
PHRASE_MID_BAR = 0.15
WEIGHTS = {"density": 0.35, "toms": 0.25, "resolution": 0.15, "phrase": 0.25}
DEFAULT_MINIMUM_CONFIDENCE = 0.35
"""Below this a candidate is counted but not written — the floor on what is worth reading."""


class Candidate(NamedTuple):
    """One run of fill-like playing, with the evidence that nominated it."""

    start: float
    end: float
    beats: int
    beat: int
    bar: int
    in_bar: int
    hits: int
    counts: dict[str, int]
    density: float
    density_ratio: float
    resolves_into_bar: int | None
    factors: dict[str, float]
    confidence: float


class Detection(NamedTuple):
    """What was kept, what was weighed, and the baseline everything was weighed against."""

    candidates: tuple[Candidate, ...]
    considered: int
    dropped: int
    baseline: float


class Reading(NamedTuple):
    """The performance as the rules see it: the grid, its spans, the hits in each, the norm.

    Every one of these is derived from the same two inputs and every rule needs several of
    them, so they travel as one thing rather than as five arguments in the same order.
    """

    grid: Sequence[Mapping[str, Any]]
    edges: Sequence[float]
    tallies: Sequence[Counter[str]]
    times: Sequence[float]
    baseline: float


# --- the rule layer -----------------------------------------------------------------


def candidates(
    hits: Sequence[drums.Hit],
    grid: Sequence[Mapping[str, Any]],
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
) -> Detection:
    """Fill candidates over ``hits``, aligned to the numbered beat ``grid``.

    ``grid`` is what ``beats.numbered`` produces and the beats half writes: one record per
    beat carrying its time, bar and whether it starts one.
    """
    beats = list(grid)
    if len(beats) < 2:
        return Detection((), 0, 0, 0.0)

    edges = _edges(beats)
    tallies = _tally(hits, edges)
    densities = [
        sum(tally.values()) / (edges[index + 1] - edges[index])
        for index, tally in enumerate(tallies)
    ]
    baseline = statistics.median(densities)
    if baseline <= 0:
        # Nothing to be busier than: a kit this quiet has no ordinary beat to depart from.
        return Detection((), 0, 0, 0.0)

    reading = Reading(
        grid=beats,
        edges=edges,
        tallies=tallies,
        times=sorted(hit.seconds for hit in hits),
        baseline=baseline,
    )
    local = _local_baselines(tallies, _bar_length(beats))
    busy = [
        _dense(tally, density, baseline) or _departs(tally, usual)
        for tally, density, usual in zip(tallies, densities, local, strict=True)
    ]

    kept: list[Candidate] = []
    considered = 0
    dropped = 0
    for first, last in _runs(busy):
        if last - first + 1 > MAXIMUM_BEATS:
            dropped += 1
            continue
        considered += 1
        candidate = _weighed(reading, first, last)
        if candidate.confidence >= minimum_confidence:
            kept.append(candidate)
    return Detection(tuple(kept), considered, dropped, baseline)


def _edges(beats: Sequence[Mapping[str, Any]]) -> list[float]:
    """Beat times plus one more for the end of the last beat, so every beat has a span."""
    times = [float(row["t"]) for row in beats]
    intervals = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    usable = [one for one in intervals if one > 0]
    return [*times, times[-1] + (statistics.median(usable) if usable else 1.0)]


def _tally(hits: Sequence[drums.Hit], edges: Sequence[float]) -> list[Counter[str]]:
    """Hits per stem per beat. Anything outside the grid belongs to no beat and is dropped."""
    tallies: list[Counter[str]] = [Counter() for _ in range(len(edges) - 1)]
    for hit in hits:
        index = bisect.bisect_right(edges, hit.seconds) - 1
        if 0 <= index < len(tallies):
            tallies[index][_counted_as(hit.stem)] += 1
    return tallies


def _counted_as(stem: str) -> str:
    """The name a stem is tallied under. The cymbals arrive separated and are counted as one.

    Here rather than in the weighting, so that everything downstream — both gates, the run
    builder, the scorer — sees one cymbal signal instead of separating them and then undoing
    the separation.
    """
    return CYMBAL_STEM if stem in CYMBAL_SOURCES else stem


def _bar_length(beats: Sequence[Mapping[str, Any]]) -> int:
    """Beats to the bar, from the grid's own account of itself."""
    found = beats_module.meter(beats)
    return found if found is not None and found > 0 else DEFAULT_METER


def _dense(tally: Counter[str], density: float, baseline: float) -> bool:
    """Gate one: busier than the median beat of the performance, and busy in absolute terms."""
    return density >= baseline * BUSY_MULTIPLE and sum(tally.values()) >= MINIMUM_HITS


def _departs(tally: Counter[str], usual: Mapping[str, float]) -> bool:
    """Gate two: hits beyond what the stems carrying them usually play around here.

    Only the excess counts, and only upwards. A timekeeper contributes nothing however loud
    it is, because it is already in its own baseline — which is what makes a stem safe to
    carry whether it turns out to be a fill instrument in this performance or the pulse. A
    stem that *stops* is left out on purpose: an absence is not activity, and nominating on
    it would make every rest the start of a fill.
    """
    excess = sum(max(0.0, count - usual.get(stem, 0.0)) for stem, count in tally.items())
    return excess >= MINIMUM_HITS


def _local_baselines(
    tallies: Sequence[Counter[str]],
    bar_length: int,
) -> list[dict[str, float]]:
    """Per beat, per stem: what that stem is usually doing around there.

    Local rather than whole-tune, because a single baseline over the performance would catch
    a drummer who comps on the toms all night and miss one who switches into a tom groove
    halfway — the average lands between the two and describes neither (#125).
    """
    stems = sorted({stem for tally in tallies for stem in tally})
    half = max(1, BASELINE_BARS * bar_length // 2)
    return [
        {stem: _usual(tallies, stem, index, half) for stem in stems}
        for index in range(len(tallies))
    ]


def _usual(
    tallies: Sequence[Counter[str]],
    stem: str,
    index: int,
    half: int,
) -> float:
    """The median count for one stem around one beat, that beat's own neighbourhood left out.

    A grid shorter than the exclusion zone has no context left after the zone is taken out,
    and a baseline of zero there would read every beat as a departure. So the zone is given
    up before the baseline is: on a clip that short, the rest of the clip is the context.
    """
    window = range(max(0, index - half), min(len(tallies), index + half + 1))
    sample = [tallies[other][stem] for other in window if abs(other - index) > EXCLUDED_BEATS]
    if not sample:
        sample = [tallies[other][stem] for other in window if other != index]
    return statistics.median(sample) if sample else 0.0


def _runs(busy: Sequence[bool]) -> list[tuple[int, int]]:
    """Busy beats grouped into runs, bridging a single quiet beat inside one."""
    runs: list[tuple[int, int]] = []
    first: int | None = None
    last = 0
    for index, flag in enumerate(busy):
        if not flag:
            continue
        if first is None:
            first = index
        elif index - last - 1 > MAXIMUM_GAP_BEATS:
            runs.append((first, last))
            first = index
        last = index
    if first is not None:
        runs.append((first, last))
    return runs


def _weighed(reading: Reading, first: int, last: int) -> Candidate:
    """One run turned into a candidate: its span, its counts, and how sure the rules are.

    The end of the span *is* the resolution point — the beat the fill lands into — because
    a run is grown until the playing settles, and that beat is what a cut is placed against.
    """
    counts: Counter[str] = Counter()
    for tally in reading.tallies[first : last + 1]:
        counts.update(tally)
    total = sum(counts.values())
    start, end = reading.edges[first], reading.edges[last + 1]
    length = last - first + 1
    density = total / (end - start)

    into = reading.grid[last + 1] if last + 1 < len(reading.grid) else None
    tolerance = RESOLUTION_BEATS * (end - start) / length
    # Tom share is of the kit without the cymbals, so that carrying two more stems does not
    # quietly lower the score of every tom fill the detector already reads correctly (#125).
    kit = total - counts[CYMBAL_STEM]
    factors = {
        "density": _clamp((density / reading.baseline - 1.0) / (STRONG_MULTIPLE - 1.0)),
        "toms": _clamp(counts[TOM_STEM] / kit / TOM_SHARE) if kit else 0.0,
        "resolution": 1.0 if _hit_near(reading.times, end, tolerance) else 0.0,
        "phrase": _phrase(into),
    }
    return Candidate(
        start=round(start, PLACES),
        end=round(end, PLACES),
        beats=length,
        beat=int(reading.grid[first]["beat"]),
        bar=int(reading.grid[first]["bar"]),
        in_bar=int(reading.grid[first]["in_bar"]),
        hits=total,
        counts={stem: counts[stem] for stem in sorted(counts)},
        density=round(density, PLACES),
        density_ratio=round(density / reading.baseline, PLACES),
        resolves_into_bar=int(into["bar"]) if into is not None else None,
        factors={name: round(value, PLACES) for name, value in factors.items()},
        confidence=round(
            _clamp(sum(WEIGHTS[name] * value for name, value in factors.items())), PLACES
        ),
    )


def _phrase(into: Mapping[str, Any] | None) -> float:
    """A fill into the top of a phrase is doing more work than one into any old bar line."""
    if into is None or not into["downbeat"]:
        return PHRASE_MID_BAR
    return 1.0 if (int(into["bar"]) - 1) % PHRASE_BARS == 0 else PHRASE_AT_BAR_LINE


def _hit_near(times: Sequence[float], seconds: float, tolerance: float) -> bool:
    index = bisect.bisect_left(times, seconds - tolerance)
    return index < len(times) and times[index] <= seconds + tolerance


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def rows(detection: Detection) -> tuple[dict[str, Any], ...]:
    """One flat record per candidate — per-stem counts inlined so a grep on toms works."""
    return tuple(
        {
            "start": one.start,
            "end": one.end,
            "duration": round(one.end - one.start, PLACES),
            "beat": one.beat,
            "bar": one.bar,
            "in_bar": one.in_bar,
            "beats": one.beats,
            "resolves_into_bar": one.resolves_into_bar,
            "hits": one.hits,
            **{stem: one.counts.get(stem, 0) for stem in COUNTED_STEMS},
            "density": one.density,
            "density_ratio": one.density_ratio,
            "confidence": one.confidence,
            "factors": one.factors,
        }
        for one in detection.candidates
    )


def gist(
    detection: Detection,
    minimum_confidence: float,
    stems: Sequence[str],
    hits: int,
) -> dict[str, Any]:
    """The stats worth returning inline: how many, how sure, and what they were measured against."""
    kept = detection.candidates
    strongest = max(kept, key=lambda one: one.confidence) if kept else None
    return {
        "count": len(kept),
        "considered": detection.considered,
        "below_confidence": detection.considered - len(kept),
        "long_runs_dropped": detection.dropped,
        "minimum_confidence": round(minimum_confidence, PLACES),
        "baseline_hits_per_second": round(detection.baseline, PLACES),
        "hits": hits,
        "stems": list(stems),
        "mean_confidence": (
            round(statistics.mean(one.confidence for one in kept), PLACES) if kept else None
        ),
        "strongest": (
            {"t": strongest.start, "confidence": strongest.confidence} if strongest else None
        ),
    }


# --- the job ------------------------------------------------------------------------


def detect_drum_fills(
    stems: Mapping[str, str | Path] | str | Path,
    audio: str | Path,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
    detector: beats_module.Detector | None = None,
    transcriber: drums.Transcriber | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start the fill job. Returns the job record, not the candidates."""
    config = config or get_config()
    source = _readable(audio)
    found = _stems(stems)
    _sane_floor(minimum_confidence)

    settings = {"minimum_confidence": float(minimum_confidence)}
    identity = cache.identity(source, config.audio_dir)
    key = _key(identity, found, settings)

    def work(progress: Progress) -> JobOutput:
        return detect(
            source,
            found,
            settings,
            progress,
            key=key,
            identity=identity,
            detector=detector,
            transcriber=transcriber,
            refresh=refresh,
            config=config,
        )

    return start_job(
        KIND,
        {"audio": source.name, "stems": sorted(found), **settings},
        work,
        cache_key=key,
        refresh=refresh,
        config=config,
    )


def _readable(audio: str | Path) -> Path:
    return halves.readable(
        audio,
        "Pass the master mix the stems were separated from — the beat grid comes from it, "
        "and fills are reported against that grid.",
    )


def _stems(stems: Mapping[str, str | Path] | str | Path) -> dict[str, Path]:
    """The drum stems to read, from a separation's directory or from named paths.

    Either shape a ``separate_stems`` result offers works, and both are narrowed to the
    decomposed drum stems: handed the whole four-stem mapping, this reads the kit and not
    the horns.
    """
    if isinstance(stems, str | Path):
        given: list[str] = [str(stems)]
        found = _collected(Path(stems))
    else:
        given = sorted(str(label) for label in stems)
        found = {
            str(label): Path(path) for label, path in stems.items() if str(label) in DRUM_STEMS
        }
        _all_on_disk(found)
    if found and len(found) < len(DRUM_STEMS):
        # Not refused: some stems beat none. But a kit missing its toms scores every
        # candidate with a tom factor of zero, and that is worth a line in the log rather
        # than a mysteriously timid confidence read off a document weeks later.
        log.warning(
            "Looking for fills in %s only — %s missing, so confidence will read low",
            ", ".join(sorted(found)),
            ", ".join(one for one in DRUM_STEMS if one not in found),
        )
    if not found:
        raise InvalidRequestError(
            cause="None of the drum stems were named, so there is nothing to look for fills in.",
            fix=(
                "Pass the directory a separate_stems job reported — its second pass writes the "
                f"{', '.join(DRUM_STEMS)} this reads. A four-stem separation on its own is not "
                "enough; the drum pass is what fills are found in."
            ),
            detail={"wanted": list(DRUM_STEMS), "given": given},
        )
    return dict(sorted(found.items()))


def _collected(directory: Path) -> dict[str, Path]:
    """The drum stems under a separation's directory — the pass's own, or the parent of it.

    A separation writes its two passes into ``<directory>/mix`` and ``<directory>/drums``,
    and the job reports the parent. That parent is what an agent has in hand, so it is what
    this accepts, and the drum pass inside it is looked in first. The parent itself is
    checked too, because a director who copied three stems into a folder of their own is
    doing something reasonable and should not have to name a subdirectory that is not there.
    """
    if not directory.is_dir():
        raise InvalidRequestError(
            cause=f"There is no directory at {directory}.",
            fix="Pass the directory a separate_stems job reported, or the drum pass inside it.",
            detail={"requested": str(directory)},
        )
    for candidate in (directory / DRUM_PASS, directory):
        found = {
            label: path
            for label, path in separator.collect(candidate).items()
            if label in DRUM_STEMS
        }
        if found:
            return found
    return {}


def _all_on_disk(found: Mapping[str, Path]) -> None:
    missing = sorted(label for label, path in found.items() if not path.is_file())
    if missing:
        raise InvalidRequestError(
            cause=f"These drum stems are not on disk: {', '.join(missing)}.",
            fix=(
                "Run separate_stems again — the cache drops an entry whose files went missing, "
                "so asking for them redoes the separation."
            ),
            detail={"missing": [str(found[label]) for label in missing]},
        )


def _sane_floor(minimum_confidence: float) -> None:
    if not 0.0 <= minimum_confidence <= 1.0:
        raise InvalidRequestError(
            cause="The confidence floor is a fraction between 0 and 1.",
            fix=f"The default is {DEFAULT_MINIMUM_CONFIDENCE}; 0 writes every candidate.",
            detail={"minimum_confidence": minimum_confidence},
        )


def _key(
    identity: Mapping[str, Any],
    stems: Mapping[str, Path],
    settings: Mapping[str, Any],
) -> str:
    """The job's key. One function, because the starter and the worker must agree on it.

    The stems are fingerprinted rather than hashed, which is the one place this pillar
    departs from "hash what the server wrote" — see ADR 0003.
    """
    inputs = [
        dict(identity),
        *({"stem": label, **cache.fingerprint(path)} for label, path in sorted(stems.items())),
    ]
    return cache.cache_key(KIND, inputs, dict(settings))


def detect(
    source: Path,
    stems: Mapping[str, Path],
    settings: Mapping[str, Any],
    progress: Progress,
    key: str | None = None,
    identity: Mapping[str, Any] | None = None,
    detector: beats_module.Detector | None = None,
    transcriber: drums.Transcriber | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: the grid, then the hits, then the rules over both.

    The grid comes from the half ``analyze_music`` writes, under that half's own key: a
    master the agent already ran music analysis over does not pay for the beat model a
    second time (#22, story 26).
    """
    config = config or get_config()
    config.analysis_dir.mkdir(parents=True, exist_ok=True)
    described = wav.describe(source)
    known = dict(identity) if identity is not None else cache.identity(source, config.audio_dir)
    key = key or _key(known, stems, settings)

    progress(0.05, "reading the beat grid")
    grid = music.numbered_beats(source, described, known, detector, refresh, config)

    progress(0.25, "transcribing the drum stems")
    hits = drums.transcribe(stems, transcriber)

    progress(0.8, "looking for fills")
    floor = float(settings["minimum_confidence"])
    detection = candidates(hits, grid, floor)
    summary = gist(detection, floor, sorted(stems), len(hits))

    progress(0.9, "writing the candidates")
    target = config.analysis_dir / f"{slug(source.stem, 'analysis')}-{key[:12]}-{FILLS}.json"
    header = {
        "kind": FILLS,
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **summary,
    }
    records.write(target, header, FILLS, rows(detection))

    progress(0.95, "written")
    return JobOutput({"path": str(target), **summary}, (target,))


