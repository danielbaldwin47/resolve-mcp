"""The structure analysis job: where the tunes are, and where the front changes hands.

Two halves, because they answer the two questions a concert's shape is made of (#22, story
29) and they need different substrate:

* **Tune boundaries** come off the master mix. Applause is the only reliable segmentation a
  jazz set offers — there is no verse and no chorus to find — so the mix is tagged for it
  and the music between the bursts is the tune. This is what a ``songs.json`` author reads
  before placing a single marker (#22, story 33). Applause alone over-calls, though: on the
  first live concert three of thirteen calls were talking bounded by clapping, so a call
  also has to have a pulse under it, and this half reads the beat grid too (#133). Set
  ``density_per_second`` to zero and it does not. It reads the loudness curve for the same
  kind of reason (#179): on a board mix the clapping barely registers and the announcement
  after it is a minute long, so the threshold scales to the file's own peak and each
  boundary walks forward to where the band actually comes in. Set ``settle_seconds`` to
  zero and it does not.

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
from ..errors import AnalysisDependencyError, InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from . import applause as applause_module
from . import beats as beats_module
from . import halves, music
from . import solos as solos_module

log = get_logger("analysis")

KIND = "analyze_structure"
TUNES = "tunes"
SOLOS = "solos"

APPLAUSE_SHAPE = (
    "threshold",
    "scale",
    "burst_seconds",
    "gap_seconds",
    "tune_seconds",
    "settle_db",
    "settle_seconds",
    "density_per_second",
)
"""Every setting that changes what the tune half says — and so what it is keyed on.

``density_per_second`` is in here even though it only filters what the tagger already
produced, which means moving it re-tags the room. That is the same bargain
``tune_seconds`` has always made, and the alternative is worse: two floors sharing one
cache entry would serve whichever set happened to be computed first."""
SOLO_SHAPE = (
    "window_seconds",
    "hop_seconds",
    "solo_seconds",
    "margin_db",
    "semitones",
    "snap_seconds",
)
"""Every setting that changes what the solo half says — and so what it is keyed on."""


def analyze_structure(
    audio: str | Path,
    tunes: bool = True,
    solos: bool = False,
    stems: str | Path | None = None,
    threshold: float = applause_module.DEFAULT_THRESHOLD,
    scale: float = applause_module.DEFAULT_SCALE,
    burst_seconds: float = applause_module.DEFAULT_MINIMUM_SECONDS,
    gap_seconds: float = applause_module.DEFAULT_GAP_SECONDS,
    tune_seconds: float = applause_module.DEFAULT_TUNE_SECONDS,
    settle_db: float = applause_module.DEFAULT_SETTLE_DB,
    settle_seconds: float = applause_module.DEFAULT_SETTLE_SECONDS,
    density_per_second: float = applause_module.DEFAULT_DENSITY_PER_SECOND,
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
    _sane_numbers(
        threshold,
        scale,
        settle_db,
        settle_seconds,
        window_seconds,
        hop_seconds,
        density_per_second,
    )
    found = _stems(stems, solos)

    settings: dict[str, Any] = {
        TUNES: tunes,
        SOLOS: solos,
        "threshold": float(threshold),
        "scale": float(scale),
        "burst_seconds": float(burst_seconds),
        "gap_seconds": float(gap_seconds),
        "tune_seconds": float(tune_seconds),
        "settle_db": float(settle_db),
        "settle_seconds": float(settle_seconds),
        "density_per_second": float(density_per_second),
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
        "solo_seconds": float(solo_seconds),
        "margin_db": float(margin_db),
        "semitones": float(semitones),
        "snap_seconds": float(snap_seconds),
    }
    identity = halves.identity(source, config)
    stem_identity = _stem_identity(found, config)
    key = cache.cache_key(KIND, [identity, stem_identity], settings)

    def work(progress: Progress) -> JobOutput:
        return analyze(
            source,
            settings,
            progress,
            identity=identity,
            stems=found,
            stem_identity=stem_identity,
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


def _sane_numbers(
    threshold: float,
    scale: float,
    settle_db: float,
    settle_seconds: float,
    window_seconds: float,
    hop_seconds: float,
    density_per_second: float,
) -> None:
    """Refuse the numbers that would quietly mean something other than what was asked.

    ``settle_db`` is in here because a negative margin puts playing level *above* the file's
    own median, which no set has half of itself over — every call would come back as one
    the band never came in on, and the job would succeed while reporting no tunes at all.
    """
    if (
        not 0.0 < threshold <= 1.0
        or not 0.0 <= scale <= 1.0
        or settle_db < 0
        or settle_seconds < 0
        or window_seconds <= 0
        or hop_seconds <= 0
        or density_per_second < 0
    ):
        raise InvalidRequestError(
            cause=(
                "The applause threshold must be a probability, the scale a fraction of one, "
                "the windows positive, and the settle margin, settle hold and beat-density "
                "floor zero or more."
            ),
            fix=(
                f"Defaults are threshold={applause_module.DEFAULT_THRESHOLD}, "
                f"scale={applause_module.DEFAULT_SCALE} (0 uses the threshold as it stands), "
                f"settle_db={applause_module.DEFAULT_SETTLE_DB}, "
                f"settle_seconds={applause_module.DEFAULT_SETTLE_SECONDS} "
                "(0 turns the settle step off), "
                f"window_seconds={solos_module.DEFAULT_WINDOW_SECONDS}, "
                f"hop_seconds={solos_module.DEFAULT_HOP_SECONDS}, "
                f"density_per_second={applause_module.DEFAULT_DENSITY_PER_SECOND} "
                "(0 turns the density check off)."
            ),
            detail={
                "threshold": threshold,
                "scale": scale,
                "settle_db": settle_db,
                "settle_seconds": settle_seconds,
                "window_seconds": window_seconds,
                "hop_seconds": hop_seconds,
                "density_per_second": density_per_second,
            },
        )


def _stems(stems: str | Path | None, solos: bool) -> dict[str, Path]:
    """The separated stems to read, from the directory a ``separate_stems`` job returned.

    That job writes each pass into its own directory, so the path it hands back holds the
    four stems one level down; both that path and the pass directory itself are accepted,
    because an agent reading the job record has one and an agent looking at the disk has
    the other. The opt-in third pass sits beside the first, and comes along when it is
    there — see ``_wind``.
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
            detail={SOLOS: solos, "stems": None},
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
    found.update(_wind(directory))
    return found


