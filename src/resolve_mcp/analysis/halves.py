"""What every analysis half does the same way: identify the audio, cache it, write it out.

A "half" is one measurement of one file — the beat grid, the energy curve, the tune
boundaries — cached on its own terms rather than on the job's. That distinction is the
reason this module exists: the job is keyed on everything it was asked for, which is right
for the job and wrong for its parts, because asking again with a finer energy hop must not
re-run a beat model over an hour of concert (#22, story 26 — analysis is paid for once per
media state). Two jobs that need the same measurement share the entry: structure analysis
needs downbeats to snap solo changes to, and it reads the same beats half ``analyze_music``
wrote rather than paying for a second one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..naming import slug
from . import records


def readable(audio: str | Path, fix: str | None = None) -> Path:
    """The audio as a path, or the error that says what to pass instead.

    The ``fix`` differs per job because the advice does: a fill job wants the master the
    stems came from, and music analysis wants any master at all.
    """
    source = Path(audio)
    if not source.is_file():
        raise InvalidRequestError(
            cause=f"There is no file at {source}.",
            fix=fix
            or (
                "Pass the path to the master mix, or the path an acquire_timeline_audio job "
                "returned. Analysis reads WAV."
            ),
            detail={"requested": str(source)},
        )
    return source


def sane_floor(minimum_confidence: float, default: float, writes: str = "candidate") -> None:
    """Refuse a confidence floor that is not a fraction, before a job exists to carry it.

    Here beside ``readable`` because it is the same kind of thing: the checks a detector runs
    on what it was *asked for*, rather than on what it read. Every detector that reports
    candidates with a confidence takes this argument and must refuse the same values.

    ``default`` and ``writes`` differ per detector because the advice does — a fill job says
    "0 writes every candidate" and a phrase job "0 writes every boundary", and a reader who
    got that error should not have to translate.
    """
    if not 0.0 <= minimum_confidence <= 1.0:
        raise InvalidRequestError(
            cause="The confidence floor is a fraction between 0 and 1.",
            fix=f"The default is {default}; 0 writes every {writes}.",
            detail={"minimum_confidence": minimum_confidence},
        )


def identity(source: Path, config: Config) -> dict[str, Any]:
    """Hash what this server wrote; fingerprint what the director handed over.

    Audio this server wrote is hashed, because it is the substrate later analysis keys off
    and a false hit there would attribute one concert's beats to another; a master the
    director handed over is fingerprinted, because it is tens of gigabytes that sit
    unchanged for months and reading all of it would stall the starter that is supposed to
    return a job id at once. The rule itself lives in ``jobs.cache``, so jobs that key off
    an audio path without going through a half agree with the ones that do.
    """
    return cache.identity(source, config.audio_dir)


def inside(source: Path, directory: Path) -> bool:
    """Whether this path is one of ours — under a directory this server writes into."""
    return source.resolve().is_relative_to(directory.resolve())


def cached(
    kind: str,
    key: str,
    build: Callable[[Path], dict[str, Any]],
    source: Path,
    refresh: bool,
    config: Config,
) -> dict[str, Any]:
    """This half, computed or reused, and its file named after its own key.

    Named after the key rather than the job's, so the same half asked for twice under
    different job settings is one file rather than two identical ones.
    """
    if not refresh:
        hit = cache.lookup(key, config)
        if hit is not None:
            return hit
    label = kind.rsplit(":", 1)[-1]
    target = config.analysis_dir / f"{slug(source.stem, 'analysis')}-{key[:12]}-{label}.json"
    result = build(target)
    cache.remember(key, kind, result, [Path(result["path"])], config)
    return result


def audio_gist(described: Mapping[str, Any]) -> dict[str, Any]:
    """What every analysis result says about the file it read, before any measurement."""
    return {
        "path": described["path"],
        "duration_seconds": described["duration_seconds"],
        "sample_rate": described["sample_rate"],
        "channels": described["channels"],
    }


def written(
    target: Path,
    kind: str,
    described: Mapping[str, Any],
    gist: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    aside: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One file per half: a header of gist stats, then one record per line.

    ``aside`` is written into the header but kept out of what comes back, which is the way
    a half records something too long for a tool result and too short for its own file —
    the calls the tune half rejected, say (#133). The gist rides home in the job record and
    stays stats; the aside stays on disk with the records it is about. It goes in under the
    gist rather than over it, so a name a half picks for an aside can never quietly replace
    a stat and leave the header saying something the returned dict does not.
    """
    header = {
        "kind": kind,
        "audio": described["path"],
        "duration_seconds": described["duration_seconds"],
        **(aside or {}),
        **gist,
    }
    records.write(target, header, kind, list(rows))
    return {"path": str(target), **gist}
