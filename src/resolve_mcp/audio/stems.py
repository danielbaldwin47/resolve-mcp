"""Two passes, because fill detection needs near-clean drums.

Pass one splits the mix into four stems; pass two takes the drum stem alone and decomposes
it into kick, snare and toms. Running the drum model on the full mix is what the second
pass exists to avoid — it has bass and vocals bleeding into every hit, and a fill detector
reading that is reading the band, not the drummer.

Three decisions worth knowing:

* **The job's key and the stems' key are not the same key, on purpose.** The job is keyed
  the way its acquisition is keyed — a timeline fingerprint, a clip's path/size/mtime —
  because that is all that can be known in the starter, before any audio exists. The stems
  on disk are keyed by the *content hash* of the audio that produced them plus the params
  (#22: "workers key off content hash of cached audio + params hash"), so audio that comes
  back byte-identical under a moved fingerprint costs nothing: the directory is already
  there and both passes are skipped. ``refresh`` overrides both.

* **Acquisition runs inside this job.** #12 puts the export inside the starter rather than
  making the agent sequence one, so the stems job starts the acquisition job and follows
  it, mapping its progress into the first quarter of its own. Its failure arrives as this
  job's failure with the acquisition's own cause and fix (see ``ChainedJobError``) —
  a render queue problem reads as a render queue problem either way.

* **Each pass writes into its own directory.** Pass two reads the drum stem pass one
  wrote; sharing a directory would have the decomposition land beside — and, with a model
  that labels its output ``(Drums)``, on top of — the file it is reading.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import ChainedJobError, InternalError
from ..jobs import cache, store
from ..jobs import runner as job_runner
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from . import acquire, ffmpeg, separator

log = get_logger("audio")

KIND = "separate_stems"

FOUR_STEMS = ("vocals", "drums", "bass", "other")
DRUM_STEMS = ("kick", "snare", "toms")
DRUM_SOURCE = "drums"
"""Which of the four stems the second pass decomposes."""

MIX_PASS = "mix"
DRUM_PASS = "drums"

ACQUIRE_FLOOR = 0.02
ACQUIRE_CEILING = 0.25
PASS_ONE_CEILING = 0.6
PASS_TWO_CEILING = 0.9
COLLECTING = 0.95
POLL_SECONDS = 0.1

PERCENT = 100


def separate_stems(
    connection: ResolveConnection,
    scope: str = acquire.TIMELINE_SCOPE,
    timeline: str | None = None,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    refresh: bool = False,
    runner: separator.Runner | None = None,
    ffmpeg_runner: ffmpeg.Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a two-pass separation job. Returns the job record, not the stems.

    ``runner`` and ``ffmpeg_runner`` are the two subprocess seams: the default shells out
    for real, and a caller can hand in its own to exercise the route without the models or
    ffmpeg installed.
    """
    config = config or get_config()
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
    params = {
        **source.params,
        "model": config.stem_model,
        "drum_model": config.drum_model,
        "stems": list(FOUR_STEMS),
        "drum_stems": list(DRUM_STEMS),
    }
    key = cache.cache_key(KIND, [source.fingerprint], params)

    def work(progress: Progress) -> JobOutput:
        audio = acquired(source, progress, config)
        return two_pass(audio, params, progress, runner=runner, reuse=not refresh, config=config)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def acquired(
    source: acquire.Source,
    progress: Progress,
    config: Config | None = None,
    poll: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the acquisition this separation needs, and report it as this job's first quarter.

    A cache hit comes back completed from the starter and is never waited on at all; a real
    export is followed record by record, because for a concert that render is minutes of
    the job and a poller that sees nothing move assumes a hang.
    """
    config = config or get_config()
    progress(ACQUIRE_FLOOR, "acquiring the audio")
    record = source.start()
    span = ACQUIRE_CEILING - ACQUIRE_FLOOR

    while record["state"] == store.RUNNING:
        fraction = float(record.get("progress") or 0.0)
        progress(ACQUIRE_FLOOR + span * fraction, str(record.get("step") or "acquiring the audio"))
        sleep(poll)
        job_id = str(record["job_id"])
        record = store.load(job_id, config).payload()
        if record["state"] == store.RUNNING and not job_runner.alive(job_id):
            raise InternalError(
                cause=f"The acquisition {job_id} stopped without finishing its record.",
                detail={"job_id": job_id, "step": record.get("step")},
            )

    if record["state"] == store.FAILED:
        raise ChainedJobError(record.get("error") or {}, str(record["job_id"]))

    result = record.get("result")
    if not isinstance(result, dict):
        raise InternalError(cause=f"The acquisition {record['job_id']} finished with no audio.")
    return result


def two_pass(
    audio: dict[str, Any],
    params: dict[str, Any],
    progress: Progress,
    runner: separator.Runner | None = None,
    reuse: bool = True,
    config: Config | None = None,
) -> JobOutput:
    """The worker: four stems, then the drum stem decomposed into kick, snare and toms."""
    config = config or get_config()
    key = cache.cache_key(KIND, [{"content_sha256": audio["content_sha256"]}], params)
    directory = config.stems_dir / f"{slug(Path(str(audio['path'])).stem, 'stems')}-{key[:12]}"
    mix_dir = directory / MIX_PASS
    drums_dir = directory / DRUM_PASS

    reused = reuse and _already_separated(mix_dir, drums_dir)
    if reused:
        log.info("Stems for %s are already on disk at %s", audio["path"], directory)
        stems = separator.collect(mix_dir)
        drums = separator.collect(drums_dir)
    else:
        stems = separator.separate(
            audio["path"],
            mix_dir,
            config.stem_model,
            FOUR_STEMS,
            progress=_pass(progress, ACQUIRE_CEILING, PASS_ONE_CEILING, "separating four stems"),
            runner=runner,
            config=config,
        )
        progress(PASS_ONE_CEILING, "decomposing the drum stem")
        drums = separator.separate(
            stems[DRUM_SOURCE],
            drums_dir,
            config.drum_model,
            DRUM_STEMS,
            progress=_pass(progress, PASS_ONE_CEILING, PASS_TWO_CEILING, "decomposing the drums"),
            runner=runner,
            config=config,
        )

    progress(COLLECTING, "collecting the stems")
    result = {
        "key": key,
        "directory": str(directory),
        "stems": {name: str(path) for name, path in stems.items()},
        "drums": {name: str(path) for name, path in drums.items()},
        "models": {"stems": config.stem_model, "drums": config.drum_model},
        "audio": audio,
        "reused": reused,
    }
    artifacts = tuple([*stems.values(), *drums.values()])
    log.info("Separated %s into %d stems at %s", audio["path"], len(artifacts), directory)
    return JobOutput(result, artifacts)


def _already_separated(mix_dir: Path, drums_dir: Path) -> bool:
    """Both passes are on disk in full — a partial run is worth redoing, not patching."""
    return not separator.missing_from(mix_dir, FOUR_STEMS) and not separator.missing_from(
        drums_dir, DRUM_STEMS
    )


def _pass(progress: Progress, floor: float, ceiling: float, step: str) -> separator.Fraction:
    """Map one pass's own 0-1 onto the slice of the job that pass is."""
    span = ceiling - floor

    def report(fraction: float) -> None:
        progress(floor + span * fraction, f"{step} ({int(fraction * PERCENT)}%)")

    return report
