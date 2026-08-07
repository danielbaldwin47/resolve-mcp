"""Measuring a cut against the music it was cut to.

This is the instrument style is learned with (#22, story 32) and the one a build is reviewed
with (story 47). Both directions need the same numbers: for every shot, how far its start
sits from the nearest beat and from the nearest transient, where in the bar that is, which
tune it happens in, who was out front, how long the shot runs and which angle it came from.

Two rules shape everything here.

*Nothing is decided.* An offset of two frames late is reported as two frames late, never as
"good" or "loose": what counts as musical is the style profile's business, and that is a
Markdown document Claude writes, never server code (#21). The same goes for the roles: the
angle sidecar is the agent's own document and no server path touches it (#45), so the labels
arrive here as a mapping the caller already lifted out of it.

*Measurement, not analysis.* The beat grid, the tunes and the solo changes are files another
job already wrote; this joins them to the shots. The one thing it computes is the transient
list, because onsets are the fourth-wall risk (#21: transients, not the beat grid) and no
job persists them. That decode is why this is a job at all, and why the detector is
injectable per ADR 0002 — the arithmetic is testable without a model or a concert.

The cut's clock is not the timeline's. Analysis times count from the start of the master
mix, so a shot's time is read through whatever the audio track says about where that mix
sits — and when a timeline has no audio to say, the reading falls back to counting from the
timeline's own first frame and *says which it did*, because a whole file of times measured
against the wrong zero looks exactly like a whole file of times measured against the right
one.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import InvalidRequestError
from ..jobs import cache
from ..jobs.runner import JobOutput, Progress, start_job
from ..logging_config import get_logger
from ..naming import keyed_name
from ..resolve.connection import ResolveConnection
from ..resolve.session import frame_rate
from ..resolve.timeline import (
    FIRST_TRACK,
    Reader,
    angle_name,
    clip_name,
    find_timeline,
    fingerprint,
    items_in_track,
    name_of,
    open_project,
    read_frames,
    source_bounds,
    start_frame,
)
from ..timing import SECONDS_PRECISION, dual_time
from . import decode, energy, records
from .beats import nearest

log = get_logger("analysis")

KIND = "correlate_timeline"

INLINE_CUTS = 12
"""How many records the gist carries: enough to see the shape of the cut, not the concert."""

UNLABELLED = "unlabelled"
"""Where shots from a clip the angle sidecar does not name are counted."""

Onsets = Callable[[Path], tuple[float, ...]]
"""The transient seam: a WAV in, onset times in seconds out."""


class Shot(NamedTuple):
    """One shot as the measurement needs it: what it is and where it sits."""

    clip: str
    record_in: int
    duration: int

    @property
    def record_out(self) -> int:
        return self.record_in + self.duration


class Clock(NamedTuple):
    """How a timeline frame becomes a second in the analysed mix."""

    zero_frame: int
    fps: float
    mode: str
    audio: str | None
    matched: bool

    def seconds(self, frame: int) -> float:
        """Where ``frame`` falls in the analysis, unrounded — the callers round."""
        return (frame - self.zero_frame) / self.fps

    def reading(self) -> dict[str, Any]:
        """How the times were arrived at — the first thing to check when they look wrong."""
        return {
            "mode": self.mode,
            "audio": self.audio,
            "matched": self.matched,
            "zero_frame": self.zero_frame,
        }


class Music(NamedTuple):
    """Everything read off disk: the grid, the transients' source, and the optional shape."""

    beats: tuple[dict[str, Any], ...]
    tunes: tuple[dict[str, Any], ...] | None
    solos: tuple[dict[str, Any], ...] | None
    roles: dict[str, str] | None
    audio: Path | None


