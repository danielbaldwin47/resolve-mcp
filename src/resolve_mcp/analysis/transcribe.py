"""The transcription job: acquire the audio, read it, write the transcript.

It does not acquire the audio itself. It *chains onto* the acquisition job, and that is the
load-bearing decision here:

* The agent never sequences two calls. One tool, one job id, one poll — the audio route
  (render queue for a timeline, ffmpeg for a clip) is an implementation detail of this job,
  which is what ``tools.jobs`` means by "acquisition is internal to those starters".

* **The cache key is the acquisition's key plus the transcription parameters.** The audio's
  content hash would be the truer input, but it does not exist until the export has run, and
  a starter has to return before that. The acquisition key already covers everything that
  decides those bytes — the clip's fingerprint or the timeline's, sample rate, bit depth —
  so keying off it says the same thing early enough to be useful. It also means a rerun with
  a different model pays for the model and not for the export again: that acquisition is
  itself a cache hit.

* A failure below is reported with the *lower* job's advice. "The audio was not acquired" is
  not something an agent can act on; "check the render queue for a stuck job" is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audio import ffmpeg
from ..audio.acquire import acquire_clip_audio, acquire_timeline_audio
from ..config import Config, get_config
from ..errors import InternalError, InvalidRequestError, TranscriptionError
from ..jobs import cache, store
from ..jobs.runner import JobOutput, Progress, start_job, wait_for
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from . import silence, transcript, whisper
from .transcript import Transcriber

log = get_logger("analysis")

KIND = "transcribe_audio"

DEFAULT_LOW_CONFIDENCE = 0.5
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
    low_confidence: float = DEFAULT_LOW_CONFIDENCE,
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
    key = cache.cache_key(KIND, [{"audio": _key_of(acquisition)}], params)

    def work(progress: Progress) -> JobOutput:
        return transcribe_acquired(
            acquisition["job_id"], key, params, progress, transcriber, config
        )

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def transcribe_acquired(
    acquisition_job: str,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    transcriber: Transcriber | None = None,
    config: Config | None = None,
) -> JobOutput:
    """The worker: wait for the audio, transcribe it, measure it, write the document."""
    config = config or get_config()

    progress(ACQUIRING, "waiting for the audio")
    audio = _acquired(acquisition_job, config)

    progress(READING, "transcribing")
    source = Path(str(audio["path"]))
    heard = (transcriber or whisper.transcribe)(source, params)

    progress(MEASURING, "measuring the silence")
    spans = silence.silence(
        source,
        threshold_db=float(params["silence_threshold_db"]),
        min_seconds=float(params["min_silence_seconds"]),
    )

    progress(WRITING, "writing the transcript")
    document = transcript.document(
        audio=audio,
        params=params,
        words=heard.words,
        silence=spans,
        language=heard.language,
    )
    target = transcript.write(_target(params, key, config), document)

    log.info(
        "Transcribed %s: %d words, %d silence spans, %d unsure regions -> %s",
        _label(params),
        len(heard.words),
        len(spans),
        document["stats"]["low_confidence_regions"],
        target,
    )
    return JobOutput(transcript.gist(document, target), (target,))


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


def _key_of(acquisition: dict[str, Any]) -> str:
    """The acquisition's cache key, which this job's own key is derived from.

    An acquisition with no key would make every transcript on this machine collide on one
    entry and serve one concert's words for another, so it is a failure, not a fallback.
    """
    key = acquisition.get("cache_key")
    if not key:
        raise InternalError(
            cause="The audio acquisition job was registered without a cache key.",
            detail={"acquisition_job_id": acquisition.get("job_id")},
        )
    return str(key)


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
        fix=failure.get("fix"),
        detail={"acquisition": record.payload()},
    )


def _target(params: dict[str, Any], key: str, config: Config) -> Path:
    """Deterministic from the cache key, so a rerun overwrites instead of accumulating."""
    return config.analysis_dir / f"{slug(_label(params), 'transcript')}-{key[:12]}.transcript.json"


def _label(params: dict[str, Any]) -> str:
    return str(params.get("clip") or params.get("timeline") or "transcript")
