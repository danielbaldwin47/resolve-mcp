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
from typing import Any, NamedTuple

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

Only a claim nobody is working on ages: a running separation refreshes its own (see
``_touch``), so what this measures is silence rather than runtime.
"""

CLAIM_REFRESH = 60.0
"""How often a running separation rewrites its claim, in seconds. See ``_keeping_the_claim``."""

_claims: dict[str, threading.Lock] = {}
_claims_lock = threading.Lock()
"""One lock per stems directory, for the threads of *this* process. See ``claimed``."""


def _local_lock(directory: Path) -> threading.Lock:
    """The lock this process uses for that directory, made once and kept."""
    key = os.path.normcase(str(directory.resolve()))
    with _claims_lock:
        return _claims.setdefault(key, threading.Lock())


@contextmanager
def claimed(directory: Path) -> Iterator[Callable[[], None]]:
    """Hold this stems directory for one separation at a time.

    The directory is keyed by the audio's bytes and the models, so two runs over the same mix
    — a retry, a second agent, a job started twice — land in the same one, and two separators
    writing the same files interleave into stems that are neither run's: half the tracks from
    each, all of them looking complete to the reuse check that reads them next.

    Two locks, because there are two kinds of rival. A file for the other *processes*: since
    G4 a separation runs in a detached worker, and disk is the only thing those share — the
    same reason the job record itself is a file. The claim is written under a name of this
    process's own and then *linked* into place, so the winner is the one whose link the
    filesystem accepted rather than whoever read an empty directory last — and what appears
    under the claim's name is a complete claim, never the empty file an exclusive create
    publishes before its content lands. And a plain in-process lock for the other *threads*,
    which the file cannot separate at all: they share a pid, so each would read the other's
    claim as its own and both would walk straight in.

    A claim whose process is gone is a crashed run rather than an owner, and is taken over
    instead of waited on: nobody is left locked out by a worker that died at the 50% mark. A
    claim that is merely unreadable is not that — it is waited on, because the one thing that
    reliably makes a claim unreadable is being read at the moment it is written.

    Yields the callable that keeps the claim young: a separation runs for as long as an hour,
    the ceiling that reads a claim as abandoned is measured from when it was written, and a
    claim nobody touches is eventually stolen out from under a run that is still going.
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
            yield lambda: _touch(marker)
        finally:
            _release(marker)
    finally:
        lock.release()


class _Held(NamedTuple):
    """What the claim on disk says, as ``_take`` needs to hear it."""

    held: bool
    """Somebody is separating into this directory right now — wait rather than take over."""
    pid: int | None
    """Which process, when the claim names one legibly."""
    judged: bytes | None
    """The exact bytes this verdict was reached on, so a stale claim is cleared by identity."""


def _take(marker: Path) -> None:
    """Win the claim, or say who holds it — never read-then-write.

    Reading first and writing after is two separations both finding an empty directory and
    both proceeding; the link is what makes the answer the filesystem's. A link that is
    refused is not yet a refusal to run: the claim on disk may be one nothing is holding, and
    then it is cleared and the link tried once more. Only once more — a second loser is a live
    rival, not a stale file.
    """
    if _create(marker):
        return
    verdict = _holder(marker)
    if not verdict.held:
        _discard(marker, verdict.judged)
        if _create(marker):
            return
        verdict = _holder(marker)
    directory = marker.parent
    cause = (
        f"Another process (pid {verdict.pid}) is already separating into {directory}."
        if verdict.pid is not None
        else f"Another process claimed {directory} at the same moment this one tried to."
    )
    raise SeparationInProgressError(
        cause=cause,
        detail={"directory": str(directory), "pid": verdict.pid},
    )


def _content() -> bytes:
    """The claim this process would write, as the bytes that go on disk."""
    return json.dumps({"pid": os.getpid(), "session": SESSION, "claimed_at": time.time()}).encode()


def _create(marker: Path) -> bool:
    """Publish this process's claim only if nobody else has one; ``False`` means somebody does.

    Written under a name of this process's own and hard-linked into place, rather than created
    exclusively and then filled in. Both refuse a second winner, but an exclusive create
    publishes the claim's *name* before its content: a rival reading in that instant finds an
    empty file, reads it as unreadable, and the recovery for an unreadable claim — whatever it
    is — is being run against the winner's live claim rather than against a stale one. A link
    makes the name and the content appear together, so there is no such instant to read.

    A filesystem that refuses hard links altogether (a cache directory on exFAT) falls back to
    the exclusive create, which is still correct about who won; the empty-file window is then
    covered by ``_holder`` reading an unreadable claim as held rather than adoptable.
    """
    scratch = marker.with_name(f"{marker.name}.{os.getpid()}")
    payload = _content()
    try:
        scratch.write_bytes(payload)
        try:
            os.link(scratch, marker)
        except FileExistsError:
            return False
        except OSError as exc:
            log.warning(
                "This filesystem will not link a stems claim into place (%s); falling back to "
                "an exclusive create at %s",
                exc,
                marker,
            )
            return _create_exclusively(marker, payload)
    finally:
        scratch.unlink(missing_ok=True)
    log.info("Claimed the stems directory %s for pid %s", marker.parent, os.getpid())
    return True


