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

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from .. import lease
from ..config import Config, get_config
from ..errors import InternalError, InvalidRequestError, SeparationInProgressError
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs import runner as job_runner
from ..jobs.runner import Detached, JobOutput, Progress, start_job
from ..jobs.store import JobRecord, pid_alive
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
    param, so it stays in one stems directory rather than separating the same audio twice, and
    turning it on for audio already separated runs that pass alone against the stems on disk
    (#192).

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


def stem_directory(
    audio: dict[str, Any],
    params: dict[str, Any],
    config: Config | None = None,
) -> Path:
    """Where this audio's stems sit: the key, behind a name a human can recognise in a cache.

    The slug is for the person opening the directory; the key is what makes it this audio's and
    no other's. Named rather than spelled out at its one caller because anything that wants to
    look at a separation from outside — a live test measuring what a run cost — would otherwise
    have to write the same expression again, and a second copy of a key is a key that can drift.
    """
    config = config or get_config()
    key = stem_key(audio, params)
    return config.stems_dir / f"{slug(Path(str(audio['path'])).stem, 'stems')}-{key[:12]}"


CLAIM = ".separating.json"
"""The file that says a process is writing this stems directory right now. See ``claimed``."""

CLAIM_CEILING = 6 * 60 * 60
"""How old a claim may get before it is read as abandoned, in seconds.

Deliberately far longer than any separation: the longest measured pass over a full set is
under an hour, so six of them can only be a claim whose process is gone in a way the pid
check could not see — the pid recycled onto a live, unrelated process. Without a ceiling
that directory is locked out for the life of the machine.

Only a claim nobody is working on ages: a running separation refreshes its own (the callable
``claimed`` yields), so what this measures is silence rather than runtime.
"""

CLAIM_REFRESH = 60.0
"""How often a running separation rewrites its claim, in seconds.

The bar it is hung off moves several times a second while the claim only has to stay younger
than a ceiling measured in hours, so the lease throttles to this. See ``_keeping_the_claim``.
"""

CLAIM_WAIT = 2 * 60 * 60
"""How long a run waits for the separation already under way to finish, in seconds.

The directory is keyed by the audio's bytes and the models, so a run that finds it claimed is
not a caller to turn away: what the holder is writing is precisely the stems this run was asked
for. Since the tool detaches by default, arriving mid-pass is the ordinary shape of asking twice
— a retry, a second agent, the same call made again — and the old refusal met all three ten
minutes into the production of exactly what they wanted. Twice the longest measured pass over a
full set, so a run that does give up waited out a separation that was never going to finish;
well inside ``CLAIM_CEILING``, so giving up still comes long before the claim would be taken over.
"""

CLAIM_POLL = 5.0
"""How often a waiting run looks at the claim again, in seconds — one small read of one file."""

Waiting = lease.Waiting
"""Told, each time round, which process is being waited for. See ``claimed``."""


@contextmanager
def claimed(
    directory: Path,
    waiting: Waiting | None = None,
    budget: float | None = None,
    poll: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Callable[[], None]]:
    """Hold this stems directory for one separation at a time.

    The directory is keyed by the audio's bytes and the models, so two runs over the same mix
    — a retry, a second agent, a job started twice — land in the same one, and two separators
    writing the same files interleave into stems that are neither run's: half the tracks from
    each, all of them looking complete to the reuse check that reads them next.

    All of the protocol is ``lease``'s — the file, the in-process lock, who may take a claim
    over and when a wait has gone on too long — because a stems directory and a detached job
    record are the same claim asked the same question: is the process that wrote this still
    working? What is here is the policy: how long a claim may go quiet (``CLAIM_CEILING``),
    how long a run waits for the separation already under way (``CLAIM_WAIT``), how often the
    claim is rewritten (``CLAIM_REFRESH``), and what the agent is told when the answer is no.

    A rival that is genuinely working is **waited out** rather than refused, whenever the caller
    passes ``waiting`` — the callback that says the wait is still going, so a job with a progress
    bar never reads as hung. Waiting is the right answer because of what keys the directory: the
    run holding it is producing exactly the stems the waiting run wants, so the wait ends with
    them on disk and the caller reading them instead of paying the GPU a second time. A caller
    with nowhere to report the wait leaves it off and gets the refusal, which still names the
    holder. ``budget`` and ``poll`` default to ``CLAIM_WAIT`` and ``CLAIM_POLL``, read when the
    wait starts rather than when this was defined.

    Yields the callable that keeps the claim young: a separation runs for as long as an hour,
    the ceiling that reads a claim as abandoned is measured from when it was written, and a
    claim nobody touches is eventually stolen out from under a run that is still going. It is
    yielded already wrapped, so a separation that has lost its directory hears about it in this
    module's words at the moment it reports, rather than as a lease error at the end.
    """
    marker = directory / CLAIM
    try:
        with lease.claim(
            marker,
            ceiling=CLAIM_CEILING,
            refresh=CLAIM_REFRESH,
            alive=pid_alive,
            waiting=waiting,
            budget=CLAIM_WAIT if budget is None else budget,
            poll=CLAIM_POLL if poll is None else poll,
            sleep=sleep,
        ) as beat:
            yield _reporting(directory, beat)
    except lease.LeaseHeld as held:
        raise _in_progress(directory, held) from held


def _reporting(directory: Path, beat: Callable[[], None]) -> Callable[[], None]:
    """The lease's refresh, saying who has the directory in the words the agent reads."""

    def refresh() -> None:
        try:
            beat()
        except lease.LeaseHeld as held:
            raise _in_progress(directory, held) from held

    return refresh


def _in_progress(directory: Path, held: lease.LeaseHeld) -> SeparationInProgressError:
    """Who has the directory, said the way the agent reads it.

    Three refusals, because they are three different things to be told: the rival that already
    had it, a thread of this very server that has it (the claim file cannot separate those —
    they share a pid), and the one that arrives mid-separation, where the directory this run is
    *writing* has become somebody else's. That last one stops the separation rather than
    carrying on quietly, because what carrying on produces is a set of stems from two runs that
    looks complete to the reuse check reading it next.
    """
    return SeparationInProgressError(
        cause=_refusal(directory, held),
        detail={"directory": str(directory), "pid": held.pid},
    )


def _refusal(directory: Path, held: lease.LeaseHeld) -> str:
    """The sentence for this refusal, chosen by what the lease said was in the way."""
    if held.reason is lease.Reason.THREAD:
        return (
            f"Another thread of this process (pid {held.pid}) is already separating "
            f"into {directory}."
        )
    if held.reason is lease.Reason.LOST:
        return (
            f"Another process (pid {held.pid}) has taken the claim on {directory} while this "
            "separation was writing into it."
            if held.pid is not None
            else f"The claim this separation was holding on {directory} is gone."
        )
    return (
        f"Another process (pid {held.pid}) is already separating into {directory}."
        if held.pid is not None
        else f"Another process claimed {directory} at the same moment this one tried to."
    )


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
    directory = stem_directory(audio, params, config)
    mix_dir = directory / MIX_PASS
    drums_dir = directory / DRUM_PASS
    other_dir = directory / OTHER_PASS
    wanted_other = other_dir if split_wind else None

    separator_env: dict[str, Any] | None = None
    devices: list[str] = []
    reused = reuse and _already_separated(mix_dir, drums_dir, other_dir, split_wind)
    if reused:
        log.info("Stems for %s are already on disk at %s", audio["path"], directory)
        stems, drums, other = _on_disk(mix_dir, drums_dir, wanted_other)
    else:
        with claimed(directory, waiting=_waiting_for(progress)) as refresh:
            # Asked a second time, now from inside the claim. The first reading had to come
            # before it — taking the claim is what that reading decides — and between the two
            # this run may have waited out another separation over the same audio. The stems it
            # was about to compute are the ones that run has just finished writing, so asking
            # only once is asking too early: it is half an hour of GPU spent overwriting a
            # byte-identical answer, and the wait was for those very files.
            reused = reuse and _already_separated(mix_dir, drums_dir, other_dir, split_wind)
            if reused:
                log.info(
                    "The separation this run waited out has left the stems for %s at %s",
                    audio["path"],
                    directory,
                )
                stems, drums, other = _on_disk(mix_dir, drums_dir, wanted_other)
            else:
                # Asked only when a separation is actually about to run: the report says
                # what *this* work ran on, and a reuse ran nothing (#202).
                separator_env = separator.environment(runner=runner, config=config)
                stems, drums, other = _passes(
                    audio,
                    mix_dir,
                    drums_dir,
                    other_dir,
                    split_wind=split_wind,
                    progress=progress,
                    refresh=refresh,
                    runner=runner,
                    config=config,
                    reuse=reuse,
                    on_device=devices.append,
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
    if separator_env is not None:
        # What the passes said they ran on, beside the build they ran under (#188). Only the
        # fresh path has either: a reuse ran no process, and no process said anything.
        separator.record_device(separator_env, devices)
        result["separator"] = separator_env
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


class _Sets(NamedTuple):
    """The three passes' stems, however they were come by — computed here or already on disk."""

    mix: dict[str, Path]
    drums: dict[str, Path]
    other: dict[str, Path]
    """Empty when the third pass is off, which is the shape the envelope already expects."""


def _on_disk(mix_dir: Path, drums_dir: Path, other_dir: Path | None) -> _Sets:
    """What the passes left behind, read back. ``other_dir`` is ``None`` with the third pass off."""
    return _Sets(
        separator.collect(mix_dir),
        separator.collect(drums_dir),
        separator.collect(other_dir) if other_dir is not None else {},
    )


def _passes(
    audio: dict[str, Any],
    mix_dir: Path,
    drums_dir: Path,
    other_dir: Path,
    split_wind: bool,
    progress: Progress,
    refresh: Callable[[], None],
    runner: separator.Runner | None,
    config: Config,
    reuse: bool,
    on_device: separator.Device | None = None,
) -> _Sets:
    """The two passes, and the third when it is asked for — run with the claim already held.

    Only the passes this directory is actually missing are run (#192). The wind flag is not
    part of the stems key, so turning it on lands in the directory the first two passes already
    filled, and treating that as partial-so-redo-it-whole spent the better part of an hour of
    GPU rewriting two byte-identical answers to add a third. What a pass costs is the reason
    the flag is opt-in at all; paying for the other two as well was the same mistake twice.

    Every reading of the bar refreshes the claim, so the ceiling that reads a claim as
    abandoned measures how long it has been since anybody was working — not how long this
    separation has run. A full set can take the better part of an hour.
    """
    beat = _keeping_the_claim(progress, refresh)
    done = _complete(mix_dir, drums_dir, other_dir, split_wind) if reuse else _Done()
    if done.mix:
        log.info("Reusing the four stems already at %s", mix_dir)
        stems = separator.collect(mix_dir)
    else:
        stems = separator.separate(
            audio["path"],
            mix_dir,
            config.stem_model,
            FOUR_STEMS,
            progress=_pass(beat, ACQUIRE_CEILING, PASS_ONE_CEILING, "separating four stems"),
            runner=runner,
            config=config,
            on_device=on_device,
        )
    if done.drums:
        log.info("Reusing the drum stems already at %s", drums_dir)
        drums = separator.collect(drums_dir)
    else:
        beat(PASS_ONE_CEILING, "decomposing the drum stem")
        drums = separator.separate(
            stems[DRUM_SOURCE],
            drums_dir,
            config.drum_model,
            DRUM_STEMS,
            progress=_pass(
                beat,
                PASS_ONE_CEILING,
                WIND_FLOOR if split_wind else SEPARATED,
                "decomposing the drums",
            ),
            runner=runner,
            config=config,
            on_device=on_device,
        )
    other: dict[str, Path] = {}
    if not split_wind:
        return _Sets(stems, drums, other)
    if done.other:
        log.info("Reusing the wind split already at %s", other_dir)
        other = separator.collect(other_dir)
    else:
        beat(WIND_FLOOR, "splitting the winds out of the other stem")
        other = separator.separate(
            stems[OTHER_SOURCE],
            other_dir,
            config.wind_model,
            WIND_STEMS,
            progress=_pass(beat, WIND_FLOOR, SEPARATED, "splitting the winds"),
            runner=runner,
            config=config,
            on_device=on_device,
        )
    return _Sets(stems, drums, other)


def _waiting_for(progress: Progress) -> Waiting:
    """Report the wait for another separation as a step of this job, so it never reads as hung.

    The bar does not move while the wait lasts: none of this job's own work is happening, and
    borrowing somebody else's percentages would say the opposite of what the step says. What
    the step carries instead is who is being waited for — a poller reading "waiting for the
    separation in pid 9000 to finish" can see that the answer is coming from a run already
    under way, which is the thing that makes the wait better than the refusal it replaced.
    """

    def report(pid: int | None) -> None:
        who = f"pid {pid}" if pid is not None else "another process"
        progress(ACQUIRE_CEILING, f"waiting for the separation in {who} to finish")

    return report


class _Done(NamedTuple):
    """Which passes this run does not owe. Every one is owed until it has been looked for."""

    mix: bool = False
    drums: bool = False
    other: bool = False
    """On disk in full — or not asked for, which costs the same nothing to satisfy."""


def _complete(mix_dir: Path, drums_dir: Path, other_dir: Path, split_wind: bool) -> _Done:
    """Read each pass's directory for the stems that pass is supposed to have left there.

    A missing first pass makes the other two unreusable whatever is in their directories: both
    are cut from files the first pass wrote, so a mix that has to run again would leave one
    separation's drums sitting beside another's under a name that promises they came from the
    same run. The dependency only runs that way — the drum pass and the wind pass never read
    each other — so either can be the one thing a directory owes.

    The wind flag is answered here and nowhere else. It is not part of the stems key, so a
    two-pass and a three-pass run share a directory, and what completeness means has to come
    from what this run asked for: with the pass off, an empty ``other`` is nothing owed rather
    than something missing. Reading that off the flag in each caller was three chances to read
    it differently.
    """
    if separator.missing_from(mix_dir, FOUR_STEMS):
        return _Done()
    return _Done(
        mix=True,
        drums=not separator.missing_from(drums_dir, DRUM_STEMS),
        other=not split_wind or not separator.missing_from(other_dir, WIND_STEMS),
    )


def _already_separated(mix_dir: Path, drums_dir: Path, other_dir: Path, split_wind: bool) -> bool:
    """Nothing is owed at all: every pass this run was asked for is on disk in full.

    The question the claim is taken on. A directory that owes one pass is not this, and is not
    redone whole either — ``_passes`` runs the passes it is missing (#192).
    """
    return all(_complete(mix_dir, drums_dir, other_dir, split_wind))


def _keeping_the_claim(progress: Progress, refresh: Callable[[], None]) -> Progress:
    """The progress bar with a claim refresh hung off it.

    The bar is the only thing that reports from inside a separation, so it is the only place a
    claim can be kept young from. What is left here is the wiring: the throttle that turns
    several readings a second into one write a minute is the lease's, so that the claim and the
    ceiling it is measured against are kept in step by one module (``CLAIM_REFRESH``).
    """

    def report(fraction: float, step: str) -> None:
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
