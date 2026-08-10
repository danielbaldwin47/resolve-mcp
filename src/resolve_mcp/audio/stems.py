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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import InternalError
from ..ffmpeg import Runner
from ..jobs import cache
from ..jobs import runner as job_runner
from ..jobs.runner import JobOutput, Progress, start_job
from ..jobs.store import JobRecord
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

WIND_KEYS = {"woodwinds": "wind", "no woodwinds": "comp"}
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
PASS_TWO_CEILING = 0.8
SEPARATED = 0.9
"""Where the last pass ends, whichever pass that is.

The optional third pass splits the back half rather than extending it: with it off pass two
runs to ``SEPARATED``, with it on pass two stops at ``PASS_TWO_CEILING`` and pass three
carries on to ``SEPARATED``. A bar that ended somewhere different depending on a flag would
have the cheaper job look unfinished at the point the expensive one is done.
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
    runner: separator.Runner | None = None,
    ffmpeg_runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a separation job. Returns the job record, not the stems.

    ``split_wind`` adds the third pass over ``other``. It is a job param but not a stems-key
    param, so asking for it on audio already separated runs that pass alone.

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
    params = {**source.params, **separation_params(config), "split_wind": split_wind}
    key = cache.cache_key(KIND, [source.fingerprint], params)

    def work(progress: Progress) -> JobOutput:
        audio = acquired(source, progress, config)
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
    wind_dir = directory / OTHER_PASS
    drums_ceiling = PASS_TWO_CEILING if split_wind else SEPARATED

    reused = reuse and _already_separated(mix_dir, drums_dir, wind_dir if split_wind else None)
    if reused:
        log.info("Stems for %s are already on disk at %s", audio["path"], directory)
        stems = separator.collect(mix_dir)
        drums = separator.collect(drums_dir)
        wind = separator.collect(wind_dir) if split_wind else {}
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
            progress=_pass(progress, PASS_ONE_CEILING, drums_ceiling, "decomposing the drums"),
            runner=runner,
            config=config,
        )
        wind = {}
        if split_wind:
            progress(PASS_TWO_CEILING, "splitting the winds out of the other stem")
            wind = separator.separate(
                stems[OTHER_SOURCE],
                wind_dir,
                config.wind_model,
                WIND_STEMS,
                progress=_pass(progress, PASS_TWO_CEILING, SEPARATED, "splitting the winds"),
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
        result[OTHER_PASS] = {WIND_KEYS.get(name, name): str(path) for name, path in wind.items()}
        result["models"][OTHER_PASS] = config.wind_model
    artifacts = tuple([*stems.values(), *drums.values(), *wind.values()])
    log.info("Separated %s into %d stems at %s", audio["path"], len(artifacts), directory)
    return JobOutput(result, artifacts)


def _already_separated(mix_dir: Path, drums_dir: Path, wind_dir: Path | None = None) -> bool:
    """Every pass this run was asked for is on disk in full — a partial run is worth redoing.

    ``wind_dir`` is ``None`` when the third pass is off, and then a two-pass directory is
    complete rather than partial. The flag is not part of the stems key, so both shapes share
    a directory: what completeness means has to come from what was asked for, or a job with
    the pass off would rerun everything to fill a directory it does not want.
    """
    if separator.missing_from(mix_dir, FOUR_STEMS) or separator.missing_from(drums_dir, DRUM_STEMS):
        return False
    return wind_dir is None or not separator.missing_from(wind_dir, WIND_STEMS)


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
