"""The music analysis job: beats and downbeats, energy curves, gist stats inline.

This is the substrate every cutting decision reads (#22, story 29), and it runs on the
master mix — no stems, no Resolve. Three consequences worth stating:

* **It never touches Resolve**, so it does not queue behind a render. The audio is a file
  the director handed over or one ``acquire_timeline_audio`` already wrote.

* **The curves go to disk and the gist comes back inline.** A concert is ten thousand beats
  and seven thousand energy windows; what a tool result can carry is "120 bpm, in four,
  loudest at 41:12, here are the two paths".

* **Each half is cached on its own terms.** The job is keyed on everything it was asked
  for, which is right for the job and wrong for its halves: asking again with a finer
  energy hop must not re-run a beat model over an hour of concert (#22, story 26 — analysis
  is paid for once per media state). So beats and energy are each their own cache entry,
  keyed on the audio and only the settings that shape them.

How the audio is identified follows ``jobs.cache``'s rule rather than inventing one: audio
this server wrote is hashed, because it is the substrate later analysis keys off and a
false hit there would attribute one concert's beats to another; a master the director
handed over is fingerprinted, because it is tens of gigabytes that sit unchanged for months
and reading all of it would stall the starter that is supposed to return a job id at once.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audio import wav
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..naming import slug
from . import beats as beats_module
from . import records

if TYPE_CHECKING:  # pragma: no cover - the worker imports these when it runs
    from .energy import Measurement

KIND = "analyze_music"
BEATS = "beats"
ENERGY = "energy"
DEFAULT_WINDOW_SECONDS = 3.0
DEFAULT_HOP_SECONDS = 0.5
MAXIMUM_WINDOW_SECONDS = 600.0


def analyze_music(
    audio: str | Path,
    beats: bool = True,
    energy: bool = True,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
    refresh: bool = False,
    detector: beats_module.Detector | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start the analysis job. Returns the job record, not the analysis."""
    config = config or get_config()
    source = _readable(audio)
    _asked_for_something(beats, energy)
    _sane_windows(window_seconds, hop_seconds)

    settings = {
        BEATS: beats,
        ENERGY: energy,
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
    }
    identity = _identity(source, config)
    key = cache.cache_key(KIND, [identity], settings)

    def work(progress: Progress) -> JobOutput:
        return analyze(
            source,
            settings,
            progress,
            identity=identity,
            detector=detector,
            refresh=refresh,
            config=config,
        )

    # The record says which file was analysed; the key deliberately does not, so the same
    # audio under two names in the cache directory is not analysed twice.
    return start_job(
        KIND,
        {"audio": source.name, **settings},
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
                "Pass the path to the master mix, or the path an acquire_timeline_audio job "
                "returned. Analysis reads WAV."
            ),
            detail={"requested": str(source)},
        )
    return source


def _asked_for_something(beats: bool, energy: bool) -> None:
    if not beats and not energy:
        raise InvalidRequestError(
            cause="Neither beats nor energy was asked for, so there is nothing to analyse.",
            fix="Leave both on, or turn exactly one off.",
            detail={BEATS: beats, ENERGY: energy},
        )


def _sane_windows(window_seconds: float, hop_seconds: float) -> None:
    if not 0 < window_seconds <= MAXIMUM_WINDOW_SECONDS or hop_seconds <= 0:
        raise InvalidRequestError(
            cause="The energy window and hop must be positive, and the window under 10 minutes.",
            fix=f"Defaults are window_seconds={DEFAULT_WINDOW_SECONDS}, "
            f"hop_seconds={DEFAULT_HOP_SECONDS}.",
            detail={"window_seconds": window_seconds, "hop_seconds": hop_seconds},
        )


def _identity(source: Path, config: Config) -> dict[str, Any]:
    """Hash what this server wrote; fingerprint what the director handed over."""
    if _inside(source, config.audio_dir):
        return {"sha256": cache.content_hash(source)}
    return cache.fingerprint(source)


def _inside(source: Path, directory: Path) -> bool:
    return source.resolve().is_relative_to(directory.resolve())


