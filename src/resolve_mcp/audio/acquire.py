"""Audio acquisition, both routes — the substrate every analysis job runs on.

Which route is chosen is not a preference, it is a correctness question:

* **Timeline scope goes through the render queue.** It is the only route that captures the
  timeline *mix* — Resolve's own summing of the tracks, with the director's clip levels and
  track mapping applied. Nothing outside Resolve can reproduce that. 48 kHz / 24-bit WAV,
  audio only.

* **A single source clip goes through ffmpeg.** The clip's File Path is a file on disk;
  reading it directly is far faster than a render and leaves the GUI alone. This is only
  truthful while Resolve is not doing anything to that audio, so the clip's audio mapping
  is checked first and a linked or offset source is refused with a pointer at the timeline
  route rather than quietly extracting the wrong thing.

Caching follows the same split (see ``jobs.cache``): the source clip is fingerprinted
cheaply, the acquired WAV is hashed for real, and that hash is what analysis jobs key off.
A timeline has no bytes to fingerprint, so its identity is name, unique id, bounds, track
counts and a digest of the shots on it — still a heuristic, because a clip's audio level is
not readable through the scripting API at all, which is why every starter takes ``refresh``
to force the export again when the director changed something no reading can see.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import (
    AudioExportError,
    AudioExtractionError,
    AudioMappingError,
    InvalidRequestError,
    RenderQueueError,
)
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, band, start_job
from ..logging_config import get_logger
from ..naming import keyed_name
from ..resolve import media, render
from ..resolve.connection import ResolveConnection
from ..resolve.session import current_project
from ..resolve.timeline import Reader, current_timeline, find_timeline, fingerprint, read_frames
from . import ffmpeg, wav

log = get_logger("audio")

Timeline = Any
Project = Any

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_BIT_DEPTH = 24

TIMELINE_KIND = "acquire_timeline_audio"
CLIP_KIND = "acquire_clip_audio"

TIMELINE_SCOPE = "timeline"
CLIP_SCOPE = "clip"
SCOPES = (TIMELINE_SCOPE, CLIP_SCOPE)

RENDER_FORMAT = "wav"
RENDER_CODEC = "lpcm"
#: Stock Resolve preset, and the only route to a WAV on a build that refuses the pair above.
AUDIO_ONLY_PRESET = "Audio Only"

EXPORT_FLOOR = 0.1
EXPORT_CEILING = 0.9

OFFSET_HINT = ("offset", "delay")


# --- timeline scope: the render queue ----------------------------------------------------


def acquire_timeline_audio(
    connection: ResolveConnection,
    timeline: str | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    refresh: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job that exports the timeline mix. Returns the job record, not the audio."""
    config = config or get_config()
    project = current_project(connection, "No project is open, so there is no timeline to export.")
    found = find_timeline(project, timeline)
    name = str(found.GetName() or "timeline")
    refuse_a_timeline_with_no_audio(found, name)
    params = _timeline_params(name, sample_rate, bit_depth)
    identity = fingerprint(Reader(connection), found)
    key = cache.cache_key(TIMELINE_KIND, [identity], params)

    def work(progress: Progress) -> JobOutput:
        return export_timeline_mix(project, found, key, params, progress, config)

    return start_job(
        TIMELINE_KIND,
        params,
        work,
        cache_key=key,
        touches_resolve=True,
        refresh=refresh,
        config=config,
    )


def export_timeline_mix(
    project: Project,
    timeline: Timeline,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    config: Config | None = None,
) -> JobOutput:
    """The worker: queue an audio-only render of one timeline and wait for the WAV."""
    config = config or get_config()
    # Checked in the starters too, so the refusal reaches the agent as a reply rather than a
    # job it has to poll. This is the copy that holds the invariant: nothing this worker is
    # handed reaches the queue unchecked, whichever route called it.
    refuse_a_timeline_with_no_audio(timeline, str(params["timeline"]))
    target_dir = config.audio_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = keyed_name(str(params["timeline"]), key, "", "audio")
    expecting = target_dir / f"{stem}.wav"

    progress(0.05, "queuing the audio export")
    with current_timeline(project, timeline):
        # Only here, with the timeline current, can a track's enabled state be believed
        # (#84), so the second half of the precondition waits until the switch has happened
        # and still runs before anything is queued.
        refuse_a_timeline_with_no_audio_switched_on(timeline, str(params["timeline"]))
        # One file for the whole timeline. The other mode renders a file per clip on it,
        # which for a concert cut is hundreds of fragments instead of the mix.
        project.SetCurrentRenderMode(render.SINGLE_CLIP)
        settings = {
            "SelectAllFrames": True,
            "TargetDir": str(target_dir),
            "CustomName": stem,
            "ExportVideo": False,
            "ExportAudio": True,
            "AudioCodec": RENDER_CODEC,
            "AudioBitDepth": int(params["bit_depth"]),
            "AudioSampleRate": int(params["sample_rate"]),
        }
        try:
            job_id = render.submit(
                project,
                settings,
                (RENDER_FORMAT, RENDER_CODEC),
                fallback_preset=AUDIO_ONLY_PRESET,
            )
            render.render(
                project,
                job_id,
                expecting,
                band(progress, EXPORT_FLOOR, EXPORT_CEILING),
            )
        except RenderQueueError as exc:
            raise _as_export_failure(exc) from exc

    progress(0.95, "hashing the export")
    return JobOutput(_result(expecting, params), (expecting,))


