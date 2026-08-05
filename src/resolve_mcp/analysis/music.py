"""The music analysis job: beats and downbeats, energy curves, gist stats inline.

This is the substrate every cutting decision reads (#22, story 29), and it runs on the
master mix — no stems, no Resolve. Two consequences worth stating:

* **It never touches Resolve**, so it does not queue behind a render. The audio is a file
  the director handed over or one ``acquire_timeline_audio`` already wrote.

* **The curves go to disk and the gist comes back inline.** A concert is ten thousand beats
  and seven thousand energy windows; what a tool result can carry is "120 bpm, in four,
  loudest at 41:12, here are the two paths".

The cache is keyed on the audio's content hash, not its path and mtime: the acquired WAV is
the substrate every later analysis keys off, and a false hit there would quietly attribute
one concert's beats to another (see ``jobs.cache``). The cost is one pass over the file
inside the starter, which is seconds even for a concert master.
"""

from __future__ import annotations

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
    from .decode import Audio

KIND = "analyze_music"
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

    params = {
        "audio": source.name,
        "beats": beats,
        "energy": energy,
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
    }
    key = cache.cache_key(KIND, [{"sha256": cache.content_hash(source)}], params)

    def work(progress: Progress) -> JobOutput:
        return analyze(source, key, params, progress, detector=detector, config=config)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


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
            detail={"beats": beats, "energy": energy},
        )


def _sane_windows(window_seconds: float, hop_seconds: float) -> None:
    if not 0 < window_seconds <= MAXIMUM_WINDOW_SECONDS or hop_seconds <= 0:
        raise InvalidRequestError(
            cause="The energy window and hop must be positive, and the window under 10 minutes.",
            fix=f"Defaults are window_seconds={DEFAULT_WINDOW_SECONDS}, "
            f"hop_seconds={DEFAULT_HOP_SECONDS}.",
            detail={"window_seconds": window_seconds, "hop_seconds": hop_seconds},
        )


def analyze(
    source: Path,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    detector: beats_module.Detector | None = None,
    config: Config | None = None,
) -> JobOutput:
    """The worker: read the audio once, write what was asked for, return the gist."""
    config = config or get_config()
    target = config.analysis_dir
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{slug(source.stem, 'analysis')}-{key[:12]}"

    progress(0.05, "reading the audio")
    described = wav.describe(source)
    result: dict[str, Any] = {"audio": _audio_gist(described)}
    artifacts: list[Path] = []

    if params.get("beats", True):
        progress(0.1, "finding beats and downbeats")
        result["beats"] = _beats(source, target / f"{stem}-beats.json", described, detector)
        artifacts.append(Path(result["beats"]["path"]))

    if params.get("energy", True):
        progress(0.6, "measuring loudness and onset density")
        result["energy"] = _energy(source, target / f"{stem}-energy.json", described, params)
        artifacts.append(Path(result["energy"]["path"]))

    progress(0.95, "written")
    return JobOutput(result, tuple(artifacts))


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
    gist = beats_module.gist(grid, rows)
    header = {
        "kind": "beats",
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **gist,
    }
    records.write(target, header, "beats", rows)
    return {"path": str(target), **gist}


def _energy(
    source: Path,
    target: Path,
    described: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    from . import decode
    from . import energy as energy_module

    audio = decode.read(source)
    window = float(params.get("window_seconds", DEFAULT_WINDOW_SECONDS))
    hop = float(params.get("hop_seconds", DEFAULT_HOP_SECONDS))
    points = energy_module.curve(audio, window_seconds=window, hop_seconds=hop)
    gist = _energy_gist(audio, points, window, hop, energy_module)

    header = {
        "kind": "energy",
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **gist,
    }
    rows = [
        {
            "t": point.seconds,
            "lufs": point.lufs,
            "rms_dbfs": point.rms_dbfs,
            "onsets_per_second": point.onsets_per_second,
        }
        for point in points
    ]
    records.write(target, header, "energy", rows)
    return {"path": str(target), **gist}


def _energy_gist(
    audio: Audio,
    points: tuple[Any, ...],
    window: float,
    hop: float,
    energy_module: Any,
) -> dict[str, Any]:
    """Where it lifts and where it drops — the two questions asked of a curve first."""
    loudest = max(points, key=lambda point: point.lufs)
    quietest = min(points, key=lambda point: point.lufs)
    return {
        "count": len(points),
        "window_seconds": window,
        "hop_seconds": hop,
        "integrated_lufs": round(energy_module.integrated_lufs(audio), 2),
        "loudest": {"t": loudest.seconds, "lufs": loudest.lufs},
        "quietest": {"t": quietest.seconds, "lufs": quietest.lufs},
        "onsets_per_second_mean": round(
            sum(point.onsets_per_second for point in points) / len(points), 3
        ),
    }