def _create_exclusively(marker: Path, payload: bytes) -> bool:
    """The fallback for a filesystem with no hard links: create, then fill in."""
    try:
        handle = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(handle, "wb") as sink:
        sink.write(payload)
    log.info("Claimed the stems directory %s for pid %s", marker.parent, os.getpid())
    return True


def _touch(marker: Path) -> None:
    """Say the claim is still being worked on, so the ceiling measures staleness not runtime.

    ``CLAIM_CEILING`` exists for a claim whose process is gone in a way the pid check cannot
    see, and it can only mean that if a claim that *is* being worked on stays young. Without
    this a separation long enough to pass the ceiling — the whole case the ceiling is written
    for — has its own claim read as abandoned and a second separator walks into the directory
    it is still writing. Rewritten rather than ``utime``d because the age is read out of the
    claim's content, and only while the claim is still this process's to rewrite.

    Finding the claim is somebody else's stops the separation rather than skipping the
    refresh. The claim is what says who may write these files, so a run that has lost it is a
    run writing stems into a directory another separator now owns — the interleaving the claim
    exists to prevent, arrived at from the inside. Carrying on quietly would produce exactly
    the half-and-half set that looks complete to the reuse check reading it next, so the run
    is failed here instead, naming whoever holds the directory now.

    A refresh that cannot be *written* is only a claim left to age, so that stays a warning:
    the directory is still ours and the pass still running, and the next reading of the bar
    tries again.
    """
    raw = _read_claim(marker)
    if raw is None or raw.get("session") != SESSION or raw.get("pid") != os.getpid():
        raise _lost_the_claim(marker, raw)
    scratch = marker.with_name(f"{marker.name}.{os.getpid()}")
    try:
        scratch.write_bytes(_content())
        os.replace(scratch, marker)
    except OSError:
        log.warning("Could not refresh the stems claim at %s", marker)
        # Guarded in its own right: this runs on the path where writing that very file already
        # failed, and on Windows a scratch file another handle still holds refuses the unlink
        # too. Letting that out would end a running separation over a leftover file nothing
        # reads — the scratch name carries this process's pid, so no other run reads it as a
        # claim.
        try:
            scratch.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - a scratch claim left behind is read by nothing
            log.debug("Could not clear the scratch claim at %s", scratch)


def _lost_the_claim(marker: Path, raw: dict[str, Any] | None) -> SeparationInProgressError:
    """Why a separation stops mid-pass: the directory it is writing is not its own any more."""
    holder = raw.get("pid") if raw is not None else None
    pid = holder if isinstance(holder, int) else None
    log.warning("The claim at %s is no longer this process's; stopping the separation", marker)
    cause = (
        f"Another process (pid {pid}) has taken the claim on {marker.parent} while this "
        "separation was writing into it."
        if pid is not None
        else f"The claim this separation was holding on {marker.parent} is gone."
    )
    return SeparationInProgressError(
        cause=cause,
        detail={"directory": str(marker.parent), "pid": pid},
    )


def _read_claim(marker: Path) -> dict[str, Any] | None:
    """What the claim file says, or ``None`` if there is nothing legible there."""
    raw = _claim_bytes(marker)
    return None if raw is None else _parse_claim(marker, raw)


def _claim_bytes(marker: Path) -> bytes | None:
    """The claim exactly as it is on disk, or ``None`` if there is no claim there."""
    try:
        return marker.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Could not read the stems claim at %s", marker)
        return b""


