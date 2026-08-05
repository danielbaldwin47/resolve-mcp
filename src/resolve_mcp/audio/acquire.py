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

import contextlib
import hashlib
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
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from ..resolve import media, render
from ..resolve.connection import ResolveConnection
from ..resolve.session import current_project
from ..resolve.timeline import Reader, find_timeline
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
SINGLE_CLIP = 1

EXPORT_FLOOR = 0.1
EXPORT_CEILING = 0.9

OFFSET_HINT = ("offset", "delay")


# --- timeline scope: the render queue ----------------------------------------------------


def timeline_fingerprint(reader: Reader, timeline: Timeline) -> dict[str, Any]:
    """A timeline's identity, as far as anything outside Resolve can read it.

    Bounds and track counts alone would call a take swap or a reordered cut "unchanged" —
    same duration, same stack — and hand back yesterday's mix, so the shots themselves are
    digested too. What no reading can see is a clip's audio level, which the scripting API
    does not expose at all; that is what ``refresh`` on the starter is for.

    Nothing here smooths a failure into a default: a field that cannot be read while
    Resolve is dying must not quietly produce a fingerprint, because every dead-handle
    reading would collide on one key and serve one concert's audio for another.
    """
    return {
        "name": str(timeline.GetName()),
        "unique_id": reader.optional(timeline, "GetUniqueId", None),
        "start": timeline.GetStartFrame(),
        "end": timeline.GetEndFrame(),
        "audio_tracks": timeline.GetTrackCount("audio"),
        "video_tracks": timeline.GetTrackCount("video"),
        "structure": _structure(reader, timeline),
    }


def _structure(reader: Reader, timeline: Timeline) -> str:
    """A digest of every shot on the cut: what it is, where it starts, how long it runs."""
    digest = hashlib.sha256()
    for track_type in ("video", "audio"):
        count = int(timeline.GetTrackCount(track_type) or 0)
        for index in range(1, count + 1):
            items = reader.optional(timeline, "GetItemListInTrack", [], track_type, index) or []
            digest.update(f"{track_type}{index}:".encode())
            for item in items:
                name = reader.optional(item, "GetName", "")
                start = reader.optional(item, "GetStart", None)
                duration = reader.optional(item, "GetDuration", None)
                digest.update(f"{name}@{start}+{duration};".encode())
    return digest.hexdigest()


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
    params = _timeline_params(name, sample_rate, bit_depth)
    fingerprint = timeline_fingerprint(Reader(connection), found)
    key = cache.cache_key(TIMELINE_KIND, [fingerprint], params)

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
    target_dir = config.audio_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = _stem(str(params["timeline"]), key)
    expecting = target_dir / f"{stem}.wav"

    progress(0.05, "queuing the audio export")
    with _current_timeline(project, timeline):
        # One file for the whole timeline. The other mode renders a file per clip on it,
        # which for a concert cut is hundreds of fragments instead of the mix.
        project.SetCurrentRenderMode(SINGLE_CLIP)
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
            job_id = render.submit(project, settings, RENDER_FORMAT, RENDER_CODEC)
            render.render(project, job_id, expecting, _scaled(progress))
        except RenderQueueError as exc:
            raise _as_export_failure(exc) from exc

    progress(0.95, "hashing the export")
    return JobOutput(_result(expecting, params), (expecting,))


def _as_export_failure(exc: RenderQueueError) -> AudioExportError:
    """Re-label a queue failure as an audio one, keeping any fix the queue was specific about.

    The generic queue advice is worth replacing with "check the timeline has audio on it".
    Advice the queue raised for one case — a modal dialog stalling the render — is not:
    that is the more actionable of the two, so it survives the relabelling.
    """
    specific = exc.fix if exc.fix != RenderQueueError.default_fix else None
    return AudioExportError(cause=exc.cause, fix=specific, detail=exc.detail)


@contextlib.contextmanager
def _current_timeline(project: Project, timeline: Timeline) -> Iterator[None]:
    """Render what was asked for, and put the director's timeline back afterwards.

    The render queue renders the *current* timeline, so acquiring audio for any other one
    means switching. Leaving the switch in place would move the GUI out from under whoever
    is sitting at it.
    """
    previous = project.GetCurrentTimeline()
    switched = previous is not timeline
    if switched:
        project.SetCurrentTimeline(timeline)
    try:
        yield
    finally:
        if switched and previous is not None:
            project.SetCurrentTimeline(previous)


def _scaled(progress: Progress) -> Progress:
    """Map the render's own 0-1 onto the part of the job the render actually is."""
    span = EXPORT_CEILING - EXPORT_FLOOR

    def scaled(fraction: float, step: str) -> None:
        progress(EXPORT_FLOOR + span * fraction, step)

    return scaled


# --- clip scope: ffmpeg ------------------------------------------------------------------


def acquire_clip_audio(
    connection: ResolveConnection,
    clip: str,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    bit_depth: int = DEFAULT_BIT_DEPTH,
    refresh: bool = False,
    runner: ffmpeg.Runner | None = None,
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
    runner: ffmpeg.Runner | None = None,
) -> JobOutput:
    """The worker: ffmpeg the clip's audio into the cache as a WAV."""
    config = config or get_config()
    target = config.audio_dir / f"{_stem(str(params['clip']), key)}.wav"

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
    runner: ffmpeg.Runner | None = None,
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
        return Source(
            timeline_fingerprint(Reader(connection), found),
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


def _stem(label: str, key: str) -> str:
    """Deterministic from the cache key, so a rerun overwrites instead of accumulating."""
    return f"{slug(label, 'audio')}-{key[:12]}"