def analyze(
    source: Path,
    settings: dict[str, Any],
    progress: Progress,
    identity: dict[str, Any] | None = None,
    detector: beats_module.Detector | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> JobOutput:
    """The worker: measure what was asked for, write it out, return the gist."""
    config = config or get_config()
    target = config.analysis_dir
    target.mkdir(parents=True, exist_ok=True)
    identity = identity or _identity(source, config)

    progress(0.05, "reading the audio")
    described = wav.describe(source)
    result: dict[str, Any] = {"audio": _audio_gist(described)}
    artifacts: list[Path] = []

    if settings[BEATS]:
        progress(0.1, "finding beats and downbeats")
        result[BEATS] = _half(
            BEATS,
            cache.cache_key(f"{KIND}:{BEATS}", [identity], {}),
            lambda path: _beats(source, path, described, detector),
            source,
            refresh,
            config,
        )
        artifacts.append(Path(result[BEATS]["path"]))

    if settings[ENERGY]:
        progress(0.6, "measuring loudness and onset density")
        shape = {name: settings[name] for name in ("window_seconds", "hop_seconds")}
        result[ENERGY] = _half(
            ENERGY,
            cache.cache_key(f"{KIND}:{ENERGY}", [identity], shape),
            lambda path: _energy(source, path, described, shape),
            source,
            refresh,
            config,
        )
        artifacts.append(Path(result[ENERGY]["path"]))

    progress(0.95, "written")
    return JobOutput(result, tuple(artifacts))


def _half(
    kind: str,
    key: str,
    build: Callable[[Path], dict[str, Any]],
    source: Path,
    refresh: bool,
    config: Config,
) -> dict[str, Any]:
    """This half of the analysis, computed or reused, and its file named after its own key.

    Named after the key rather than the job's, so the same half asked for twice under
    different job settings is one file rather than two identical ones.
    """
    if not refresh:
        hit = cache.lookup(key, config)
        if hit is not None:
            return hit
    target = config.analysis_dir / f"{slug(source.stem, 'analysis')}-{key[:12]}-{kind}.json"
    result = build(target)
    cache.remember(key, f"{KIND}:{kind}", result, [Path(result["path"])], config)
    return result


def _audio_gist(described: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": described["path"],
        "duration_seconds": described["duration_seconds"],
        "sample_rate": described["sample_rate"],
        "channels": described["channels"],
    }


def _beats(
    source: Path,
    target: Path,
    described: dict[str, Any],
    detector: beats_module.Detector | None,
) -> dict[str, Any]:
    grid = beats_module.detect(source, detector)
    rows = beats_module.numbered(grid)
    return _written(target, BEATS, described, beats_module.gist(grid, rows), list(rows))


def _energy(
    source: Path,
    target: Path,
    described: dict[str, Any],
    shape: dict[str, Any],
) -> dict[str, Any]:
    from . import decode
    from . import energy as energy_module

    audio = decode.read(source)
    window = float(shape["window_seconds"])
    hop = float(shape["hop_seconds"])
    measured = energy_module.measure(audio, window, hop)
    rows = [
        {
            "t": point.seconds,
            "lufs": point.lufs,
            "rms_dbfs": point.rms_dbfs,
            "onsets_per_second": point.onsets_per_second,
        }
        for point in measured.points
    ]
    return _written(target, ENERGY, described, _energy_gist(measured, window, hop), rows)


def _energy_gist(measured: Measurement, window: float, hop: float) -> dict[str, Any]:
    """Where it lifts and where it drops — the two questions asked of a curve first."""
    loudest = max(measured.points, key=lambda point: point.lufs)
    quietest = min(measured.points, key=lambda point: point.lufs)
    return {
        "count": len(measured.points),
        "window_seconds": window,
        "hop_seconds": hop,
        "integrated_lufs": round(measured.integrated_lufs, 2),
        "loudest": {"t": loudest.seconds, "lufs": loudest.lufs},
        "quietest": {"t": quietest.seconds, "lufs": quietest.lufs},
        "onsets_per_second_mean": round(
            sum(point.onsets_per_second for point in measured.points) / len(measured.points), 3
        ),
    }


def _written(
    target: Path,
    kind: str,
    described: dict[str, Any],
    gist: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """One file per half: a header of gist stats, then one record per line."""
    header = {
        "kind": kind,
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **gist,
    }
    records.write(target, header, kind, rows)
    return {"path": str(target), **gist}