def refuse_a_timeline_with_no_audio(timeline: Timeline, name: str) -> None:
    """Refuse to export a timeline with nothing on its audio tracks, before anything queues.

    Resolve takes such a render and never runs it: the job sits at "Ready for background
    render" at 0%, ``IsRenderingInProgress()`` reports ``True``, no dialog opens, and there
    is no error and no timeout — only ``DeleteAllRenderJobs()`` clears it, and the next one
    wedges the same way (#88, live on Studio 21.0.3.7). A hang is the one failure nothing
    downstream can recover from or explain, so the precondition is checked before the queue
    can see the job.

    Only a reading that *succeeded* is allowed to refuse. A getter that answers ``None`` —
    the way this API says "not on this build", and the way a dying handle reads — leaves
    this silent rather than turning an unreadable count into "no audio": refusing a timeline
    that has a mix on it is the failure this guard would otherwise trade the hang for, and
    it is the worse of the two, because the hang at least announces itself by never
    finishing. For the same reason the counts are not taken through the shared
    ``timeline.items_in_track``, which folds ``None`` into an empty list.

    Reading counts off a timeline that is not the current one is sound — verified live on
    21.0.3.7, where ``GetItemListInTrack`` agreed exactly with the current-timeline reading,
    unlike ``GetTakesCount`` and ``GetIsTrackEnabled`` (#84). That is why this half of the
    precondition can run in a starter and ``refuse_a_timeline_with_no_audio_switched_on``
    cannot.
    """
    counted = _audio_items(timeline, name, only_enabled=False)
    if counted is None or counted.items:
        return
    log.info("Refusing a mix export of %r: %d audio tracks, no items on them", name, counted.tracks)
    raise AudioExportError(
        cause=f"Timeline {name!r} has no audio items, so there is no mix to export.",
        fix=(
            "Check you targeted the timeline you meant — list_timelines names them, and "
            "inspect_timeline reports what sits on each track. Resolve queues an audio-only "
            "render of a timeline with no audio on it and never runs it, so this refuses "
            "instead."
        ),
        detail={"timeline": name, "audio_tracks": counted.tracks, "audio_items": 0},
    )


def refuse_a_timeline_with_no_audio_switched_on(timeline: Timeline, name: str) -> None:
    """Refuse when no audio track that is switched on has anything on it.

    Resolve wedges on this exactly as it wedges on a timeline with no audio at all: one
    clip on A1, A1 disabled, and the render sits at "Ready for background render" at 0%
    with ``IsRenderingInProgress()`` True for as long as you care to watch (live on
    21.0.3.7, while verifying #88). The hang is not "no items" — it is "nothing to render",
    which is why the count that matters here is per *live* track and not per track.

    **This must only be called with ``timeline`` current.** ``GetIsTrackEnabled`` answers
    ``False`` for every track of a timeline that is not the current one (#84), so run
    anywhere else it would refuse every timeline it was handed.
    """
    counted = _audio_items(timeline, name, only_enabled=True)
    if counted is None or counted.items:
        return
    log.info("Refusing a mix export of %r: nothing on any audio track that is on", name)
    raise AudioExportError(
        cause=(
            f"No audio track on timeline {name!r} is both switched on and carrying audio, "
            f"so the mix would be silent."
        ),
        fix=(
            "Switch on the audio track the mix should come from in Resolve's timeline, or "
            "target the timeline you meant — inspect_timeline reports each track and "
            "whether it is on. Resolve queues a render with nothing to render and never "
            "runs it, so this refuses instead."
        ),
        detail={"timeline": name, "audio_tracks": counted.tracks, "audio_items_switched_on": 0},
    )


class _AudioItems(NamedTuple):
    """What the audio tracks hold: how many tracks were read, and how many items were on them."""

    tracks: int
    items: int


