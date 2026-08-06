"""The structure analysis job: where the tunes are, and where the front changes hands.

Two halves, because they answer the two questions a concert's shape is made of (#22, story
29) and they need different substrate:

* **Tune boundaries** come off the master mix. Applause is the only reliable segmentation a
  jazz set offers — there is no verse and no chorus to find — so the mix is tagged for it
  and the music between the bursts is the tune. This is what a ``songs.json`` author reads
  before placing a single marker (#22, story 33).

* **Solo changes** come off the stems, because "who is out front" is not a question the mix
  can answer. They are also snapped to downbeats, which means this job needs a beat grid —
  and it reads the one ``analyze_music`` writes rather than paying for a second detection,
  because both halves are keyed on the audio rather than on the job that asked (see
  ``halves``). Ask for solo changes on audio whose beats are already analysed and the beat
  model does not run at all.

Nothing here touches Resolve, and neither half is on by default in the sense that matters:
each drags in its own heavy dependency — a tagger for one, a separation run for the other —
so a caller asks for what it is prepared to pay for and gets a structured error naming the
install when it is not there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..audio import separator
from ..audio import stems as stems_module
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from . import applause as applause_module
from . import beats as beats_module
from . import halves, music, records
from . import solos as solos_module

log = get_logger("analysis")

KIND = "analyze_structure"
TUNES = "tunes"
SOLOS = "solos"

APPLAUSE_SHAPE = ("threshold", "burst_seconds", "gap_seconds", "tune_seconds")
SOLO_SHAPE = ("window_seconds", "hop_seconds", "solo_seconds", "margin_db", "semitones")


def analyze_structure(
    audio: str | Path,
    tunes: bool = True,
    solos: bool = False,
    stems: str | Path | None = None,
    threshold: float = applause_module.DEFAULT_THRESHOLD,
    burst_seconds: float = applause_module.DEFAULT_MINIMUM_SECONDS,
    gap_seconds: float = applause_module.DEFAULT_GAP_SECONDS,
    tune_seconds: float = applause_module.DEFAULT_TUNE_SECONDS,
    window_seconds: float = solos_module.DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = solos_module.DEFAULT_HOP_SECONDS,
    solo_seconds: float = solos_module.DEFAULT_MINIMUM_SECONDS,
    margin_db: float = solos_module.DEFAULT_MARGIN_DB,
    semitones: float = solos_module.DEFAULT_SEMITONES,
    snap_seconds: float = solos_module.DEFAULT_SNAP_SECONDS,
    refresh: bool = False,
    tagger: applause_module.Tagger | None = None,
    detector: beats_module.Detector | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start the structure job. Returns the job record, not the structure."""
    config = config or get_config()
    source = halves.readable(audio)
    _asked_for_something(tunes, solos)
    _sane_numbers(threshold, window_seconds, hop_seconds)
    found = _stems(stems, solos)

    settings: dict[str, Any] = {
        TUNES: tunes,
        SOLOS: solos,
        "threshold": float(threshold),
        "burst_seconds": float(burst_seconds),
        "gap_seconds": float(gap_seconds),
        "tune_seconds": float(tune_seconds),
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
        "solo_seconds": float(solo_seconds),
        "margin_db": float(margin_db),
        "semitones": float(semitones),
        "snap_seconds": float(snap_seconds),
    }
    identity = halves.identity(source, config)
    key = cache.cache_key(KIND, [identity, _stem_identity(found, config)], settings)

    def work(progress: Progress) -> JobOutput:
        return analyze(
            source,
            settings,
            progress,
            identity=identity,
            stems=found,
            tagger=tagger,
            detector=detector,
            refresh=refresh,
            config=config,
        )

    return start_job(
        KIND,
        {"audio": source.name, "stems": str(stems) if stems else None, **settings},
        work,
        cache_key=key,
        refresh=refresh,
        config=config,
    )


def _asked_for_something(tunes: bool, solos: bool) -> None:
    if not tunes and not solos:
        raise InvalidRequestError(
            cause="Neither tune boundaries nor solo changes were asked for.",
            fix="Leave tunes on, or turn on solos, or both.",
            detail={TUNES: tunes, SOLOS: solos},
        )


