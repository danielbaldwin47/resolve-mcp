"""Two passes, because fill detection needs near-clean drums — and an opt-in third.

Pass one splits the mix into four stems; pass two takes the drum stem alone and decomposes
it into the pieces of the kit. Running the drum model on the full mix is what the second
pass exists to avoid — it has bass and vocals bleeding into every hit, and a fill detector
reading that is reading the band, not the drummer.

Pass three does the same to ``other``, the stem that holds everything no model has a stem
for, splitting the winds out of it so a solo detector reading brightness has one instrument
family to track rather than horns and piano mixed together. It is off unless the caller asks
for it (#126): on a band with no piano ``other`` is already the wind candidate, and the pass
recovers nothing for the compute it costs.

Four decisions worth knowing:

* **The job's key and the stems' key are not the same key, on purpose.** The job is keyed
  the way its acquisition is keyed — a timeline fingerprint, a clip's path/size/mtime —
  because that is all that can be known in the starter, before any audio exists. The stems
  on disk are keyed by the *content hash* of the audio that produced them plus the models
  run on it (#22: "workers key off content hash of cached audio + params hash"), and by
  nothing about where that audio came from — so a renamed timeline, or the same file
  reached as a clip rather than through the mix, finds its stems already there and skips
  both passes. ``refresh`` overrides both.

* **Acquisition runs inside this job.** #12 puts the export inside the starter rather than
  making the agent sequence one, so the stems job starts the acquisition job and follows
  it, mapping its progress into the first quarter of its own. Its failure arrives as this
  job's failure with the acquisition's own cause and fix (see ``ChainedJobError``) —
  a render queue problem reads as a render queue problem either way.

* **Each pass writes into its own directory.** Pass two reads the drum stem pass one
  wrote; sharing a directory would have the decomposition land beside — and, with a model
  that labels its output ``(Drums)``, on top of — the file it is reading.

* **The third pass is a flag on the job, not on the stems key.** Which models could run is
  part of what the stems on disk *are* and keys the directory; whether the optional one was
  asked for this time is not, so both shapes land in one directory rather than separating
  the same audio into two. That makes completeness depend on the flag — a two-pass directory
  is complete with the pass off and partial with it on — and a partial directory is redone
  whole rather than topped up, which is the existing rule and costs a re-run of passes one
  and two the first time the flag is turned on for audio already separated.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import InternalError, InvalidRequestError, SeparationInProgressError
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs import runner as job_runner
from ..jobs.runner import Detached, JobOutput, Progress, start_job
from ..jobs.store import SESSION, JobRecord, pid_alive
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from . import acquire, separator

log = get_logger("audio")

KIND = "separate_stems"

FOUR_STEMS = ("vocals", "drums", "bass", "other")
DRUM_STEMS = ("kick", "snare", "toms", "ride", "crash")
"""What the second pass is kept for. The drum model writes ``hh`` too and it is left behind.

