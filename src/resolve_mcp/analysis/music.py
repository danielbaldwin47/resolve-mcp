"""The music analysis job: beats and downbeats, energy curves, gist stats inline.

This is the substrate every cutting decision reads (#22, story 29), and it runs on the
master mix — no stems, no Resolve. Three consequences worth stating:

* **It never touches Resolve**, so it does not queue behind a render. The audio is a file
  the director handed over or one ``acquire_timeline_audio`` already wrote.

* **The curves go to disk and the gist comes back inline.** A concert is ten thousand beats
  and seven thousand energy windows; what a tool result can carry is "120 bpm, in four,
  loudest at 41:12, here are the two paths".

* **Each half is cached on its own terms** — see ``halves``, which also owns how the audio
  is identified and what a half's file looks like. Beats and energy are each their own
  cache entry, keyed on the audio and only the settings that shape them, so asking again
  with a finer energy hop does not re-run a beat model over an hour of concert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..audio import wav
from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from . import beats as beats_module
from . import device, halves

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
    source = halves.readable(audio)
    _asked_for_something(beats, energy)
    _sane_windows(window_seconds, hop_seconds)

    settings = {
        BEATS: beats,
        ENERGY: energy,
        "window_seconds": float(window_seconds),
        "hop_seconds": float(hop_seconds),
    }
    identity = halves.identity(source, config)
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


def beats_of(
    source: Path,
    described: dict[str, Any],
    identity: dict[str, Any],
    detector: beats_module.Detector | None = None,
    refresh: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """The beat grid for this audio, computed or reused — the entry every job shares.

    Public because it is shared: structure analysis snaps solo changes to downbeats, and
    calling this is how it gets them without running the beat model a second time. The key
    and the kind stay here together rather than being rebuilt by each caller, because a
    caller that assembled one of them differently would quietly get its own cache entry.
    """
    return halves.cached(
        f"{KIND}:{BEATS}",
        cache.cache_key(f"{KIND}:{BEATS}", [identity], {}),
        lambda path: beats_half(source, path, described, detector),
        source,
        refresh,
        config or get_config(),
    )


def numbered_beats(
    source: Path,
    described: dict[str, Any],
    identity: dict[str, Any],
    detector: beats_module.Detector | None,
    refresh: bool,
    config: Config,
) -> list[dict[str, Any]]:
    """The beat records themselves, from ``beats_of`` — computed or read back from disk.

    The document is the interchange format, so a caller that wants the grid rather than a
    path reads it the way an agent would. Reading it here rather than at the caller keeps
    the file's layout the business of the module that writes it. Drum-fill detection (#39)
    reports against this grid and must not pay for the beat model a second time on audio
    music analysis already ran over.
    """
    document = beats_of(source, described, identity, detector, refresh, config)
    written = json.loads(Path(document["path"]).read_text(encoding="utf-8"))
    return list(written[BEATS])


def energy_of(
    source: Path,
    described: dict[str, Any],
    identity: dict[str, Any],
    shape: dict[str, Any],
    refresh: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """The loudness curve for this audio, computed or reused — the other shared entry.

    Public for the reason ``beats_of`` is: structure analysis reads this curve to find where
    a tune starts after the applause (#179), and measuring it a second time would decode the
    whole set again. ``shape`` is the window and hop, which key the half — a caller asking
    for the defaults gets whatever ``analyze_music`` already wrote.
    """
    return halves.cached(
        f"{KIND}:{ENERGY}",
        cache.cache_key(f"{KIND}:{ENERGY}", [identity], shape),
        lambda path: _energy(source, path, described, shape),
        source,
        refresh,
        config or get_config(),
    )


def numbered_energy(
    source: Path,
    described: dict[str, Any],
    identity: dict[str, Any],
    shape: dict[str, Any],
    refresh: bool,
    config: Config,
) -> list[dict[str, Any]]:
    """The energy records themselves, from ``energy_of`` — computed or read back from disk.

    The document is the interchange format, the same bargain ``numbered_beats`` makes: a
    caller that wants the curve rather than a path reads it the way an agent would, and the
    file's layout stays the business of the module that writes it.
    """
    document = energy_of(source, described, identity, shape, refresh, config)
    written = json.loads(Path(document["path"]).read_text(encoding="utf-8"))
    return list(written[ENERGY])


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
    identity = identity or halves.identity(source, config)

    progress(0.05, "reading the audio")
    described = wav.describe(source)
    result: dict[str, Any] = {"audio": halves.audio_gist(described)}
    artifacts: list[Path] = []

    if settings[BEATS]:
        progress(0.1, "finding beats and downbeats")
        result[BEATS] = beats_of(source, described, identity, detector, refresh, config)
        artifacts.append(Path(result[BEATS]["path"]))
        # Which torch stack the beat model runs on (#202). Reported per job rather than
        # only at the inference site, because a cached grid ran nothing and the record
        # should still say what a fresh run here would use.
        note = device.torch_note()
        if note is not None:
            result["torch"] = note

    if settings[ENERGY]:
        progress(0.6, "measuring loudness and onset density")
        shape = {name: settings[name] for name in ("window_seconds", "hop_seconds")}
        result[ENERGY] = energy_of(source, described, identity, shape, refresh, config)
        artifacts.append(Path(result[ENERGY]["path"]))

    progress(0.95, "written")
    return JobOutput(result, tuple(artifacts))


def beats_half(
    source: Path,
    target: Path,
    described: dict[str, Any],
    detector: beats_module.Detector | None,
) -> dict[str, Any]:
    """The beat grid for this audio, written out. Shared with structure analysis."""
    grid = beats_module.detect(source, detector)
    rows = beats_module.rows(grid)
    return halves.written(target, BEATS, described, beats_module.gist(grid, rows), list(rows))


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
    return halves.written(target, ENERGY, described, _energy_gist(measured, window, hop), rows)


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