def _parse_claim(marker: Path, raw: bytes) -> dict[str, Any] | None:
    """The claim as a record, or ``None`` if those bytes are not one."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.warning("Ignoring an unreadable stems claim at %s", marker)
        return None
    if not isinstance(parsed, dict):
        log.warning("Ignoring a stems claim that is not a record at %s", marker)
        return None
    return parsed


def _holder(marker: Path) -> _Held:
    """Whether this claim is still held, and by whom — or that it is there for the taking.

    Four ways a claim is there for the taking, and all of them read fields the claim has
    always written. The pid is not enough on its own: it is a number the OS re-issues, so a
    claim outlives the process that wrote it and can end up naming a live stranger — or this
    very process, which is how a stale claim used to lock a directory out permanently while
    reporting that *we* were separating into it.

    A claim that cannot be read at all is the one case that is *not* adoptable. Reading it is
    a guess either way, and the two guesses are not symmetrical: read as adoptable, the likely
    cause — a claim caught mid-write, or a rival's write this filesystem has not shown us yet
    — costs a second separator in a directory somebody is writing, which is the whole failure
    this file exists to prevent. Read as held, an actually-corrupt claim costs a refused job
    until the ceiling ages it out, and the ceiling is what clears it.
    """
    raw = _claim_bytes(marker)
    if raw is None:
        return _Held(held=False, pid=None, judged=None)
    parsed = _parse_claim(marker, raw)
    if parsed is None:
        return _unreadable(marker, raw)
    pid = parsed.get("pid")
    if not isinstance(pid, int):
        log.warning("Ignoring a stems claim that names no process at %s", marker)
        return _unreadable(marker, raw)
    session = parsed.get("session")
    if session == SESSION:
        # This process wrote it, and no thread of ours holds the directory's lock — the caller
        # took that before asking. So it is our own leftover, from a run that died mid-write.
        log.info("Taking back the stems claim at %s: this process left it behind", marker)
        return _Held(held=False, pid=pid, judged=raw)
    if pid == os.getpid():
        log.info(
            "Taking over the stems claim at %s: pid %s is now this process, not the one that "
            "wrote the claim",
            marker,
            pid,
        )
        return _Held(held=False, pid=pid, judged=raw)
    if not pid_alive(pid):
        log.info("Taking over the stems claim at %s from pid %s, which is gone", marker, pid)
        return _Held(held=False, pid=pid, judged=raw)
    claimed_at = parsed.get("claimed_at")
    age = time.time() - claimed_at if isinstance(claimed_at, int | float) else 0.0
    if age > CLAIM_CEILING:
        log.warning(
            "Taking over the stems claim at %s from pid %s: it is %.1f hours old, so that pid "
            "is a recycled number rather than the separation that wrote it",
            marker,
            pid,
            age / 3600,
        )
        return _Held(held=False, pid=pid, judged=raw)
    return _Held(held=True, pid=pid, judged=raw)


def _unreadable(marker: Path, raw: bytes) -> _Held:
    """A claim that names no process: held while it is young, adoptable once it is not.

    Its age cannot come from its content — that is the part that could not be read — so it
    comes from the file's own timestamp, which is the moment it was linked into place.
    """
    try:
        age = time.time() - marker.stat().st_mtime
    except OSError:
        age = 0.0
    if age > CLAIM_CEILING:
        log.warning(
            "Taking over the stems claim at %s: it names no process and is %.1f hours old",
            marker,
            age / 3600,
        )
        return _Held(held=False, pid=None, judged=raw)
    log.info(
        "Waiting out the claim at %s: it names no process, which is what a claim being written "
        "right now looks like",
        marker,
    )
    return _Held(held=True, pid=None, judged=raw)


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


def _discard(marker: Path, judged: bytes | None) -> None:
    """Clear the exact claim that was judged abandoned — never whatever is there now.

    The gap between judging a claim and unlinking it is one in which the judged claim can be
    released and a rival's live one can appear under the same name: unlinking by name alone
    hands a second separator into a directory the first is writing, which is what the claim
    exists to prevent. So the bytes are read back and matched first. That leaves a window of
    its own, narrower by the whole liveness check and unreachable on the ordinary path — the
    link in ``_create`` is what makes the ordinary path safe, and nothing but a claim already
    judged stale ever reaches this.
    """
    if judged is None:
        return
    current = _claim_bytes(marker)
    if current is None:
        return
    if current != judged:
        log.info("Leaving the claim at %s alone: it changed after it was judged abandoned", marker)
        return
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clear the abandoned stems claim at %s", marker)
        return
    log.info("Cleared the abandoned stems claim at %s", marker)


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
        with claimed(directory) as refresh:
            # Every reading of the bar refreshes the claim, so the ceiling that reads a claim
            # as abandoned measures how long it has been since anybody was working — not how
            # long this separation has run. A full set can take the better part of an hour.
            beat = _keeping_the_claim(progress, refresh)
            stems = separator.separate(
                audio["path"],
                mix_dir,
                config.stem_model,
                FOUR_STEMS,
                progress=_pass(beat, ACQUIRE_CEILING, PASS_ONE_CEILING, "separating four stems"),
                runner=runner,
                config=config,
            )
            beat(PASS_ONE_CEILING, "decomposing the drum stem")
            drums = separator.separate(
                stems[DRUM_SOURCE],
                drums_dir,
                config.drum_model,
                DRUM_STEMS,
                progress=_pass(beat, PASS_ONE_CEILING, drums_ceiling, "decomposing the drums"),
                runner=runner,
                config=config,
            )
            other = {}
            if split_wind:
                beat(WIND_FLOOR, "splitting the winds out of the other stem")
                other = separator.separate(
                    stems[OTHER_SOURCE],
                    other_dir,
                    config.wind_model,
                    WIND_STEMS,
                    progress=_pass(beat, WIND_FLOOR, SEPARATED, "splitting the winds"),
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


def _keeping_the_claim(progress: Progress, refresh: Callable[[], None]) -> Progress:
    """The progress bar with a claim refresh hung off it — at most one every minute.

    The bar is the only thing that reports from inside a separation, so it is the only place
    a claim can be kept young from. Throttled because it moves several times a second while
    the claim only has to stay younger than a ceiling measured in hours: refreshing on every
    reading would be thousands of writes to say what one a minute already says. The clock
    starts now rather than at zero, because the claim was written a moment ago.
    """
    last = time.monotonic()

    def report(fraction: float, step: str) -> None:
        nonlocal last
        now = time.monotonic()
        if now - last >= CLAIM_REFRESH:
            last = now
            refresh()
        progress(fraction, step)

    return report


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
