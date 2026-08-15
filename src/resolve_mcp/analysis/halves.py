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


def collected(directory: Path, fix: str | None = None) -> dict[str, Path]:
    """The melodic stems under a separation's directory — first pass, third pass, or both.

    A separation writes a directory per pass — ``<directory>/mix``, ``<directory>/drums``, and
    ``<directory>/other`` when the wind split was asked for — and the job reports the parent of
    all of them. The melodic stems are in the first pass, so that is looked in first; the
    parent itself is checked too, because a director who copied the stems into a folder of
    their own should not have to name a subdirectory that is not there. The opt-in third pass
    sits beside the first and comes along when it is there — see ``_third_pass``.

    Here beside ``readable`` because more than one detector reads a melodic stem — phrases off
    the line, bars off the pulse (#180), solo changes off all of them — and two answers to
    "where are the stems" would be two conventions. That is not a hypothetical: ``structure``
    carried its own copy until #220, and the wind and comp stems it alone knew how to find
    reached the solo detector and nothing else. The drum pass is read by ``fills`` through a
    lookup of its own, because it wants a different pass narrowed to a different set of names;
    that one is still a second convention, and #220 did not close it.

    What a caller says when the stems are *missing* stays with the caller, because the advice
    differs — ``fix``, as in ``readable``, shapes the refusal for a directory that is not
    there, and an empty result is left for the caller to refuse in its own words.

    The imports are function-local: ``audio.stems`` reaches the Resolve seam, and every
    analysis half imports this module.
    """
    from ..audio import separator
    from ..audio.stems import MIX_PASS

    if not directory.is_dir():
        raise InvalidRequestError(
            cause=f"There is no directory at {directory}.",
            fix=fix
            or "Pass the directory a separate_stems job reported, or the mix pass inside it.",
            detail={"requested": str(directory)},
        )
    for candidate in (directory / MIX_PASS, directory):
        found = separator.collect(candidate)
        if found:
            found.update(_third_pass(directory))
            return found
    return {}


def _third_pass(directory: Path) -> dict[str, Path]:
    """The third pass's two halves under their envelope names — nothing, if it never ran.

    That pass writes a sibling of the mix pass (#153), so it is reached from whichever of
    the two accepted directories was given: the job's own directory holds it directly, and
    the mix pass directory holds it one level up. Anywhere else there is no pass layout to
    read, and a directory of loose stems gets nothing.

    Both halves or neither, and only the two names the envelope knows. One half alone is a
    partial pass, and it would join a voice set that still holds ``other`` — the residual
    measured twice over, which is the one way reaching for this pass reads worse than not
    reaching for it at all.
    """
    from ..audio import separator
    from ..audio.stems import MIX_PASS, OTHER_PASS, WIND_KEYS

    if (directory / MIX_PASS).is_dir():
        outer = directory / OTHER_PASS
    elif directory.name == MIX_PASS:
        outer = directory.parent / OTHER_PASS
    else:
        return {}
    if not outer.is_dir():
        return {}
    halved = {
        WIND_KEYS[name]: path
        for name, path in separator.collect(outer).items()
        if name in WIND_KEYS
    }
    return halved if len(halved) == len(WIND_KEYS) else {}


def stem_named(
    stems: Mapping[str, str | Path] | str | Path,
    wanted: str,
    purpose: str,
    absent: str,
) -> Path:
    """One stem out of a separation's directory or a mapping of named paths, or the refusal.

    Both detectors that read a stem ask this the same way — a directory the job reported, or
    the paths already lifted out of it — and both refuse the same two ways: the stem is not
    among them, or it is named and gone. Those refusals were duplicated word for word before
    #180 added the second caller, which is how a fix line gets corrected in one of them.

    ``purpose`` and ``absent`` are what genuinely differ, and they differ because the advice
    does: a phrase job cannot run without its stem and a bar job falls back to the master mix,
    so a reader who got this error should not have to translate. The missing-file fix is the
    same for both — the cache drops an entry whose files went missing, whoever asked.
    """
    if isinstance(stems, str | Path):
        found = collected(Path(stems))
    else:
        found = {str(label): Path(path) for label, path in stems.items()}

    chosen = found.get(wanted)
    if chosen is None:
        raise InvalidRequestError(
            cause=f"There is no {wanted} stem {purpose}.",
            fix=absent,
            detail={"wanted": wanted, "found": sorted(found)},
        )
    if not chosen.is_file():
        raise InvalidRequestError(
            cause=f"The {wanted} stem is not on disk: {chosen}.",
            fix=(
                "Run separate_stems again — the cache drops an entry whose files went missing, "
                "so asking for them redoes the separation."
            ),
            detail={"stem": str(chosen)},
        )
    return chosen


def identity(source: Path, config: Config) -> dict[str, Any]:
    """What this audio is: its bytes, whoever wrote the file and wherever it sits.

    Content rather than path, because the same concert arrives under several names — the
    director's master, the copy an acquisition staged into the cache directory, an excerpt
    rendered for one song — and a half keyed on the name is a beat model run again over
    identical audio (#193). The hash is read once per file state and remembered against a
    stat, so the starter this runs in still returns a job id at once. The rule itself lives
    in ``jobs.cache``; this is the door every analysis module goes through to reach it, so a
    job that keys off an audio path without writing a half of its own still agrees with the
    ones that do.
    """
    return cache.audio_identity(source, config)


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
