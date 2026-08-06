"""The transcription job: acquire the audio, read it, write the transcript.

It does not acquire the audio itself. It *chains onto* the acquisition job, and that is the
load-bearing decision here:

* The agent never sequences two calls. One tool, one job id, one poll — the audio route
  (render queue for a timeline, ffmpeg for a clip) is an implementation detail of this job,
  which is what ``tools.jobs`` means by "acquisition is internal to those starters".

* **The cache key is the acquired audio's content hash plus the transcription parameters**
  (#22: "workers key off content hash of cached audio + params hash"). Nothing weaker holds:
  a timeline's fingerprint is blind to a clip's audio level by its own admission, so a
  ``refresh`` by any other job can put different bytes behind the same acquisition key, and
  a transcript keyed off that key would answer with the previous mix's words.

  The hash exists only once the audio does, which splits the job in two. When the
  acquisition came back already cached — the rerun case, the one the cache is for — the hash
  is in hand before the starter returns, so the transcript answers from cache with no thread
  at all. When the audio had to be made, there is nothing to hit anyway, and the worker
  writes the entry itself once it knows what it transcribed.

* A failure below is reported with the *lower* job's advice. "The audio was not acquired" is
  not something an agent can act on; "check the render queue for a stuck job" is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import ffmpeg
from ..audio.acquire import acquire_clip_audio, acquire_timeline_audio
from ..config import Config, get_config
from ..errors import InvalidRequestError, TranscriptionError
from ..jobs import cache, store
from ..jobs.runner import JobOutput, Progress, start_job, wait_for
from ..logging_config import get_logger
from ..naming import keyed_name
from ..resolve.connection import ResolveConnection
from . import silence, transcript, whisper
from .transcript import Transcriber

log = get_logger("analysis")

KIND = "transcribe_audio"

ACQUIRE_TIMEOUT = 3600.0

ACQUIRING = 0.05
READING = 0.2
MEASURING = 0.85
WRITING = 0.95


def transcribe_audio(
    connection: ResolveConnection,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    timeline: str | None = None,
    model: str = whisper.DEFAULT_MODEL,
    language: str | None = None,
    low_confidence: float = transcript.DEFAULT_LOW_CONFIDENCE,
    silence_threshold_db: float = silence.DEFAULT_THRESHOLD_DB,
    min_silence_seconds: float = silence.DEFAULT_MIN_SECONDS,
    refresh: bool = False,
    transcriber: Transcriber | None = None,
    runner: ffmpeg.Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job that transcribes one clip or the timeline mix. Returns the job record.

    ``transcriber`` and ``runner`` are the two seams: the model and the ffmpeg subprocess.
    The defaults do the real thing; a caller hands in its own to exercise the route.
    """
    config = config or get_config()
    _refuse_an_ambiguous_scope(clip, bin, timeline)

    if clip is not None:
        acquisition = acquire_clip_audio(
            connection, clip, bin, refresh=refresh, runner=runner, config=config
        )
    else:
        acquisition = acquire_timeline_audio(connection, timeline, refresh=refresh, config=config)

    acquired = dict(acquisition["params"])
    params = {
        "scope": acquired["scope"],
        "clip": acquired.get("clip"),
        "bin": acquired.get("bin"),
        "timeline": acquired.get("timeline"),
        "model": model,
        "language": language,
        "low_confidence": low_confidence,
        "silence_threshold_db": silence_threshold_db,
        "min_silence_seconds": min_silence_seconds,
    }
    known = _hash_of(acquisition)
    key = cache_key(known, params) if known is not None else None

    def work(progress: Progress) -> JobOutput:
        return transcribe_acquired(
            acquisition["job_id"], params, progress, key, transcriber, config
        )

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def cache_key(content_sha256: str, params: dict[str, Any]) -> str:
    """What one transcript is: these bytes of audio, read with these parameters."""
    return cache.cache_key(KIND, [{"content_sha256": content_sha256}], params)