def _sane_numbers(threshold: float, window_seconds: float, hop_seconds: float) -> None:
    if not 0.0 < threshold <= 1.0 or window_seconds <= 0 or hop_seconds <= 0:
        raise InvalidRequestError(
            cause="The applause threshold must be a probability, and the windows positive.",
            fix=(
                f"Defaults are threshold={applause_module.DEFAULT_THRESHOLD}, "
                f"window_seconds={solos_module.DEFAULT_WINDOW_SECONDS}, "
                f"hop_seconds={solos_module.DEFAULT_HOP_SECONDS}."
            ),
            detail={
                "threshold": threshold,
                "window_seconds": window_seconds,
                "hop_seconds": hop_seconds,
            },
        )


def _stems(stems: str | Path | None, solos: bool) -> dict[str, Path]:
    """The separated stems to read, from the directory a ``separate_stems`` job returned.

    That job writes each pass into its own directory, so the path it hands back holds the
    four stems one level down; both that path and the pass directory itself are accepted,
    because an agent reading the job record has one and an agent looking at the disk has
    the other.
    """
    if not solos:
        return {}
    if stems is None:
        raise InvalidRequestError(
            cause="Solo changes are measured on the stems, and no stems directory was given.",
            fix=(
                "Run separate_stems first and pass the directory from its result as stems, "
                "or ask for solos=false."
            ),
        )
    directory = Path(stems)
    inner = directory / stems_module.MIX_PASS
    found = separator.collect(inner if inner.is_dir() else directory)
    if not found:
        raise InvalidRequestError(
            cause=f"There are no separated stems in {directory}.",
            fix=(
                "Pass the directory a separate_stems job returned — the one holding the "
                f"{stems_module.MIX_PASS} pass — and check the job completed."
            ),
            detail={"requested": str(directory)},
        )
    return found


def _stem_identity(found: Mapping[str, Path], config: Config) -> dict[str, Any]:
    """What the stems are, for keying: their own directory name, or their bytes.

    Stems this server separated live in a directory named after the content hash of the
    audio they came from and the models that made them, so the name *is* the identity and
    reading a gigabyte of WAV back to learn what it already says would be waste. Stems from
    anywhere else are fingerprinted one by one, because nothing about the name is known.
    """
    if not found:
        return {}
    directory = next(iter(found.values())).parent.resolve()
    if halves.inside(directory, config.stems_dir):
        keyed = directory.relative_to(config.stems_dir.resolve()).parts[0]
        return {"stems": keyed, "names": sorted(found)}
    return {"files": [cache.fingerprint(found[name]) for name in sorted(found)]}