def _audio_items(timeline: Timeline, name: str, only_enabled: bool) -> _AudioItems | None:
    """Count the items on the timeline's audio tracks, or ``None`` if any reading failed.

    ``None`` is the whole point: it is what keeps an unreadable timeline from being refused
    as an empty one, because a caller that cannot tell "no audio" from "no answer" must not
    refuse. It covers the getter that *answers* nothing — a handle on its way out.

    A build that lacks one of these getters outright is a different failure and is left to
    raise: fusionscript answers every attribute name, so the missing method reads as ``None``
    and the *call* dies with ``NoneType is not callable`` (#41, live on 21.0.3.7). That is
    loud, lands as a failed job, and is honest; these three getters are old enough that a
    build without them is not a case worth degrading a precondition for.
    """
    tracks = read_frames(timeline.GetTrackCount("audio"))
    if tracks is None:
        log.info("Not checking %r for audio: its audio track count did not read", name)
        return None
    items = 0
    for index in range(1, tracks + 1):
        if only_enabled:
            enabled = timeline.GetIsTrackEnabled("audio", index)
            if enabled is None:
                log.info("Not checking %r for audio: audio track %d did not report on", name, index)
                return None
            if not enabled:
                continue
        listed = timeline.GetItemListInTrack("audio", index)
        if listed is None:
            log.info("Not checking %r for audio: audio track %d did not read", name, index)
            return None
        items += len(listed)
    return _AudioItems(tracks, items)


def _as_export_failure(exc: RenderQueueError) -> AudioExportError:
    """Re-label a queue failure as an audio one, keeping any fix the queue was specific about.

    The generic queue advice is worth replacing with "check the timeline has audio on it".
    Advice the queue raised for a specific case — a wedged render engine, a modal dialog
    stalling the render — is not: those are the more actionable of the two, so they survive
    the relabelling.
    """
    specific = exc.fix if exc.fix != RenderQueueError.default_fix else None
    return AudioExportError(cause=exc.cause, fix=specific, detail=exc.detail)


# --- clip scope: ffmpeg ------------------------------------------------------------------


def acquire_clip_audio(
    connection: ResolveConnection,
    clip: str,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job that extracts one source clip's audio. Returns the job record.

    ``runner`` is the subprocess seam ``ffmpeg.extract`` documents: the default shells out
    for real, and a caller can hand in its own to exercise the route without ffmpeg.
    """
    config = config or get_config()
    located, source = _locate_clip(connection, clip, bin)
    params = _clip_params(clip, located.bin_path, sample_rate, bit_depth)
    key = cache.cache_key(CLIP_KIND, [cache.fingerprint(source)], params)

    def work(progress: Progress) -> JobOutput:
        return extract_clip_audio(source, key, params, progress, config, runner)

    return start_job(CLIP_KIND, params, work, cache_key=key, refresh=refresh, config=config)


def extract_clip_audio(
    source: str,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    config: Config | None = None,
    runner: Runner | None = None,
) -> JobOutput:
    """The worker: ffmpeg the clip's audio into the cache as a WAV."""
    config = config or get_config()
    target = config.audio_dir / keyed_name(str(params["clip"]), key, ".wav", "audio")

    progress(0.1, "extracting audio with ffmpeg")
    ffmpeg.extract(
        source,
        target,
        sample_rate=int(params["sample_rate"]),
        bit_depth=int(params["bit_depth"]),
        runner=runner,
        config=config,
    )

    progress(0.9, "hashing the extraction")
    return JobOutput(_result(target, params), (target,))


def mapping_conflict(mapping: dict[str, Any] | None, file_path: str) -> str | None:
    """Why this clip's audio cannot be read from its own file, or ``None`` if it can.

    ``GetAudioMapping`` returns JSON whose shape Blackmagic does not document and has
    changed between versions, so this reads it structurally rather than by key: any path
    that is not the clip's own file, and any non-zero offset, mean Resolve is doing
    something to this audio that ffmpeg on the source file would not reproduce. Erring
    toward refusing costs a slower export; erring the other way costs analysis of audio
    that is not the audio in the cut.
    """
    if not mapping:
        return None
    own = _normal(file_path) if file_path else ""
    for path, value in _walk(mapping):
        name = path[-1].lower() if path else ""
        if isinstance(value, str) and _looks_like_a_path(value) and own and _normal(value) != own:
            return f"its audio mapping points at {value}."
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and value
            and any(hint in name for hint in OFFSET_HINT)
        ):
            return f"its audio mapping carries a {name} of {value}."
    return None


def _normal(path: str) -> str:
    """One spelling for a path, so D:\\media\\a.wav and D:/media/a.wav are the same file.

    Windows rules regardless of the host: Resolve runs on Windows, but the fake tier runs
    on ubuntu in CI, where ``os.path`` would leave the two spellings above unequal and this
    comparison would mean something different there than in production.
    """
    return str(PureWindowsPath(path)).lower()