def transcribe_acquired(
    acquisition_job: str,
    params: dict[str, Any],
    progress: Progress,
    known_key: str | None = None,
    transcriber: Transcriber | None = None,
    config: Config | None = None,
) -> JobOutput:
    """The worker: wait for the audio, transcribe it, measure it, write the document.

    ``known_key`` is the key the starter already had, which it only has when the audio was
    already in the cache. ``None`` means this worker is the first to know what it is
    transcribing, so writing the cache entry is its job — inside the worker, where a write
    that fails fails the job rather than leaving a result nothing can find again.
    """
    config = config or get_config()

    progress(ACQUIRING, "waiting for the audio")
    audio = _acquired(acquisition_job, config)
    key = known_key or cache_key(str(audio["content_sha256"]), params)

    progress(READING, "transcribing")
    source = Path(str(audio["path"]))
    heard = (transcriber or whisper.transcribe)(source, params)

    progress(MEASURING, "measuring the silence")
    quiet = silence.measure(
        source,
        threshold_db=float(params["silence_threshold_db"]),
        min_seconds=float(params["min_silence_seconds"]),
    )

    progress(WRITING, "writing the transcript")
    document = transcript.document(
        audio=audio,
        params=params,
        words=heard.words,
        silence=quiet,
        language=heard.language,
    )
    target = transcript.write(_target(params, key, config), document)

    log.info(
        "Transcribed %s: %d words, %d silence spans, %d unsure regions -> %s",
        _label(params),
        len(heard.words),
        len(quiet),
        document["stats"]["low_confidence_regions"],
        target,
    )
    output = JobOutput(transcript.gist(document, target), (target,))
    if known_key is None:
        cache.remember(key, KIND, output.result, output.artifacts, config)
    return output


def _refuse_an_ambiguous_scope(clip: str | None, bin: str | None, timeline: str | None) -> None:  # noqa: A002
    """One scope per transcript. Guessing which one was meant transcribes the wrong audio."""
    if clip is not None and timeline is not None:
        raise InvalidRequestError(
            cause="A transcript is of one clip or of one timeline, not of both.",
            fix="Pass clip for a source file, or timeline (or neither, for the open one).",
            detail={"clip": clip, "timeline": timeline},
        )
    if bin is not None and clip is None:
        raise InvalidRequestError(
            cause="bin narrows which clip is meant, and no clip was named.",
            fix="Pass clip as well, or drop bin to transcribe the timeline mix.",
            detail={"bin": bin},
        )


def _hash_of(acquisition: dict[str, Any]) -> str | None:
    """The acquired audio's content hash, when the acquisition already has one.

    It does when that job answered from its own cache, which is exactly the rerun this
    job's cache exists for. Otherwise the audio is still being made and there is no hash to
    key on yet — and nothing to hit either, so waiting for one would buy nothing.
    """
    if acquisition.get("state") != store.COMPLETED:
        return None
    result = acquisition.get("result") or {}
    return str(result["content_sha256"]) if result.get("content_sha256") else None


def _acquired(job_id: str, config: Config) -> dict[str, Any]:
    """Block until the audio is on disk, or fail carrying the acquisition's own advice."""
    record = wait_for(job_id, timeout=ACQUIRE_TIMEOUT, config=config)
    if record.state == store.COMPLETED and record.result is not None:
        return dict(record.result)

    failure = record.error or {}
    raise TranscriptionError(
        cause=(
            "The audio to transcribe was not acquired: "
            f"{failure.get('cause') or f'its job is still {record.state}.'}"
        ),
        fix=failure.get("fix") or _still_going(record.state),
        detail={"acquisition": record.payload()},
    )


def _still_going(state: str) -> str | None:
    """A running acquisition is not a failure to fix — it is one to wait out and re-ask."""
    if state != store.RUNNING:
        return None
    return (
        "The audio export is still running after an hour. Poll it with get_job, and start "
        "the transcript again once it has finished — the audio will be a cache hit by then."
    )


def _target(params: dict[str, Any], key: str, config: Config) -> Path:
    return config.analysis_dir / keyed_name(_label(params), key, ".transcript.json", "transcript")


def _label(params: dict[str, Any]) -> str:
    return str(params.get("clip") or params.get("timeline") or "transcript")