def correlate_timeline(
    connection: ResolveConnection,
    beats: str,
    timeline: str | None = None,
    audio: str | None = None,
    tunes: str | None = None,
    solos: str | None = None,
    angles: Mapping[str, Any] | None = None,
    track: int = FIRST_TRACK,
    refresh: bool = False,
    onsets: Onsets | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job measuring a timeline against its music analysis. Returns the job record.

    Every input is read and checked here, before the job exists: a path that is not there
    and a file that is not what it claims are wrong *calls*, and a wrong call should come
    back from the tool rather than from a poll two seconds later.

    ``angles`` is a mapping the caller passes, not a file this reads: the angle sidecar is
    the agent's document (#45 — no server path reads or writes it), so the labels arrive
    already lifted out of it.

    ``onsets`` is the transient seam; the default decodes the audio for real.
    """
    config = config or get_config()
    roles = _roles(angles)
    music = _music(beats, audio, tunes, solos, roles)
    shots, clock, print_, name = _read_cut(connection, timeline, track, music.audio)

    params: dict[str, Any] = {
        "timeline": name,
        "track": int(track),
        "beats": _named(beats),
        "audio": _named(audio),
        "tunes": _named(tunes),
        "solos": _named(solos),
        "angles": dict(sorted(roles.items())) if roles is not None else None,
    }
    watched = [print_, *_fingerprints(beats, audio, tunes, solos)]
    key = cache.cache_key(KIND, watched, params)

    def work(progress: Progress) -> JobOutput:
        return correlate(shots, clock, name, music, key, params, progress, onsets, config)

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def correlate(
    shots: Sequence[Shot],
    clock: Clock,
    name: str,
    music: Music,
    key: str,
    params: dict[str, Any],
    progress: Progress,
    onsets: Onsets | None = None,
    config: Config | None = None,
) -> JobOutput:
    """The worker: transients, then one record per shot on disk and the stats inline."""
    config = config or get_config()

    progress(0.1, "reading the transients")
    transients = _transients(music.audio, onsets)

    progress(0.7, "measuring the cut against the music")
    rows = measure(shots, clock, music, transients)
    summary = _summary(rows, clock, transients, [float(one["t"]) for one in music.beats])

    target = config.analysis_dir / keyed_name(name, key, ".correlate.json", "correlate")
    # "count", not "cuts": the records themselves are the file's ``cuts`` field, and a header
    # key of the same name would be a second one that only the last reader of the two sees.
    header: dict[str, Any] = {
        "kind": KIND,
        "timeline": name,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "inputs": {named: value for named, value in params.items() if named != "timeline"},
        **{named: value for named, value in summary.items() if named != "cuts"},
        "count": summary["cuts"],
    }
    records.write(target, header, "cuts", rows)

    log.info("Measured %d shot(s) of %s against its music into %s", len(rows), name, target)
    return JobOutput({"path": str(target), **summary, "first_cuts": rows[:INLINE_CUTS]}, (target,))


# --- reading the cut --------------------------------------------------------------------------


def _read_cut(
    connection: ResolveConnection,
    name: str | None,
    track: int,
    mix: Path | None = None,
) -> tuple[list[Shot], Clock, dict[str, Any], str]:
    """Everything Resolve has to answer for, taken before the job starts.

    A job that reached back into Resolve would be measuring a timeline the director may
    have moved on from, and would need the Resolve lock to do it. One reading up front,
    then arithmetic — and a handle that dies during it fails the *call*, which is the one
    failure the tool layer's single reconnect can still fix.
    """
    project = open_project(connection)
    timeline = find_timeline(project, name)
    reader = Reader(connection)
    found = name_of(timeline)

    fps = frame_rate(project, timeline)
    if not fps:
        raise InvalidRequestError(
            cause=f"Resolve reported no frame rate for {found!r}, so no shot has a time.",
            fix="Open the timeline in Resolve and check its frame rate, then measure again.",
            detail={"timeline": found},
        )

    shots = _shots(reader, timeline, track)
    if not shots:
        raise InvalidRequestError(
            cause=f"Video track {track} of {found!r} holds no shots to measure.",
            fix=(
                "Name the timeline with timeline= and the track the cut sits on with track= "
                "— inspect_timeline shows which tracks hold what."
            ),
            detail={"timeline": found, "track": int(track)},
        )
    clock = _clock(reader, timeline, fps, mix)
    return shots, clock, fingerprint(reader, timeline), found


def _shots(reader: Reader, timeline: Any, track: int) -> list[Shot]:
    """The shots on one video track, in the order they play.

    A shot whose position will not read is dropped rather than guessed at: a measurement
    with an invented time in it is worse than one shot short, and the log says which.
    """
    found: list[Shot] = []
    for item in items_in_track(timeline, "video", int(track)):
        record_in = read_frames(item.GetStart())
        duration = read_frames(item.GetDuration())
        if record_in is None or duration is None:
            log.warning("Skipped a shot on video track %d: Resolve gave no position", track)
            continue
        name = angle_name(reader, item) or str(item.GetName() or "")
        found.append(Shot(clip=name, record_in=record_in, duration=duration))
    return _without_transitions(sorted(found, key=lambda shot: shot.record_in), track)


def _without_transitions(shots: Sequence[Shot], track: int) -> list[Shot]:
    """Drop the transitions Resolve hands back alongside the shots.

    ``GetItemListInTrack`` returns a dissolve as an item of its own, sitting across the
    boundary it softens: on a hand-edited concert that is a short item every time the
    editor did anything other than a straight cut, and one real corpus timeline came back
    159 of them in 525 items. Left in, each one is a shot that was never cut — its start is
    half a transition *before* the cut it belongs to, which lands in the offset
    distribution as a cut the editor did not make, and its length lands in the duration
    stats as a shot nobody held.

    Two items cannot overlap on one video track, so overlapping the shot before it is what
    a transition is and a shot is not. That is a fact about tracks rather than about any
    getter, which is why it is the test rather than the absence of a media pool item — a
    generator has none of those either, and a generator on the cut track is a shot.
    """
    kept: list[Shot] = []
    dropped = 0
    for shot in shots:
        if kept and shot.record_in < kept[-1].record_out:
            dropped += 1
            continue
        kept.append(shot)
    if dropped:
        log.info(
            "Video track %d: %d transitions read past, %d shots kept", track, dropped, len(kept)
        )
    return kept


def _clock(reader: Reader, timeline: Any, fps: float, mix: Path | None) -> Clock:
    """Where the analysed mix sits under the cut, read off the audio shot that holds it.

    The mix is one continuous clip under the whole cut (#22: the cutting substrate), so its
    record position and its own in point together say which second of the analysis any
    timeline frame is.

    *Which* audio clip is the question. On a cut this server built, A1 is the mix; on a
    hand-edited concert it is routinely camera scratch, and anchoring to that would shift
    every time in the file by however far apart the two recordings start. So when the caller
    named the audio, the clip carrying that file wins, and ``matched`` says whether the mix
    was recognised or merely assumed — which is the difference between a reading to trust
    and one to check before writing a style profile from it.
    """
    found = _audio_shots(reader, timeline)
    wanted = _matching(found, mix)
    chosen = wanted or (found[0] if found else None)
    if chosen is None:
        return Clock(start_frame(timeline), fps, "timeline_start", None, False)
    name, record_in, source_in = chosen
    return Clock(record_in - source_in, fps, "audio_clip", name, wanted is not None)


def _audio_shots(reader: Reader, timeline: Any) -> list[tuple[str, int, int]]:
    """Every audio shot that will say where it sits: its clip name, record in and mix in.

    The mix in is counted from the *start of the file*, not from the clip's own start
    timecode: a WAV stamped 01:00:00:00 reports source frames an hour in, and subtracting
    that stamp is the difference between a correct reading and one shifted by an hour.
    """
    count = int(read_frames(reader.optional(timeline, "GetTrackCount", 0, "audio")) or 0)
    found: list[tuple[str, int, int]] = []
    for index in range(1, count + 1):
        for item in items_in_track(timeline, "audio", index):
            record_in = read_frames(item.GetStart())
            source_in, _ = source_bounds(reader, item, read_frames(item.GetDuration()))
            if record_in is None or source_in is None:
                continue
            name = clip_name(reader, item) or str(item.GetName() or "")
            found.append((name, record_in, source_in - _media_start(reader, item)))
    return found


def _media_start(reader: Reader, item: Any) -> int:
    """The first frame of the media itself, which is not zero on anything with a start stamp."""
    clip = reader.optional(item, "GetMediaPoolItem", None)
    if clip is None:
        return 0
    return read_frames(reader.optional(clip, "GetClipProperty", None, "Start")) or 0


def _matching(
    found: Sequence[tuple[str, int, int]],
    mix: Path | None,
) -> tuple[str, int, int] | None:
    """The audio shot holding the file the analysis ran on, by name or by stem."""
    if mix is None:
        return None
    names = {mix.name.casefold(), mix.stem.casefold()}
    for shot in found:
        if shot[0].casefold() in names or Path(shot[0]).stem.casefold() in names:
            return shot
    return None


# --- reading the analysis ---------------------------------------------------------------------


def _music(
    beats: str,
    audio: str | None,
    tunes: str | None,
    solos: str | None,
    roles: dict[str, str] | None,
) -> Music:
    """Every file the measurement joins — or a refusal naming the one that is not there."""
    return Music(
        beats=_rows(_path(beats, "beat grid", "analyze_music writes it"), "beats"),
        tunes=_optional_rows(tunes, "tune list", "tunes"),
        solos=_optional_rows(solos, "solo changes", "solos"),
        roles=roles,
        audio=_path(audio, "master mix", "it is the file the analysis ran on") if audio else None,
    )


def _optional_rows(file: str | None, what: str, field: str) -> tuple[dict[str, Any], ...] | None:
    if file is None:
        return None
    return _rows(_path(file, what, "the structure analysis writes it"), field)


def _path(file: str, what: str, provenance: str) -> Path:
    path = Path(file).expanduser()
    if not path.is_file():
        raise InvalidRequestError(
            cause=f"There is no {what} at {str(path)!r}.",
            fix=(
                f"Pass the path a finished analysis job returned — {provenance}. "
                "analyze_music names the beats file in its result; get_job has it too."
            ),
            detail={"file": str(path)},
        )
    return path


def _rows(path: Path, field: str) -> tuple[dict[str, Any], ...]:
    """One analysis file's records, in time order — the shape ``records.write`` leaves."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError(
            cause=f"Could not read {path.name} as analysis JSON: {exc}.",
            fix="Pass the path an analysis job returned, unedited.",
            detail={"file": str(path), "field": field},
        ) from exc

    held = doc.get(field) if isinstance(doc, Mapping) else None
    if not isinstance(held, list):
        raise InvalidRequestError(
            cause=f"{path.name} holds no {field!r} records.",
            fix=f"That file is not the {field} analysis; pass the one whose kind is {field!r}.",
            detail={"file": str(path), "field": field},
        )
    rows = [
        dict(row)
        for row in held
        if isinstance(row, Mapping) and isinstance(row.get("t"), int | float)
    ]
    if not rows:
        # A file that was named but says nothing must not read like a file nobody named:
        # both would leave the column null, and only one of them is what the caller meant.
        raise InvalidRequestError(
            cause=f"{path.name} holds no {field} record with a time in it.",
            fix=(
                f"Pass the {field} file a finished analysis job wrote — its records each "
                'carry a "t" in seconds. An analysis that found nothing is worth rerunning '
                "rather than measuring against."
            ),
            detail={"file": str(path), "field": field},
        )
    return tuple(sorted(rows, key=lambda row: float(row["t"])))


def _roles(angles: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Clip name to angle role, as the agent lifted it out of its own sidecar.

    Two shapes read the same, because what the agent has to hand is whatever it wrote in
    that sidecar: a bare string is the role, and an object is a labelling with a ``role`` in
    it alongside whatever else was recorded about that angle. An entry with no role in it is
    dropped rather than refused — a sidecar that labels a camera by subject alone is a
    half-labelled corpus, not a wrong call.
    """
    if angles is None:
        return None
    if not isinstance(angles, Mapping):
        raise InvalidRequestError(
            cause="angles is not a mapping of clip name to angle.",
            fix='Pass {"C0012.mp4": {"role": "drums"}} — the role may also be a bare string.',
            detail={"angles": repr(angles)},
        )
    return {str(clip): role for clip, entry in angles.items() if (role := _role(entry)) is not None}


def _role(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping) and isinstance(entry.get("role"), str):
        return str(entry["role"])
    return None


def _transients(audio: Path | None, onsets: Onsets | None) -> tuple[float, ...] | None:
    """Onset times for the mix, or ``None`` when no mix was named.

    Not measuring is a different answer from measuring nothing, and the difference decides
    whether the file's transient column means "the cut is nowhere near a hit" or "nobody
    looked" — so it stays ``None`` all the way to the gist.
    """
    if audio is None:
        return None
    detect = onsets or measured_onsets
    return tuple(sorted(detect(audio)))


def measured_onsets(path: Path) -> tuple[float, ...]:
    """The default detector: decode the mix and take its onsets."""
    return tuple(float(seconds) for seconds in energy.onsets(decode.read(path)))


def _fingerprints(*files: str | None) -> list[dict[str, Any]]:
    """What the cache watches besides the cut: the analysis the measurement is made against."""
    return [cache.fingerprint(Path(file).expanduser()) for file in files if file is not None]


def _named(file: str | None) -> str | None:
    return None if file is None else str(Path(file).expanduser())


# --- the measurement --------------------------------------------------------------------------


def measure(
    shots: Sequence[Shot],
    clock: Clock,
    music: Music,
    transients: Sequence[float] | None,
) -> list[dict[str, Any]]:
    """One record per shot: where it starts in the music, and how far off everything it is.

    Offsets are signed from the cut to the thing it is measured against, so a negative
    number is a cut that arrives early and a positive one is a cut that arrives late. That
    sign is the whole point: cutting just before a hit and just after it are opposite
    edits, and an absolute value would call them the same.
    """
    times = [float(row["t"]) for row in music.beats]
    tune_times = [float(row["t"]) for row in (music.tunes or ())]
    solo_times = [float(row["t"]) for row in (music.solos or ())]
    roles = music.roles or {}

    rows: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        seconds = clock.seconds(shot.record_in)
        beat = _beat_at(music.beats, times, seconds)
        rows.append(
            {
                "cut": index,
                "clip": shot.clip,
                "role": roles.get(shot.clip),
                "opening": _opens(index, shot, shots),
                "t": _rounded(seconds),
                # Dual time for the two timeline positions, because an outlier the agent
                # flags is one the director then has to find in Resolve, and a bare frame
                # number is the one form nobody can scrub to.
                "in": dual_time(shot.record_in, clock.fps),
                "out": dual_time(shot.record_out, clock.fps),
                "seconds": _rounded(shot.duration / clock.fps),
                "beat_offset": None if beat is None else _rounded(seconds - float(beat["t"])),
                "beat": None if beat is None else beat.get("beat"),
                "bar": None if beat is None else beat.get("bar"),
                "in_bar": None if beat is None else beat.get("in_bar"),
                "transient_offset": _offset(transients, seconds),
                "tune": _tune_at(music.tunes, tune_times, seconds),
                "front": _front_at(music.solos, solo_times, seconds),
            }
        )
    return rows


def _opens(index: int, shot: Shot, shots: Sequence[Shot]) -> bool:
    """Whether this shot starts something rather than cutting away from something.

    The first shot is one. So is a shot that begins after a gap: there is no outgoing angle
    at that frame, so its distance from the nearest beat says nothing about how the director
    cuts, and averaging it in would quietly describe a style nobody has. Both are marked
    rather than dropped — a hand-edited timeline with three gaps in it is still a
    measurement, and the records say which shots were left out of the statistics.
    """
    return index == 1 or shot.record_in != shots[index - 2].record_out


def _beat_at(
    rows: Sequence[dict[str, Any]],
    times: Sequence[float],
    seconds: float,
) -> dict[str, Any] | None:
    found = nearest(times, seconds)
    return None if found is None else rows[found]


def _offset(times: Sequence[float] | None, seconds: float) -> float | None:
    if times is None:
        return None
    found = nearest(times, seconds)
    return None if found is None else _rounded(seconds - times[found])


def _tune_at(
    rows: Sequence[dict[str, Any]] | None,
    times: Sequence[float],
    seconds: float,
) -> int | None:
    """Which tune a cut happens in — nothing at all when it lands in the applause between two."""
    if not rows:
        return None
    for row, start in zip(rows, times, strict=False):
        end = row.get("end")
        if start <= seconds and isinstance(end, int | float) and seconds < float(end):
            return int(row["tune"]) if isinstance(row.get("tune"), int | float) else None
    return None


def _front_at(
    rows: Sequence[dict[str, Any]] | None,
    times: Sequence[float],
    seconds: float,
) -> str | None:
    """Who was out front when the cut happened: the last change at or before it.

    Before the first change there is still someone out front — the change records who it
    was, as the voice it took over *from* — so a cut in the opening head reads as that
    player rather than as nobody.
    """
    if not rows:
        return None
    first = rows[0].get("from")
    held: str | None = str(first) if isinstance(first, str) else None
    for row, start in zip(rows, times, strict=False):
        if start > seconds:
            break
        entered = row.get("to")
        held = str(entered) if isinstance(entered, str) else held
    return held


# --- the stats --------------------------------------------------------------------------------


def _summary(
    rows: Sequence[dict[str, Any]],
    clock: Clock,
    transients: Sequence[float] | None,
    grid: Sequence[float],
) -> dict[str, Any]:
    """The list-free reading: what a style profile is written from, and a self-review read.

    Every stat is taken over the records as they were written, not over the unrounded
    arithmetic behind them, so the gist and the file never disagree: an agent that greps the
    file and averages the column gets the number it was already told.
    """
    cut_to_music = [row for row in rows if not row["opening"]]
    transient_offsets = (
        None if transients is None else _offsets([row["transient_offset"] for row in cut_to_music])
    )
    return {
        "timeline_fps": clock.fps,
        "alignment": clock.reading(),
        "cuts": len(rows),
        "openings": sum(1 for row in rows if row["opening"]),
        "outside_grid": _outside(rows, grid),
        "beat_offsets": _offsets([row["beat_offset"] for row in cut_to_music]),
        "transient_offsets": transient_offsets,
        "bars": _histogram(row["in_bar"] for row in cut_to_music),
        "tunes": _spread("tune", cut_to_music) if _measured("tune", rows) else None,
        "solos": _spread("front", cut_to_music) if _measured("front", rows) else None,
        "shot_seconds": _lengths([row["seconds"] for row in rows]),
        "clips": _usage(rows, "clip"),
        "roles": _usage(rows, "role") if _measured("role", rows) else None,
    }


def _outside(rows: Sequence[dict[str, Any]], grid: Sequence[float]) -> int:
    """How many cuts fall outside the analysed span — the tell for a misaligned clock.

    The nearest-beat lookup clamps: a cut an hour past the end of the grid still reports a
    small-looking offset against the last beat, so a whole file measured against the wrong
    audio looks well-formed. Anything above zero here means the times and the analysis are
    not describing the same recording, and the alignment is what to check first.
    """
    if not grid:
        return len(rows)
    return sum(1 for row in rows if not grid[0] <= float(row["t"]) <= grid[-1])


def _measured(field: str, rows: Sequence[dict[str, Any]]) -> bool:
    """Whether a column was measured at all — an input nobody named reads as nothing, not zero."""
    return any(row[field] is not None for row in rows)


def _offsets(found: Sequence[float | None]) -> dict[str, Any] | None:
    """How far off the cuts are, and which way — early and late counted apart."""
    measured = [value for value in found if value is not None]
    if not measured:
        return None
    sizes = [abs(value) for value in measured]
    return {
        "measured": len(measured),
        "mean_abs": _rounded(statistics.fmean(sizes)),
        "median_abs": _rounded(statistics.median(sizes)),
        "max_abs": _rounded(max(sizes)),
        "early": sum(1 for value in measured if value < 0),
        "late": sum(1 for value in measured if value > 0),
        "on": sum(1 for value in measured if value == 0),
    }


def _lengths(seconds: Sequence[float]) -> dict[str, Any]:
    if not seconds:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": _rounded(statistics.fmean(seconds)),
        "median": _rounded(statistics.median(seconds)),
        "min": _rounded(min(seconds)),
        "max": _rounded(max(seconds)),
    }


def _usage(rows: Sequence[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    """How much of the cut each angle — or each role — actually holds.

    Counted in both shots and seconds because they answer different questions: a role can
    take a third of the cuts and a tenth of the screen time, and which of those a style
    profile should say is the director's call, not this tool's.
    """
    total = sum(float(row["seconds"]) for row in rows) or 1.0
    usage: dict[str, dict[str, Any]] = {}
    for row in rows:
        held = row[field]
        key = UNLABELLED if held is None else str(held)
        entry = usage.setdefault(key, {"cuts": 0, "seconds": 0.0, "share": 0.0})
        entry["cuts"] += 1
        entry["seconds"] += float(row["seconds"])
    for entry in usage.values():
        entry["share"] = _rounded(entry["seconds"] / total)
        entry["seconds"] = _rounded(entry["seconds"])
    return usage


def _spread(field: str, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How the cuts fall across the tunes, or across who was in front."""
    placed = [row[field] for row in rows if row[field] is not None]
    return {
        "covered": len(set(placed)),
        "outside": len(rows) - len(placed),
        "cuts": _histogram(iter(placed)),
    }


def _histogram(values: Any) -> dict[str, int]:
    """Counts keyed by value as a string — JSON has no integer keys to speak of."""
    counted = Counter(value for value in values if value is not None)
    return {str(key): count for key, count in sorted(counted.items(), key=lambda one: str(one[0]))}


def _rounded(seconds: float) -> float:
    return round(seconds, SECONDS_PRECISION)