Fills in the material this was built against are frequently cymbal-led (#125), and the model
already computes the cymbals — collecting three of its six outputs was throwing away the
evidence that tells a fill from ordinary comping. ``hh`` stays out for now: it is the stem most
likely to be pure timekeeping, and it is the strongest test of the detector's local baseline
rather than the first one to run.
"""
DRUM_SOURCE = "drums"
"""Which of the four stems the second pass decomposes."""

WIND_STEMS = ("woodwinds", "no woodwinds")
"""What the third pass asks its model for, in the labels that model writes them under."""

WIND = "wind"
"""Horns and reeds, once the third pass has taken them out of ``other``."""

COMP = "comp"
"""What is left of ``other`` after the winds go: accompaniment, and never a piano stem."""

WIND_KEYS = {"woodwinds": WIND, "no woodwinds": COMP}
"""The envelope's names for the two halves of the third pass.

``no woodwinds`` is **not** a piano stem and nothing here may call it one. It is piano plus
guitar, vibes, percussion and whatever bass leaked through, and on a bass-weak capture it is
mostly the bass line — #126 measured roughly 60% of its energy below 200 Hz on a source whose
``bass`` stem came back near-silent. ``comp`` says accompaniment, which is all that can be
promised about it.
"""

OTHER_SOURCE = "other"
"""Which of the four stems the third pass decomposes: everything no model has a stem for."""

SEPARATION = ("model", "drum_model", "wind_model", "stems", "drum_stems", "wind_stems")
"""The params that change what the stems *are*, as opposed to where the audio came from."""

MIX_PASS = "mix"
DRUM_PASS = "drums"
OTHER_PASS = "other"

ACQUIRE_FLOOR = 0.02
ACQUIRE_CEILING = 0.25
PASS_ONE_CEILING = 0.6
WIND_FLOOR = 0.8
"""Where the drum pass hands over to the wind pass — a boundary, not a ceiling.

The optional third pass splits the back half rather than extending it, so this is where pass
two stops only when pass three is coming. With the pass off, pass two runs the whole way to
``SEPARATED`` instead.
"""
SEPARATED = 0.9
"""Where the last pass ends, whichever pass that is.

A bar that finished somewhere different depending on a flag would have the cheaper two-pass
job look unfinished at exactly the point the expensive one reads as done.
"""
COLLECTING = 0.95

PERCENT = 100


def separate_stems(
    connection: ResolveConnection,
    scope: str = acquire.TIMELINE_SCOPE,
    timeline: str | None = None,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    refresh: bool = False,
    split_wind: bool = False,
    detach: bool = False,
    runner: separator.Runner | None = None,
    ffmpeg_runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a separation job. Returns the job record, not the stems.

    ``split_wind`` adds the third pass over ``other``. It is a job param but not a stems-key
    param, so it stays in one stems directory rather than separating the same audio twice —
    but a directory missing the pass this run wants is partial, and partial is redone whole,
    so turning it on for audio already separated re-runs the earlier passes too.

    ``detach`` moves the separation into a process of its own once the audio exists, so a
    half-hour pass survives this process exiting (G4). The acquisition stays here: it drives
    Resolve, and a Resolve handle cannot be handed to another process — so a detached job is
    only safe from the hand-off on, and a server that dies mid-export still loses the job.
    It is off by default here and on at the tool: a caller passing its own ``runner`` is
    testing the passes, and those belong in the process doing the asserting.

    ``runner`` and ``ffmpeg_runner`` are the two subprocess seams: the default shells out
    for real, and a caller can hand in its own to exercise the route without the models or
    ffmpeg installed. ``runner`` cannot cross the hand-off — a function is not something the
    record can carry — so ``detach`` with a substituted separator is refused rather than
    silently running the real one.
    """
    config = config or get_config()
    if detach and runner is not None:
        raise InvalidRequestError(
            cause="A detached separation runs in another process, which cannot be given a runner.",
            fix="Drop detach to exercise the substituted separator, or drop the runner.",
            detail={"kind": KIND},
        )
    source = acquire.audio_source(
        connection,
        scope,
        timeline=timeline,
        clip=clip,
        bin=bin,
        refresh=refresh,
        runner=ffmpeg_runner,
        config=config,
    )
    params = {**source.params, **separation_params(config), "split_wind": split_wind}
    key = cache.cache_key(KIND, [source.fingerprint], params)

    def work(progress: Progress) -> JobOutput | Detached:
        audio = acquired(source, progress, config)
        if detach:
            # Everything the other process cannot recompute: the audio this acquisition
            # produced, and whether it may trust what is already on disk. The rest — models,
            # the wind flag, the cache key — is on the record already.
            return Detached({"audio": audio, "reuse": not refresh})
        return multi_pass(
            audio,
            params,
            progress,
            split_wind=split_wind,
            runner=runner,
            reuse=not refresh,
            config=config,
        )

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def detached_pass(
    record: JobRecord,
    progress: Progress,
    config: Config | None = None,
    runner: separator.Runner | None = None,
) -> JobOutput:
    """The passes, run in a process of its own from the record alone.

    The counterpart of the ``Detached`` above: what the starter knew is on the record, so
    this reads the acquired audio off ``plan`` and the models off ``params`` and runs exactly
    the ``multi_pass`` the thread would have run. Nothing here touches Resolve, which is the
    property that makes detaching this job safe at all.
    """
    plan = record.plan or {}
    audio = plan.get("audio")
    if not isinstance(audio, dict):
        raise InternalError(
            cause=f"The separation job {record.job_id} carries no acquired audio to work on.",
            detail={"job_id": record.job_id, "plan": sorted(plan)},
        )
    return multi_pass(
        audio,
        record.params,
        progress,
        split_wind=bool(record.params.get("split_wind")),
        runner=runner,
        reuse=bool(plan.get("reuse", True)),
        config=config,
    )


def acquired(
    source: acquire.Source,
    progress: Progress,
    config: Config | None = None,
    poll: float = job_runner.FOLLOW_POLL,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the acquisition this separation needs, and report it as this job's first quarter.

    A cache hit comes back completed from the starter and is never waited on at all; a real
    export is followed record by record, because for a concert that render is minutes of
    the job and a poller that sees nothing move assumes a hang.
    """
    config = config or get_config()
    progress(ACQUIRE_FLOOR, "acquiring the audio")
    started = source.start()
    span = ACQUIRE_CEILING - ACQUIRE_FLOOR

    def watch(record: JobRecord) -> None:
        progress(ACQUIRE_FLOOR + span * record.progress, record.step or "acquiring the audio")

    finished = job_runner.follow(
        str(started["job_id"]),
        watch,
        poll=poll,
        sleep=sleep,
        config=config,
    )
    if finished.result is None:
        raise InternalError(cause=f"The acquisition {finished.job_id} finished with no audio.")
    return finished.result


def separation_params(config: Config | None = None) -> dict[str, Any]:
    """What is being run, as opposed to what it is being run on. Keys the stems on disk.

    The third pass's model is in here whether or not that pass is switched on: the key says
    which models these stems could have come from, and leaving the name out when the pass is
    off would let a changed ``wind_model`` be silently ignored by every directory separated
    before it was turned on.
    """
    config = config or get_config()
    return {
        "model": config.stem_model,
        "drum_model": config.drum_model,
        "wind_model": config.wind_model,
        "stems": list(FOUR_STEMS),
        "drum_stems": list(DRUM_STEMS),
        "wind_stems": list(WIND_STEMS),
    }


def stem_key(audio: dict[str, Any], params: dict[str, Any]) -> str:
    """What the stems on disk are keyed by: the audio's own bytes and the models run on it.

    Deliberately not the whole job params. Scope, timeline name and bin say where the audio
    came from, not what it is — a renamed timeline, or the same file reached as a clip
    rather than through the mix, is the same audio, and separating it twice would pay the
    GPU twice for a byte-identical answer (#22 story 26: analysis is paid for once per
    media state). Sample rate and bit depth are in the bytes already.
    """
    settings = {name: params.get(name) for name in SEPARATION}
    return cache.cache_key(KIND, [{"content_sha256": audio["content_sha256"]}], settings)


CLAIM = ".separating.json"
"""The file that says a process is writing this stems directory right now. See ``claimed``."""

CLAIM_CEILING = 6 * 60 * 60
"""How old a claim may get before it is read as abandoned, in seconds.

Deliberately far longer than any separation: the longest measured pass over a full set is
under an hour, so six of them can only be a claim whose process is gone in a way the pid
check could not see — the pid recycled onto a live, unrelated process. Without a ceiling
that directory is locked out for the life of the machine.
"""

_claims: dict[str, threading.Lock] = {}
_claims_lock = threading.Lock()
"""One lock per stems directory, for the threads of *this* process. See ``claimed``."""


def _local_lock(directory: Path) -> threading.Lock:
    """The lock this process uses for that directory, made once and kept."""
    key = os.path.normcase(str(directory.resolve()))
    with _claims_lock:
        return _claims.setdefault(key, threading.Lock())


@contextmanager
def claimed(directory: Path) -> Iterator[None]:
    """Hold this stems directory for one separation at a time.

    The directory is keyed by the audio's bytes and the models, so two runs over the same mix
    — a retry, a second agent, a job started twice — land in the same one, and two separators
    writing the same files interleave into stems that are neither run's: half the tracks from
    each, all of them looking complete to the reuse check that reads them next.

    Two locks, because there are two kinds of rival. A file for the other *processes*: since
    G4 a separation runs in a detached worker, and disk is the only thing those share — the
    same reason the job record itself is a file. It is created with an exclusive create, so
    the claim is won by the one whose create the filesystem accepted rather than by whoever
    read an empty directory last. And a plain in-process lock for the other *threads*, which
    the file cannot separate at all: they share a pid, so each would read the other's claim
    as its own and both would walk straight in.

    A claim whose process is gone is a crashed run rather than an owner, and is taken over
    instead of waited on: nobody is left locked out by a worker that died at the 50% mark.
    """
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / CLAIM
    lock = _local_lock(directory)
    if not lock.acquire(blocking=False):
        raise SeparationInProgressError(
            cause=(
                f"Another thread of this process (pid {os.getpid()}) is already separating "
                f"into {directory}."
            ),
            detail={"directory": str(directory), "pid": os.getpid()},
        )
    try:
        _take(marker)
        try:
            yield
        finally:
            _release(marker)
    finally:
        lock.release()


def _take(marker: Path) -> None:
    """Win the claim, or say who holds it — never read-then-write.

    Reading first and writing after is two separations both finding an empty directory and
    both proceeding; the exclusive create is what makes the answer the filesystem's. A create
    that is refused is not yet a refusal to run: the claim on disk may be one nothing is
    holding, and then it is cleared and the create tried once more. Only once more — a second
    loser is a live rival, not a stale file.
    """
    if _create(marker):
        return
    holder = _holder(marker)
    if holder is None:
        _discard(marker)
        if _create(marker):
            return
        holder = _holder(marker)
    directory = marker.parent
    cause = (
        f"Another process (pid {holder}) is already separating into {directory}."
        if holder is not None
        else f"Another process claimed {directory} at the same moment this one tried to."
    )
    raise SeparationInProgressError(
        cause=cause,
        detail={"directory": str(directory), "pid": holder},
    )


def _create(marker: Path) -> bool:
    """Write the claim only if nobody else has one; ``False`` means somebody does."""
    try:
        handle = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(handle, "w", encoding="utf-8") as sink:
        sink.write(json.dumps({"pid": os.getpid(), "session": SESSION, "claimed_at": time.time()}))
    log.info("Claimed the stems directory %s for pid %s", marker.parent, os.getpid())
    return True


def _read_claim(marker: Path) -> dict[str, Any] | None:
    """What the claim file says, or ``None`` if there is nothing legible there."""
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Ignoring an unreadable stems claim at %s", marker)
        return None
    if not isinstance(raw, dict):
        log.warning("Ignoring a stems claim that is not a record at %s", marker)
        return None
    return raw


def _holder(marker: Path) -> int | None:
    """The process still holding this claim, or ``None`` if it is there for the taking.

    Three ways a claim is there for the taking, and all three read fields the claim has always
    written and nothing has ever asked about. The pid is not enough on its own: it is a number
    the OS re-issues, so a claim outlives the process that wrote it and can end up naming a
    live stranger — or this very process, which is how a stale claim used to lock a directory
    out permanently while reporting that *we* were separating into it.
    """
    raw = _read_claim(marker)
    if raw is None:
        return None
    pid = raw.get("pid")
    session = raw.get("session")
    if not isinstance(pid, int):
        log.warning("Ignoring a stems claim that names no process at %s", marker)
        return None
    if session == SESSION:
        # This process wrote it, and no thread of ours holds the directory's lock — the caller
        # took that before asking. So it is our own leftover, from a run that died mid-write.
        log.info("Taking back the stems claim at %s: this process left it behind", marker)
        return None
    if pid == os.getpid():
        log.info(
            "Taking over the stems claim at %s: pid %s is now this process, not the one that "
            "wrote the claim",
            marker,
            pid,
        )
        return None
    if not pid_alive(pid):
        log.info("Taking over the stems claim at %s from pid %s, which is gone", marker, pid)
        return None
    claimed_at = raw.get("claimed_at")
    age = time.time() - claimed_at if isinstance(claimed_at, int | float) else 0.0
    if age > CLAIM_CEILING:
        log.warning(
            "Taking over the stems claim at %s from pid %s: it is %.1f hours old, so that pid "
            "is a recycled number rather than the separation that wrote it",
            marker,
            pid,
            age / 3600,
        )
        return None
    return pid


def _release(marker: Path) -> None:
    """Drop the claim, but only if it is still the one this process wrote."""
    raw = _read_claim(marker)
    if raw is None:
        return
    if raw.get("session") != SESSION or raw.get("pid") != os.getpid():
        log.info("Leaving the claim at %s alone: it is not the one this process wrote", marker)
        return
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clear the stems claim at %s", marker)
        return
    log.info("Released the stems claim at %s", marker)


def _discard(marker: Path) -> None:
    """Clear a claim nothing is holding, so the next create can win it."""
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clear the abandoned stems claim at %s", marker)


def multi_pass(
    audio: dict[str, Any],
    params: dict[str, Any],
    progress: Progress,
    split_wind: bool = False,
    runner: separator.Runner | None = None,
    reuse: bool = True,
    config: Config | None = None,
) -> JobOutput:
    """The worker: four stems, the drum stem decomposed, and on request the ``other`` stem too."""
    config = config or get_config()
    key = stem_key(audio, params)
    directory = config.stems_dir / f"{slug(Path(str(audio['path'])).stem, 'stems')}-{key[:12]}"
    mix_dir = directory / MIX_PASS
    drums_dir = directory / DRUM_PASS
    other_dir = directory / OTHER_PASS
    drums_ceiling = WIND_FLOOR if split_wind else SEPARATED

    reused = reuse and _already_separated(mix_dir, drums_dir, other_dir if split_wind else None)
    if reused:
        log.info("Stems for %s are already on disk at %s", audio["path"], directory)
        stems = separator.collect(mix_dir)
        drums = separator.collect(drums_dir)
        other = separator.collect(other_dir) if split_wind else {}
    else:
        with claimed(directory):
            stems = separator.separate(
                audio["path"],
                mix_dir,
                config.stem_model,
                FOUR_STEMS,
                progress=_pass(
                    progress, ACQUIRE_CEILING, PASS_ONE_CEILING, "separating four stems"
                ),
                runner=runner,
                config=config,
            )
            progress(PASS_ONE_CEILING, "decomposing the drum stem")
            drums = separator.separate(
                stems[DRUM_SOURCE],
                drums_dir,
                config.drum_model,
                DRUM_STEMS,
                progress=_pass(progress, PASS_ONE_CEILING, drums_ceiling, "decomposing the drums"),
                runner=runner,
                config=config,
            )
            other = {}
            if split_wind:
                progress(WIND_FLOOR, "splitting the winds out of the other stem")
                other = separator.separate(
                    stems[OTHER_SOURCE],
                    other_dir,
                    config.wind_model,
                    WIND_STEMS,
                    progress=_pass(progress, WIND_FLOOR, SEPARATED, "splitting the winds"),
                    runner=runner,
                    config=config,
                )

    progress(COLLECTING, "collecting the stems")
    result: dict[str, Any] = {
        "key": key,
        "directory": str(directory),
        "stems": {name: str(path) for name, path in stems.items()},
        "drums": {name: str(path) for name, path in drums.items()},
        "models": {"stems": config.stem_model, "drums": config.drum_model},
        "audio": audio,
        "reused": reused,
    }
    if split_wind:
        # Keyed like ``drums`` is: the name of the stem this pass took apart. Absent rather
        # than empty when the pass is off, so the envelope never offers a stem nobody made.
        # Only the two mapped halves go out — anything else the model wrote has no name here,
        # and a raw model label leaking into the envelope is worse than a file left on disk.
        result[OTHER_PASS] = {
            WIND_KEYS[name]: str(path) for name, path in other.items() if name in WIND_KEYS
        }
        result["models"][OTHER_PASS] = config.wind_model
    artifacts = tuple([*stems.values(), *drums.values(), *other.values()])
    log.info("Separated %s into %d stems at %s", audio["path"], len(artifacts), directory)
    return JobOutput(result, artifacts)


def _already_separated(mix_dir: Path, drums_dir: Path, other_dir: Path | None = None) -> bool:
    """Every pass this run was asked for is on disk in full — a partial run is worth redoing.

    ``other_dir`` is ``None`` when the third pass is off, and then a two-pass directory is
    complete rather than partial. The flag is not part of the stems key, so both shapes share
    a directory: what completeness means has to come from what was asked for, or a job with
    the pass off would rerun everything to fill a directory it does not want.
    """
    if separator.missing_from(mix_dir, FOUR_STEMS) or separator.missing_from(drums_dir, DRUM_STEMS):
        return False
    return other_dir is None or not separator.missing_from(other_dir, WIND_STEMS)


def _pass(progress: Progress, floor: float, ceiling: float, step: str) -> separator.Fraction:
    """Map one pass's own 0-1 onto the slice of the job that pass is.

    The bar always tracks the newest reading, including one lower than the last. A first
    run prints a bar for the model download before the bar for the separation, and
    ``separator`` drops the download's percentages precisely so that this stays rare — but
    holding the highest reading instead would be worse than a backslide: a download bar
    ends at 100%, so the separation that follows it, the long part, would report a frozen
    number for its whole run. A number that moves is the point.
    """
    span = ceiling - floor

    def report(fraction: float) -> None:
        progress(floor + span * fraction, f"{step} ({int(fraction * PERCENT)}%)")

    return report