def _walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, (*path, str(key)))
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, path)
    else:
        yield path, value


def _looks_like_a_path(value: str) -> bool:
    return ("/" in value or "\\" in value) and PureWindowsPath(value).suffix != ""


# --- what an analysis job asks for ---------------------------------------------------------


class Source(NamedTuple):
    """A scope resolved to what identifies it, and to a way of acquiring its audio.

    Every job that runs *on* audio — stems, beats, transcript — has the same problem: its
    cache key has to be computable in the starter, before the audio exists, or a rerun
    would re-export a whole concert only to discover it had the answer already. So the
    identity is read here, up front and cheaply, while the acquisition itself is deferred
    into ``start`` to run inside the calling job's own thread. That keeps the tool call as
    short as one Resolve read and leaves the export where the agent can watch it.
    """

    fingerprint: dict[str, Any]
    params: dict[str, Any]
    start: Callable[[], dict[str, Any]]


def audio_source(
    connection: ResolveConnection,
    scope: str = TIMELINE_SCOPE,
    timeline: str | None = None,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> Source:
    """Resolve a scope now; hand back the fingerprint, the params, and how to acquire it.

    The refusals a route makes before it starts — offline media, audio Resolve has linked
    away — happen here too, so an analysis tool declines for the same reason and with the
    same advice the acquisition tool would have given, rather than failing a job later.
    """
    config = config or get_config()
    if scope == TIMELINE_SCOPE:
        project = current_project(connection, "No project is open, so there is no timeline.")
        found = find_timeline(project, timeline)
        name = str(found.GetName() or "timeline")
        refuse_a_timeline_with_no_audio(found, name)
        return Source(
            fingerprint(Reader(connection), found),
            _timeline_params(name, sample_rate, bit_depth),
            lambda: acquire_timeline_audio(
                connection,
                timeline=name,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                refresh=refresh,
                config=config,
            ),
        )

    if scope == CLIP_SCOPE:
        if not clip:
            raise InvalidRequestError(
                cause="A clip scope needs a clip to read.",
                fix="Name the clip, or use scope=timeline for the timeline mix.",
                detail={"scope": scope},
            )
        located, source = _locate_clip(connection, clip, bin)
        return Source(
            cache.fingerprint(source),
            _clip_params(clip, located.bin_path, sample_rate, bit_depth),
            lambda: acquire_clip_audio(
                connection,
                clip,
                bin=bin,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                refresh=refresh,
                runner=runner,
                config=config,
            ),
        )

    raise InvalidRequestError(
        cause=f"{scope!r} is not an audio scope.",
        fix=f"Use one of {', '.join(SCOPES)}: timeline for the mix, clip for one source file.",
        detail={"requested": scope, "scopes": list(SCOPES)},
    )


def _locate_clip(
    connection: ResolveConnection,
    clip: str,
    bin: str | None,  # noqa: A002 - "bin" is the Resolve term the agent uses
) -> tuple[media.LocatedClip, str]:
    """Find the clip and prove its audio is readable off its own file, or refuse."""
    pool = media.media_pool(connection)
    located = media.find_clip(pool, clip, bin)
    reported = media.properties(located.clip)
    source = reported.get(media.FILE_PATH, "")
    if not source or media.is_offline(source):
        raise AudioExtractionError(
            cause=f"{clip!r} has no readable file on disk.",
            fix="relink_media points a clip back at its media; list_media shows what is offline.",
            detail={"clip": clip, "file_path": source},
        )

    conflict = mapping_conflict(media.audio_mapping(located.clip), source)
    if conflict is not None:
        raise AudioMappingError(
            cause=f"{clip!r} does not carry its own audio: {conflict}",
            detail={"clip": clip, "file_path": source},
        )
    return located, source


def _timeline_params(name: str, sample_rate: int, bit_depth: int) -> dict[str, Any]:
    return {
        "scope": TIMELINE_SCOPE,
        "timeline": name,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }


def _clip_params(clip: str, bin_path: str, sample_rate: int, bit_depth: int) -> dict[str, Any]:
    return {
        "scope": CLIP_SCOPE,
        "clip": clip,
        "bin": bin_path,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }


# --- shared ------------------------------------------------------------------------------


def _result(target: Path, params: dict[str, Any]) -> dict[str, Any]:
    """What the agent — and every analysis job after it — reads off a finished acquisition."""
    reading = wav.describe(target)
    reading["content_sha256"] = cache.content_hash(target)
    reading["scope"] = params["scope"]
    log.info(
        "Acquired %.1fs of audio for %s scope at %s",
        reading.get("duration_seconds") or 0.0,
        params["scope"],
        target,
    )
    return reading