def analyze(
    source: Path,
    settings: dict[str, Any],
    progress: Progress,
    identity: dict[str, Any] | None = None,
    stems: Mapping[str, Path] | None = None,
    tagger: applause_module.Tagger | None = None,
    detector: beats_module.Detector | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: find the boundaries that were asked for, write them out, return the gist."""
    from ..audio import wav

    config = config or get_config()
    config.analysis_dir.mkdir(parents=True, exist_ok=True)
    identity = identity or halves.identity(source, config)
    stems = stems or {}

    progress(0.05, "reading the audio")
    described = wav.describe(source)
    result: dict[str, Any] = {"audio": halves.audio_gist(described)}
    artifacts: list[Path] = []

    if settings[TUNES]:
        progress(0.1, "listening for applause")
        shape = {name: settings[name] for name in APPLAUSE_SHAPE}
        result[TUNES] = halves.cached(
            f"{KIND}:{TUNES}",
            cache.cache_key(f"{KIND}:{TUNES}", [identity], shape),
            lambda path: _tunes(source, path, described, shape, tagger),
            source,
            refresh,
            config,
        )
        artifacts.append(Path(result[TUNES]["path"]))

    if settings[SOLOS]:
        progress(0.6, "measuring the stems")
        shape = {name: settings[name] for name in SOLO_SHAPE}
        stem_shape = {**shape, "snap_seconds": settings["snap_seconds"]}
        result[SOLOS] = halves.cached(
            f"{KIND}:{SOLOS}",
            cache.cache_key(
                f"{KIND}:{SOLOS}",
                [identity, _stem_identity(stems, config)],
                stem_shape,
            ),
            lambda path: _solos(
                source,
                path,
                described,
                stem_shape,
                stems,
                identity,
                detector,
                refresh,
                config,
            ),
            source,
            refresh,
            config,
        )
        artifacts.append(Path(result[SOLOS]["path"]))

    progress(0.95, "written")
    return JobOutput(result, tuple(artifacts))


def _tunes(
    source: Path,
    target: Path,
    described: Mapping[str, Any],
    shape: Mapping[str, Any],
    tagger: applause_module.Tagger | None,
) -> dict[str, Any]:
    curve = applause_module.tag(source, tagger)
    spans = applause_module.spans(
        curve,
        float(shape["threshold"]),
        float(shape["burst_seconds"]),
        float(shape["gap_seconds"]),
    )
    found = applause_module.tunes(
        spans,
        float(described["duration_seconds"]),
        float(shape["tune_seconds"]),
    )
    gist = {**applause_module.gist(curve, spans, found), **shape}
    log.info("Found %d tunes and %d bursts of applause in %s", len(found), len(spans), source.name)
    return halves.written(target, TUNES, described, gist, applause_module.numbered(found))


def _solos(
    source: Path,
    target: Path,
    described: Mapping[str, Any],
    shape: Mapping[str, Any],
    stems: Mapping[str, Path],
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> dict[str, Any]:
    window = float(shape["window_seconds"])
    hop = float(shape["hop_seconds"])
    minimum = float(shape["solo_seconds"])

    voices = solos_module.voices(stems, window, hop)
    runs = solos_module.runs(voices, float(shape["margin_db"]), minimum)
    stepped = _timbre(stems, window, hop, minimum, float(shape["semitones"]))
    opened = voices[0].seconds[0] if voices and voices[0].seconds else 0.0
    changes = solos_module.snapped(
        solos_module.changes(runs, stepped, window, opened),
        _downbeats(source, described, identity, detector, refresh, config),
        float(shape["snap_seconds"]),
    )
    gist = {
        **solos_module.gist(runs, changes),
        **shape,
        "stem_count": len(stems),
        "residual": solos_module.RESIDUAL in stems,
    }
    log.info("Found %d solo changes across %d stems in %s", len(changes), len(stems), source.name)
    return halves.written(target, SOLOS, described, gist, solos_module.numbered(changes))


def _timbre(
    stems: Mapping[str, Path],
    window: float,
    hop: float,
    minimum: float,
    semitones: float,
) -> tuple[solos_module.Step, ...]:
    """Brightness steps inside the residual stem — nothing, if there is no residual stem."""
    from . import decode

    residual = stems.get(solos_module.RESIDUAL)
    if residual is None:
        return ()
    seconds, hertz = solos_module.brightness(decode.read(residual), window, hop)
    return solos_module.steps(seconds, hertz, semitones, minimum)


def _downbeats(
    source: Path,
    described: Mapping[str, Any],
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> tuple[float, ...]:
    """The downbeats to snap to, from the beats half — computed here only if nobody has yet.

    Deliberately the *same* cache entry ``analyze_music`` writes, not a private copy: the
    grid for a piece of audio is the grid, and a second detection over an hour of concert
    to learn what is already on disk is the cost that keying halves separately exists to
    avoid.
    """
    half = halves.cached(
        f"{music.KIND}:{music.BEATS}",
        music.beats_key(dict(identity)),
        lambda path: music.beats_half(source, path, dict(described), detector),
        source,
        refresh,
        config,
    )
    rows: Sequence[Mapping[str, Any]] = records.read(Path(half["path"]))[music.BEATS]
    return tuple(float(row["t"]) for row in rows if row["downbeat"])