def _wind(directory: Path) -> dict[str, Path]:
    """The third pass's two halves under their envelope names — nothing, if it never ran.

    That pass writes a sibling of the mix pass (#153), so it is reached from whichever of
    the two accepted directories was given: the job's own directory holds it directly, and
    the mix pass directory holds it one level up. Anywhere else there is no pass layout to
    read, and a directory of loose stems gets nothing.

    Both halves or neither, and only the two names the envelope knows. One half alone is a
    partial pass, and it would join a voice set that still holds ``other`` — the residual
    measured twice over, which is the one way this change reads worse than no change.
    """
    if (directory / stems_module.MIX_PASS).is_dir():
        outer = directory / stems_module.OTHER_PASS
    elif directory.name == stems_module.MIX_PASS:
        outer = directory.parent / stems_module.OTHER_PASS
    else:
        return {}
    if not outer.is_dir():
        return {}
    halved = {
        stems_module.WIND_KEYS[name]: path
        for name, path in separator.collect(outer).items()
        if name in stems_module.WIND_KEYS
    }
    return halved if len(halved) == len(stems_module.WIND_KEYS) else {}


def _stem_identity(found: Mapping[str, Path], config: Config) -> dict[str, Any]:
    """What the stems are, for keying: their own directory name, or their bytes.

    Stems this server separated live in a directory named after the content hash of the
    audio they came from and the models that made them, so the name *is* the identity and
    reading a gigabyte of WAV back to learn what it already says would be waste. Stems from
    anywhere else are fingerprinted one by one, because nothing about the name is known.

    The third pass does not change that directory name — the flag keys the stems job, not
    the stems (#153) — so on the named branch it is ``names`` that separates a wind run
    from a residual run, and on the other branch the two extra fingerprints. Both branches
    key the split apart from the unsplit; neither is served the other's cached record.
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
    stem_identity: Mapping[str, Any] | None = None,
    tagger: applause_module.Tagger | None = None,
    detector: beats_module.Detector | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: find the boundaries that were asked for, write them out, return the gist.

    ``identity`` and ``stem_identity`` come from the starter, which already worked them out
    to key the job — fingerprinting the same stems a second time here would read a gigabyte
    of WAV to learn what the caller is holding.
    """
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
            lambda path: _tunes(
                source,
                path,
                described,
                shape,
                tagger,
                identity,
                detector,
                refresh,
                config,
            ),
            source,
            refresh,
            config,
        )
        artifacts.append(Path(result[TUNES]["path"]))

    if settings[SOLOS]:
        progress(0.6, "measuring the stems")
        shape = {name: settings[name] for name in SOLO_SHAPE}
        result[SOLOS] = halves.cached(
            f"{KIND}:{SOLOS}",
            cache.cache_key(
                f"{KIND}:{SOLOS}",
                [
                    identity,
                    stem_identity if stem_identity is not None else _stem_identity(stems, config),
                ],
                shape,
            ),
            lambda path: _solos(
                source,
                path,
                described,
                shape,
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
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> dict[str, Any]:
    curve = applause_module.tag(source, tagger)
    read = applause_module.reading(
        curve,
        float(shape["threshold"]),
        float(shape["scale"]),
        float(shape["burst_seconds"]),
    )
    spans = applause_module.spans(
        curve,
        read.threshold,
        read.burst_seconds,
        float(shape["gap_seconds"]),
    )
    tune_seconds = float(shape["tune_seconds"])
    found = applause_module.tunes(spans, float(described["duration_seconds"]), tune_seconds)
    margin_db = float(shape["settle_db"])
    hold = float(shape["settle_seconds"])
    played = _where_the_band_comes_in(
        found, margin_db, hold, tune_seconds, source, described, identity, refresh, config
    )
    floor = float(shape["density_per_second"])
    calls = _with_a_pulse(
        played.kept, floor, source, described, identity, detector, refresh, config
    )
    # The shape goes in first and the reading over it: ``threshold`` and ``burst_seconds``
    # are what the caller asked for, and the ``_used`` pair what the file was actually read
    # at, which are the same numbers only when the fallback did not fire.
    gist = {
        **applause_module.gist(curve, spans, calls, played),
        **shape,
        "threshold_used": round(read.threshold, 4),
        "burst_seconds_used": read.burst_seconds,
        "read_at_own_scale": read.own_scale,
    }
    log.info(
        "Found %d tunes and %d bursts of applause in %s at a threshold of %.4f (%s)",
        len(calls.kept),
        len(spans),
        source.name,
        read.threshold,
        "the file's own scale" if read.own_scale else "as asked",
    )
    for one in calls.dropped:
        log.info(
            "Dropped the call at %.2fs (%.1fs, %.2f beats/s) from %s: no pulse under it",
            one.start,
            one.seconds,
            one.beats_per_second or 0.0,
            source.name,
        )
    for one in (*played.silent, *played.brief):
        log.info(
            "Dropped the call at %.2fs (%.1fs) from %s: the band never comes in",
            one.start,
            one.seconds,
            source.name,
        )
    return halves.written(
        target,
        TUNES,
        described,
        gist,
        applause_module.numbered(calls.kept),
        {
            "dropped_calls": list(applause_module.dropped_calls(calls.dropped, floor)),
            "quiet_calls": list(applause_module.quiet_calls(played, margin_db, tune_seconds)),
        },
    )


def _where_the_band_comes_in(
    found: Sequence[applause_module.Tune],
    margin_db: float,
    hold_seconds: float,
    tune_seconds: float,
    source: Path,
    described: Mapping[str, Any],
    identity: Mapping[str, Any],
    refresh: bool,
    config: Config,
) -> applause_module.Settled:
    """The calls with their starts moved off the applause and onto the downbeat (#179).

    The loudness curve is ``analyze_music``'s, read the way the beat grid is: one
    measurement per piece of audio, whichever job asks for it first, at that job's default
    window so a set already analysed does not get a second curve keyed one hop apart. A hold
    of zero is the way out — the step is off, no curve is read, and a boundary is the end of
    the applause again.
    """
    if hold_seconds <= 0:
        return applause_module.Settled(tuple(found), (), ())
    shape = {
        "window_seconds": music.DEFAULT_WINDOW_SECONDS,
        "hop_seconds": music.DEFAULT_HOP_SECONDS,
    }
    rows = music.numbered_energy(source, dict(described), dict(identity), shape, refresh, config)
    loudness = applause_module.Loudness(
        seconds=tuple(float(row["t"]) for row in rows),
        lufs=tuple(float(row["lufs"]) for row in rows),
    )
    return applause_module.settled(found, loudness, margin_db, hold_seconds, tune_seconds)


def _with_a_pulse(
    found: Sequence[applause_module.Tune],
    minimum_density: float,
    source: Path,
    described: Mapping[str, Any],
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> applause_module.Calls:
    """The calls the beat grid says are music, and the ones it says are talking (#133).

    The grid is the one ``analyze_music`` writes, for the same reason ``_downbeats`` reads
    it: one detection per piece of audio, whichever job asks first. A floor of zero is the
    way out — the check is off, no grid is read, and this half needs nothing but the tagger.

    That way out is why this half asks ``_grid`` for the escape hatch to name.
    """
    if minimum_density <= 0:
        return applause_module.Calls(tuple(found), ())
    rows = _grid(
        source,
        described,
        identity,
        detector,
        refresh,
        config,
        "run the job with density_per_second=0 to keep every call the applause tagger makes",
    )
    grid = tuple(float(row["t"]) for row in rows)
    return applause_module.sifted(applause_module.counted(found, grid), minimum_density)


def _grid(
    source: Path,
    described: Mapping[str, Any],
    identity: Mapping[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
    without: str,
) -> list[dict[str, Any]]:
    """The beat grid both halves read, with a missing beat model shaped for the half asking.

    ``beats`` tells a caller who has no model to pass ``beats=false``, which is advice for
    ``analyze_music`` and no help in this job at all. Each half here has its own way to run
    without a grid — a density floor of zero, or solos off — and ``without`` is the half
    naming its own. The relabelling is gated on the model actually being the thing that is
    missing, so some other dependency failing inside the beats half still says what it is.
    """
    try:
        return music.numbered_beats(
            source, dict(described), dict(identity), detector, refresh, config
        )
    except AnalysisDependencyError as exc:
        if exc.detail.get("module") != beats_module.MODULE:
            raise
        raise AnalysisDependencyError(
            cause=(
                "This job reads the beat grid, and beat_this is not installed, so there is "
                "no grid to read."
            ),
            fix=(
                f"Install it on the machine running the server ({beats_module.INSTALL}), or "
                f"{without}."
            ),
            detail={"module": beats_module.MODULE},
        ) from exc


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

    brightness_stem = solos_module.timbre_stem(stems)
    voices = solos_module.voices(stems, window, hop)
    runs = solos_module.runs(voices, float(shape["margin_db"]), minimum)
    stepped = _timbre(stems, brightness_stem, window, hop, minimum, float(shape["semitones"]))
    opened = voices[0].seconds[0] if voices and voices[0].seconds else 0.0
    changes = solos_module.snapped(
        # The stem a timbre change happened inside. It is only ever ``None`` when there was
        # nothing to read the brightness off, and then there are no steps to name, so the
        # fallback is a label nothing wears rather than a wrong one.
        solos_module.changes(
            runs, stepped, window, opened, brightness_stem or solos_module.RESIDUAL
        ),
        _downbeats(source, described, identity, detector, refresh, config),
        float(shape["snap_seconds"]),
    )
    # Off the voices that came back rather than the stems handed in: ``voices`` decides
    # which stems are read (a residual whose two halves are on disk is not), and a gist
    # that counted the directory instead would name one set and count another.
    measured = tuple(one.name for one in voices)
    gist = {
        **solos_module.gist(runs, changes),
        **shape,
        "stem_count": len(measured),
        # What was measured, and which stem the timbre came off, because neither is
        # inferable from the rest: the same stems directory reads differently depending on
        # whether the third pass ran, and a record that cannot say which one ran cannot be
        # reviewed. A joined string, because a gist holds no lists.
        "voices": ", ".join(measured),
        "timbre_stem": brightness_stem,
    }
    log.info(
        "Found %d solo changes across %d stems (timbre off %s) in %s",
        len(changes),
        len(measured),
        brightness_stem or "nothing",
        source.name,
    )
    return halves.written(target, SOLOS, described, gist, solos_module.numbered(changes))


def _timbre(
    stems: Mapping[str, Path],
    stem: str | None,
    window: float,
    hop: float,
    minimum: float,
    semitones: float,
) -> tuple[solos_module.Step, ...]:
    """Brightness steps inside the stem ``timbre_stem`` picked — nothing, if it picked none."""
    from . import decode

    if stem is None:
        return ()
    seconds, hertz = solos_module.brightness(decode.read(stems[stem]), window, hop)
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
    avoid. Both halves of this job reach it through the one accessor, so neither can drift
    into keying its own copy.
    """
    rows = _grid(
        source,
        described,
        identity,
        detector,
        refresh,
        config,
        "run the job with solos=false for tune boundaries only",
    )
    return tuple(float(row["t"]) for row in rows if row["downbeat"])
