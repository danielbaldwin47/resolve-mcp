"""Drum-fill candidates: where the kit stops keeping time and starts announcing something.

The cut reacts to fills (#22, story 45), so the edit needs to know where they are. This is
the rule layer over two readings that already exist — drum-stem hits (#36 separation, then
``drums``) and the beat grid (#37) — and it is deliberately rules rather than a model:

* **A fill is a local departure, not an absolute density.** A brushed ballad and a burning
  up-tempo differ by an order of magnitude in hits per second, and neither is filling all
  the time. So everything is measured against the median beat of this performance: busy
  means busy *for this drummer, tonight*.

* **Toms are the tell.** Kick and snare keep time; toms are mostly reserved for fills, so
  tom activity on its own is enough to nominate a beat, and tom share is a quarter of the
  confidence. Cymbals would be the other tell, and there is no cymbal stem — the
  decomposition is kick, snare and toms (#36) — so a fill's crash resolution is inferred
  from a hit landing on the following downbeat rather than heard.

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
import json
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..audio import separator, wav
from ..audio.stems import DRUM_STEMS
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..naming import slug
from . import beats as beats_module
from . import drums, music, records

KIND = "detect_drum_fills"
FILLS = "fills"
PLACES = 3

TOM_STEM = "toms"
BUSY_MULTIPLE = 1.6
"""How much busier than the median beat a beat must be before it is part of a fill."""
STRONG_MULTIPLE = 3.0
"""The ratio at which the density factor is already as convinced as it gets."""
MINIMUM_HITS = 3
"""Hits in a beat below which "busier than usual" is noise, not a fill."""
TOM_HITS = 2
"""Toms in one beat are a fill on their own, however quiet the beat reads."""
MAXIMUM_BEATS = 8
"""Two bars in four. Longer is a solo, and this file does not answer that question."""
MAXIMUM_GAP_BEATS = 1
"""One beat the detector found nothing in does not end a fill that carries on after it."""
TOM_SHARE = 0.5
"""The tom share of a run at which the tom factor is fully convinced."""
RESOLUTION_BEATS = 0.25
"""How near the resolution point a hit must land to count as landing on it."""
PHRASE_BARS = 4
"""Bars to the phrase. A fill into bar 5 of 8 is doing more work than one into bar 4."""
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
    resolves_at: float
    resolves_into_bar: int | None
    factors: dict[str, float]
    confidence: float


class Detection(NamedTuple):
    """What was kept, what was weighed, and the baseline everything was weighed against."""

    candidates: tuple[Candidate, ...]
    considered: int
    dropped: int
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

    busy = [
        (density >= baseline * BUSY_MULTIPLE and sum(tally.values()) >= MINIMUM_HITS)
        or tally[TOM_STEM] >= TOM_HITS
        for tally, density in zip(tallies, densities, strict=True)
    ]
    times = sorted(hit.seconds for hit in hits)

    kept: list[Candidate] = []
    considered = 0
    dropped = 0
    for first, last in _runs(busy):
        if last - first + 1 > MAXIMUM_BEATS:
            dropped += 1
            continue
        considered += 1
        candidate = _weighed(beats, edges, tallies, baseline, times, first, last)
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
            tallies[index][hit.stem] += 1
    return tallies


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


def _weighed(
    beats: Sequence[Mapping[str, Any]],
    edges: Sequence[float],
    tallies: Sequence[Counter[str]],
    baseline: float,
    times: Sequence[float],
    first: int,
    last: int,
) -> Candidate:
    """One run turned into a candidate: its span, its counts, and how sure the rules are."""
    counts: Counter[str] = Counter()
    for tally in tallies[first : last + 1]:
        counts.update(tally)
    total = sum(counts.values())
    start, end = edges[first], edges[last + 1]
    length = last - first + 1
    density = total / (end - start)

    into = beats[last + 1] if last + 1 < len(beats) else None
    tolerance = RESOLUTION_BEATS * (end - start) / length
    factors = {
        "density": _clamp((density / baseline - 1.0) / (STRONG_MULTIPLE - 1.0)),
        "toms": _clamp(counts[TOM_STEM] / total / TOM_SHARE) if total else 0.0,
        "resolution": 1.0 if _hit_near(times, end, tolerance) else 0.0,
        "phrase": _phrase(into),
    }
    return Candidate(
        start=round(start, PLACES),
        end=round(end, PLACES),
        beats=length,
        beat=int(beats[first]["beat"]),
        bar=int(beats[first]["bar"]),
        in_bar=int(beats[first]["in_bar"]),
        hits=total,
        counts={stem: counts[stem] for stem in sorted(counts)},
        density=round(density, PLACES),
        density_ratio=round(density / baseline, PLACES),
        resolves_at=round(end, PLACES),
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
            "resolves_at": one.resolves_at,
            "resolves_into_bar": one.resolves_into_bar,
            "hits": one.hits,
            **{stem: one.counts.get(stem, 0) for stem in DRUM_STEMS},
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
    key = cache.cache_key(KIND, [identity, *_stem_inputs(found)], settings)

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
    source = Path(audio)
    if not source.is_file():
        raise InvalidRequestError(
            cause=f"There is no file at {source}.",
            fix=(
                "Pass the master mix the stems were separated from — the beat grid comes from "
                "it, and fills are reported against that grid."
            ),
            detail={"requested": str(source)},
        )
    return source


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
    if not found:
        raise InvalidRequestError(
            cause="None of the drum stems were named, so there is nothing to look for fills in.",
            fix=(
                "Pass the drums mapping a separate_stems job returned, or its directory. The "
                f"stems this reads are {', '.join(DRUM_STEMS)}."
            ),
            detail={"wanted": list(DRUM_STEMS), "given": given},
        )
    return dict(sorted(found.items()))


def _collected(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        raise InvalidRequestError(
            cause=f"There is no directory at {directory}.",
            fix="Pass the directory a separate_stems job reported, or its drums mapping.",
            detail={"requested": str(directory)},
        )
    return {
        label: path
        for label, path in separator.collect(directory).items()
        if label in DRUM_STEMS
    }


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


def _stem_inputs(found: Mapping[str, Path]) -> list[dict[str, Any]]:
    """Identity for the stems, fingerprinted rather than hashed — the path is the hash.

    Every other input to an analysis job is hashed when this server wrote it, because a
    false hit would attribute one concert's readings to another. Stems are the exception
    and get away with it: a separation writes into a directory named after its own cache
    key, which is derived from the content hash of the audio it separated. The path already
    carries the content identity, size and mtime catch a rewrite in place, and reading three
    concert-length WAVs would stall a starter whose whole job is to hand back an id at once.
    """
    return [{"stem": label, **cache.fingerprint(path)} for label, path in sorted(found.items())]


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
    """The worker: the grid, then the hits, then the rules over both."""
    config = config or get_config()
    config.analysis_dir.mkdir(parents=True, exist_ok=True)
    described = wav.describe(source)
    identity = dict(identity) if identity is not None else cache.identity(source, config.audio_dir)
    key = key or cache.cache_key(KIND, [identity, *_stem_inputs(stems)], dict(settings))

    progress(0.05, "reading the beat grid")
    grid = _beat_grid(source, described, identity, detector, refresh, config)

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


def _beat_grid(
    source: Path,
    described: Mapping[str, Any],
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> list[dict[str, Any]]:
    """The numbered beats, read back from the half ``analyze_music`` shares with this job.

    Sharing the entry rather than the code is the point: a master the agent already ran
    music analysis over does not pay for the beat model twice (#22, story 26).
    """
    document = music.beats_half(
        source,
        described=dict(described),
        identity=dict(identity),
        detector=detector,
        refresh=refresh,
        config=config,
    )
    written = json.loads(Path(document["path"]).read_text(encoding="utf-8"))
    return list(written[music.BEATS])
