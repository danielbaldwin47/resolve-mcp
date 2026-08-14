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
from bisect import bisect_right
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
from ..resolve.mix import MixShot, audio_shots
from ..resolve.session import frame_rate
from ..resolve.timeline import (
    Reader,
    angle_of,
    current_name,
    find_timeline,
    fingerprint,
    item_enabled,
    items_in_track,
    name_of,
    open_project,
    read_frames,
    start_frame,
    track_enabled,
)
from ..timing import SECONDS_PRECISION, dual_time, to_frames
from . import decode, energy, records
from .beats import GridTrust, nearest, spacing, trust

log = get_logger("analysis")

KIND = "correlate_timeline"

INLINE_CUTS = 12
"""How many records the gist carries: enough to see the shape of the cut, not the concert."""

UNLABELLED = "unlabelled"
"""Where shots from a clip the angle sidecar does not name are counted."""

BEAT_REACH = 1.0
"""How far from a beat a cut may sit and still be described by it, in local beat intervals.

Inside a grid that reaches the cut, the nearest beat is at most *half* a beat away by
construction, so a whole beat is slack rather than a threshold to tune. What it catches is
the cut the grid does not reach at all — past its ends, or in a hole it left — where the
nearest *surviving* beat can be seconds away and the offset against it describes nothing
(#160: one cut 6.08 s from its beat in a grid of 0.39 s beats, because the detector stopped
six seconds before the cut). Drawing the line at the width of a beat rather than at the edge
of the grid keeps the honest near miss: a cut 20 ms before the first beat is a real
measurement, and refusing it would be trading one wrong answer for another.
"""

BLACK = "black"
"""Where the stretches nothing covers are counted — a known absence, not a missing label."""

RHYTHM_BINS: tuple[tuple[str, float], ...] = (
    ("<2", 2.0),
    ("2-4", 4.0),
    ("4-8", 8.0),
    ("8-15", 15.0),
    ("15-30", 30.0),
    (">30", float("inf")),
)
"""The corpus shot-length bins, as label and upper edge, in seconds.

Half-open on the upper edge: a shot of exactly 4 s counts in ``4-8``, and the last bin takes
everything from 30 s up. The labels are the ones the corpus and the style profiles already
speak, so a histogram from this report drops straight into a comparison against them; the
edges are that vocabulary's, not a threshold this file tuned.
"""

ALTERNATION_MIN = 2
"""How many cuts an A/B run needs before it is alternation rather than a cut.

Two shots that differ are just a cut — the pattern only exists once the cut *returns*, so a
run is counted from three shots (A B A) up. Without this floor every two-shot timeline reads
as perfectly alternating, which is arithmetic rather than a fact about the edit.
"""

RAMP_MIN = 4
"""How many cuts of one direction make a ramp rather than a coincidence.

Three shots that happen to shorten are two cuts, and two cuts in a row go one way in any cut
list long enough — the shape only exists once it keeps going. Counted from five shots (four
cuts) up, which is where a run stops being what randomness hands you.
"""

ALTERNATION_FLOOR = 0.8
ONE_BIN_FLOOR = 0.6
CV_FLOOR = 0.35
RAMP_FLOOR = 0.6
"""The four numbers ``reads_metronomic`` is drawn at — see ``HEURISTIC``."""

HEURISTIC = (
    "reads_metronomic is true when the longest strict A/B alternation run covers more than "
    f"{ALTERNATION_FLOOR} of the cuts and the lengths are mechanical with it: either one bin "
    f"holds more than {ONE_BIN_FLOOR} of the shots, or the coefficient of variation of shot "
    f"lengths is under {CV_FLOOR}, or the longest run of shots that only shorten or only "
    f"lengthen covers more than {RAMP_FLOOR} of the cuts. It is a warning to look at, not a "
    "verdict: a passage that genuinely wants a two-camera ping-pong scores the same as a cut "
    "nobody varied, and only the director can tell them apart."
)
"""The heuristic in words, carried in the report so nobody has to read this file to check it."""

GEAR_WINDOW_SECONDS = 1.0
"""How coarse the loudness curve the gearing is read against is, in seconds per window.

A second is a bar at slow rock tempo and half of one at anything faster: coarse enough that
a snare hit does not move a window into the loud third, fine enough that the quiet verse and
the last chorus land in different ones. Nothing here is a loudness measurement anybody
publishes — the only question asked of it is which windows are louder than which.
"""

QUIET, MID, LOUD = "quiet", "mid", "loud"
"""The three gears, named where the report names them."""

SUB_TWO_SECONDS = RHYTHM_BINS[0][1]
"""What counts as a short shot: the corpus's own ``<2`` edge, not a second threshold."""

RATE_RATIO_FLOOR = 1.3
GEAR_CV_FLOOR = 0.65
"""The two numbers ``one_speed`` is drawn at — see ``GEAR_HEURISTIC``."""

GEAR_HEURISTIC = (
    "one_speed is true when the loud third of the music is cut less than "
    f"{RATE_RATIO_FLOOR}x as fast as the quiet third (rate_ratio) and the coefficient of "
    f"variation of shot lengths is under {GEAR_CV_FLOOR}. Terciles are thirds of the span by "
    "level, not by time: the 1 s RMS windows inside the cut are ranked and split three ways, "
    "and each shot is counted in the window its first frame lands in — a shot the curve does "
    "not reach is counted in outside_shots and in no tercile. It is a warning to look "
    "at, not a verdict — a ballad cut at one speed throughout is a real edit, and a passage "
    "whose loudness never moves has no gears to change. What it catches is the build that cut "
    "the guitar solo at the pace it cut the intro."
)
"""The gearing heuristic in words, carried in the report beside the numbers it was drawn from."""

READING = 7
"""What this measurement *is*; bumped whenever a rerun over unchanged inputs would differ.

The cache is keyed on what was measured, not on the code that measured it, so a call whose
inputs have not changed is otherwise answered out of a file the previous version wrote — for
#142 that is a report with no track on its records and no ``visible`` on its header, handed
back as though it were this measurement rather than the one before it. Both the shape and
the reading count: 3 rather than 2 because the same shots now resolve to a different strip,
4 rather than 3 because the header now carries ``shot_rhythm``, 5 rather than 4 because that
block now carries ``gears``, and 6 rather than 5 because ``reads_metronomic`` now reads the
ramp as well and the gearing no longer counts shots the level curve does not reach, and 7
rather than 6 because every record now carries its place in the bar map — a cached hit from
an earlier reading answers the self-review question with a file that never asked it.
"""

GIVEN = "given"
"""The alignment mode where the caller named the frame rather than the server reading it."""

Onsets = Callable[[Path], tuple[float, ...]]
"""The transient seam: a WAV in, onset times in seconds out."""

Levels = Callable[[Path], tuple[tuple[float, float], ...]]
"""The loudness seam: a WAV in, ``(window start in seconds, RMS in dBFS)`` per window out.

Pairs rather than a record type because every caller of it does one thing — rank the windows
against each other — and a fake curve a test writes by hand should be readable as the curve
it is.
"""


class Shot(NamedTuple):
    """One shot as the measurement needs it: what it is and where it sits."""

    clip: str | None
    """The angle on screen, or ``None`` for black — the stretch no enabled item covers."""

    record_in: int
    duration: int
    media: bool = True
    """Whether Resolve gave it a media pool item — false for transitions and generators."""

    track: int | None = None
    """The video track the frame is taken from; ``None`` along with ``clip`` for black."""

    @property
    def record_out(self) -> int:
        return self.record_in + self.duration

    def overlaps(self, other: Shot) -> bool:
        return self.record_in < other.record_out and other.record_in < self.record_out


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
    bars: tuple[dict[str, Any], ...] | None
    roles: dict[str, str] | None
    audio: Path | None


def correlate_timeline(
    connection: ResolveConnection,
    beats: str,
    timeline: str | None = None,
    audio: str | None = None,
    tunes: str | None = None,
    solos: str | None = None,
    bars: str | None = None,
    angles: Mapping[str, Any] | None = None,
    track: int | None = None,
    audio_at: Any | None = None,
    refresh: bool = False,
    onsets: Onsets | None = None,
    loudness: Levels | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Start a job measuring a timeline against its music analysis. Returns the job record.

    Every input is read and checked here, before the job exists: a path that is not there
    and a file that is not what it claims are wrong *calls*, and a wrong call should come
    back from the tool rather than from a poll two seconds later.

    ``track`` chooses what is measured. Left out, it is the *visible* edit: every frame
    resolved to the topmost enabled video item, so an overlay on V2 is a shot and the frames
    it covers belong to it rather than to whatever sits underneath. Named, it is that video
    track alone, laid out as the editor left it. Both are real questions, but only the first
    describes the film anybody watches (#142).

    ``angles`` is a mapping the caller passes, not a file this reads: the angle sidecar is
    the agent's document (#45 — no server path reads or writes it), so the labels arrive
    already lifted out of it.

    ``audio_at`` is the timeline frame the analysed audio's own zero sits at, for when no
    clip on the timeline carries it and so none can be read.

    ``bars`` is the bar map ``detect_bars`` writes, and it is the file that makes a cut
    measurable against the *form* rather than only against the pulse (#180). The beat grid
    already carries a bar number, but only the one the beat model committed to — on material
    where it commits to nothing that column is a meter of one, and the bar-map file is the
    second reading that recovers a real bar line. Optional, because a grid the model did
    commit to needs no second opinion.

    ``onsets`` is the transient seam and ``loudness`` the level one; both defaults decode the
    audio for real.
    """
    config = config or get_config()
    roles = _roles(angles)
    music = _music(beats, audio, tunes, solos, bars, roles)
    cut = _read_cut(connection, timeline, track, music.audio, audio_at)

    params: dict[str, Any] = {
        "timeline": cut.name,
        "track": None if track is None else int(track),
        "audio_at": cut.clock.zero_frame if cut.clock.mode == GIVEN else None,
        "beats": _named(beats),
        "audio": _named(audio),
        "tunes": _named(tunes),
        "solos": _named(solos),
        "bars": _named(bars),
        "angles": dict(sorted(roles.items())) if roles is not None else None,
    }
    watched = [
        {"reading": READING},
        cut.fingerprint,
        *_fingerprints(beats, audio, tunes, solos, bars),
    ]
    key = cache.cache_key(KIND, watched, params)

    def work(progress: Progress) -> JobOutput:
        return correlate(
            cut.shots,
            cut.clock,
            cut.name,
            music,
            key,
            params,
            cut.visible,
            progress,
            onsets,
            loudness,
            config,
        )

    return start_job(KIND, params, work, cache_key=key, refresh=refresh, config=config)


def correlate(
    shots: Sequence[Shot],
    clock: Clock,
    name: str,
    music: Music,
    key: str,
    params: dict[str, Any],
    visible: dict[str, Any],
    progress: Progress,
    onsets: Onsets | None = None,
    loudness: Levels | None = None,
    config: Config | None = None,
) -> JobOutput:
    """The worker: transients, then one record per shot on disk and the stats inline."""
    config = config or get_config()

    progress(0.1, "reading the transients")
    transients = _transients(music.audio, onsets)

    progress(0.5, "reading the level curve")
    levels = _levels(music.audio, loudness)

    progress(0.7, "measuring the cut against the music")
    grid = trust(music.beats)
    rows = measure(shots, clock, music, transients, grid)
    summary = _summary(
        rows, clock, visible, transients, [float(one["t"]) for one in music.beats], grid, levels
    )

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


class Cut(NamedTuple):
    """Everything Resolve answered for, taken in one reading before the job starts."""

    shots: list[Shot]
    clock: Clock
    fingerprint: dict[str, Any]
    name: str
    visible: dict[str, Any]
    """How the shots were arrived at — which tracks, and how many gaps became black."""


def _read_cut(
    connection: ResolveConnection,
    name: str | None,
    track: int | None,
    mix: Path | None = None,
    at: Any | None = None,
) -> Cut:
    """Everything Resolve has to answer for, taken before the job starts.

    A job that reached back into Resolve would be measuring a timeline the director may
    have moved on from, and would need the Resolve lock to do it. One reading up front,
    then arithmetic — and a handle that dies during it fails the *call*, which is the one
    failure the tool layer's single reconnect can still fix.

    The reader is told whether this timeline is the project's current one, because the
    visible edit is decided partly on track enable-state and that getter answers ``False``
    for everything off the current timeline (#84). Believing it there would read a whole
    concert as one black shot.
    """
    project = open_project(connection)
    timeline = find_timeline(project, name)
    found = name_of(timeline)
    reader = Reader(connection, current=found == current_name(project))

    fps = frame_rate(project, timeline)
    if not fps:
        raise InvalidRequestError(
            cause=f"Resolve reported no frame rate for {found!r}, so no shot has a time.",
            fix="Open the timeline in Resolve and check its frame rate, then measure again.",
            detail={"timeline": found},
        )

    shots, visible = _read_shots(reader, timeline, track)
    if not shots:
        raise InvalidRequestError(
            cause=(
                f"Video track {track} of {found!r} holds no shots to measure."
                if track is not None
                else f"No enabled video track of {found!r} holds a shot to measure."
            ),
            fix=(
                "Name the timeline with timeline=, and a single video track with track= if "
                "you want that track alone rather than the visible edit — inspect_timeline "
                "shows which tracks hold what."
            ),
            detail={"timeline": found, "track": None if track is None else int(track)},
        )
    given = to_frames(at, fps, "audio_at")
    clock = _clock(reader, timeline, fps, mix, given)
    return Cut(shots, clock, fingerprint(reader, timeline), found, visible)


def _read_shots(
    reader: Reader, timeline: Any, track: int | None
) -> tuple[list[Shot], dict[str, Any]]:
    """The strip of shots to measure, and the reading of where it came from.

    Two different questions, and the reading says which was asked. A measurement of the
    wrong stack is shaped exactly like a measurement of the right one — every offset
    well-formed, every clip named — so the one line that separates them travels with it.
    """
    tracks = int(read_frames(reader.optional(timeline, "GetTrackCount", 0, "video")) or 0)
    if track is not None:
        shots = _shots(reader, timeline, int(track))
        # ``enabled_known`` is None rather than False here: this branch never asks whether a
        # track is switched on, and reporting on a getter it did not read would be an answer
        # to a question nobody put.
        return shots, _reading("track", tracks, [int(track)], [], None, shots)

    layers: list[Shot] = []
    measured: list[int] = []
    skipped: list[int] = []
    for index in range(1, tracks + 1):
        if track_enabled(reader, timeline, "video", index) is False:
            skipped.append(index)
            continue
        measured.append(index)
        layers.extend(_shots(reader, timeline, index, only_enabled=True))
    visible = _composite(layers, start_frame(timeline))
    if skipped:
        log.info("Video tracks %s are switched off, so nothing on them is visible", skipped)
    return visible, _reading("visible", tracks, measured, skipped, reader.reads_current, visible)


def _reading(
    mode: str,
    tracks: int,
    measured: Sequence[int],
    skipped: Sequence[int],
    enabled_known: bool | None,
    shots: Sequence[Shot],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "video_tracks": tracks,
        # Every track this reading took shots from or would have: with ``skipped`` it
        # accounts for all ``video_tracks``, so a track missing from both is a bug rather
        # than a track that happened to be empty.
        "measured": list(measured),
        "skipped": list(skipped),
        # False means every track was measured because none could be ruled out (#84), not
        # that every track was on — the difference matters to anyone reading ``measured``.
        # None means the question was never asked, which is what reading one track does.
        "enabled_known": enabled_known,
        "black": sum(1 for shot in shots if shot.clip is None),
    }


def _composite(shots: Sequence[Shot], opens_at: int) -> list[Shot]:
    """The stacked tracks flattened into the one strip of picture that plays (#142).

    Every frame belongs to the topmost item covering it, so an overlay is a shot and the
    stretch of the clip beneath it is not — that stretch is not on screen, and attributing
    the overlay's frames to it describes a cut nobody made.

    Where nothing covers a frame the viewer sees black, and black is a shot: the director
    who left a gap under an empty V1 chose that, and a gap that vanishes takes both its cuts
    with it. That includes the run-up from ``opens_at``, the timeline's own first frame,
    when the first picture lands after it — a film that opens on black opens on a held frame
    somebody chose the length of, and the cut out of it is one of the most deliberate in the
    edit.

    What is *not* a shot is the black after the last picture. A black shot needs a start and
    an end the edit decides, and that one has no end: how long it runs is however far
    whatever else is on the timeline reaches — usually an audio item a frame or an hour
    longer than the cut — which is a fact about the mix rather than about the cut.

    A clip the overlay interrupts comes back as a second shot, because the frame it comes
    back on is a cut the viewer sees, whatever the timeline's item list says.
    """
    edges = sorted({edge for shot in shots for edge in (shot.record_in, shot.record_out)})
    if edges and opens_at < edges[0]:
        edges.insert(0, opens_at)
    runs: list[tuple[Shot | None, int, int]] = []
    for start, end in zip(edges, edges[1:], strict=False):
        # Coverage cannot change inside a run: every frame at which some item starts or ends
        # is an edge, so what is on top at ``start`` is on top until ``end``.
        top = _topmost(shots, start)
        if runs and runs[-1][0] is top:
            runs[-1] = (top, runs[-1][1], end)
        else:
            runs.append((top, start, end))
    return [
        Shot(clip=None, record_in=start, duration=end - start, media=False, track=None)
        if top is None
        else Shot(
            clip=top.clip, record_in=start, duration=end - start, media=top.media, track=top.track
        )
        for top, start, end in runs
    ]


def _topmost(shots: Sequence[Shot], frame: int) -> Shot | None:
    """What is on screen at ``frame`` — the highest track holding it, or nothing at all."""
    covering = [shot for shot in shots if shot.record_in <= frame < shot.record_out]
    return max(covering, key=lambda shot: shot.track or 0, default=None)


def _shots(reader: Reader, timeline: Any, track: int, only_enabled: bool = False) -> list[Shot]:
    """The shots on one video track, in the order they play.

    A shot whose position will not read is dropped rather than guessed at: a measurement
    with an invented time in it is worse than one shot short, and the log says which.

    ``only_enabled`` drops the items the editor switched off, which is what the visible edit
    needs and what measuring a named track deliberately does not do: a track read alone is
    read as it was laid out.
    """
    found: list[Shot] = []
    for item in items_in_track(timeline, "video", int(track)):
        if only_enabled and not item_enabled(reader, item):
            continue
        record_in = read_frames(item.GetStart())
        duration = read_frames(item.GetDuration())
        if record_in is None or duration is None:
            log.warning("Skipped a shot on video track %d: Resolve gave no position", track)
            continue
        clip = reader.optional(item, "GetMediaPoolItem", None)
        name = angle_of(reader, item, clip) or str(item.GetName() or "")
        found.append(
            Shot(
                clip=name,
                record_in=record_in,
                duration=duration,
                media=clip is not None,
                track=int(track),
            )
        )
    ordered = sorted(found, key=lambda shot: (shot.record_in, not shot.media))
    return _without_transitions(ordered, track)


def _without_transitions(shots: Sequence[Shot], track: int) -> list[Shot]:
    """Drop the transitions Resolve hands back alongside the shots.

    ``GetItemListInTrack`` returns a dissolve as an item of its own, sitting across the
    boundary it softens: on a hand-edited concert that is a short item every time the
    editor did anything other than a straight cut, and one real corpus timeline came back
    159 of them in 525 items. Left in, each one is a shot that was never cut — its start is
    half a transition *before* the cut it belongs to, which lands in the offset
    distribution as a cut the editor did not make, and its length lands in the duration
    stats as a shot nobody held.

    Two items cannot overlap on one video track, so overlapping a real shot is what a
    transition does and a shot cannot. That is a fact about tracks rather than about any
    getter, which is why it is the test rather than the absence of a media pool item — a
    generator has none of those either, and a generator on the cut track is a shot.

    *Which* shot it overlaps is the part worth being careful about. Dissolves come in two
    shapes and corpus entry 2 has both: centred on the cut, overlapping the outgoing shot,
    and aligned to the incoming one, merely abutting the outgoing shot and overlapping only
    what follows. Compared against its neighbour alone the second shape survives — and then
    the real shot behind it is the thing that overlaps, so it is dropped instead. That swap
    leaves the cut count right and the cut wrong, which is the worst way to be wrong. So
    the comparison is against every *other* item on the track, not the one before.

    Overlap alone is not quite the test, because a dissolve into a slate overlaps only the
    slate and a slate has no pool item either — so "overlaps something real" cannot separate
    them and "overlaps anything" throws both away. What separates them is that a shot holds
    some stretch of track *exclusively* and a transition never does: every frame of a
    dissolve is a frame some neighbour is also on. So a pool-less item is a transition when
    the rest of the track already covers all of it, and a shot otherwise.
    """
    kept: list[Shot] = []
    dropped = 0
    for index, shot in enumerate(shots):
        others = [other for position, other in enumerate(shots) if position != index]
        if not shot.media and _covered_by(shot, others):
            dropped += 1
            continue
        if kept and shot.overlaps(kept[-1]):
            dropped += 1
            continue
        kept.append(shot)
    if dropped:
        log.info(
            "Video track %d: %d transitions read past, %d shots kept", track, dropped, len(kept)
        )
    return kept


def _covered_by(shot: Shot, others: Sequence[Shot]) -> bool:
    """Is every frame of ``shot`` also held by something else on the track?

    Walked rather than summed, because two neighbours that each cover half of a dissolve
    cover all of it between them, and a gap anywhere means the item is holding track of its
    own — which is the thing a transition never does.
    """
    frame = shot.record_in
    while frame < shot.record_out:
        reach = max(
            (other.record_out for other in others if other.record_in <= frame < other.record_out),
            default=None,
        )
        if reach is None:
            return False
        frame = reach
    return True


def _clock(
    reader: Reader, timeline: Any, fps: float, mix: Path | None, given: int | None = None
) -> Clock:
    """Where the analysed mix sits under the cut, read off the audio shot that holds it.

    Unless the caller said. A hand-edited concert routinely reaches the cut through a
    multicam's own audio angle, and then no clip on the timeline *is* the mastered mix —
    the in point that can be read belongs to the multicam's timebase, which has no stated
    relationship to the mix's own zero. Every mode below would be guessing, and a guess
    here is invisible: the reading comes back looking ordinary, with every time in it
    shifted by the same silent amount. So a caller who knows — a full-timeline render puts
    the mix's zero exactly at the timeline's first frame — says so, and is believed.

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
    if given is not None:
        return Clock(given, fps, GIVEN, mix.name if mix else None, mix is not None)

    found = audio_shots(reader, timeline)
    wanted = _matching(found, mix)
    chosen = wanted or (found[0] if found else None)
    if chosen is None:
        return Clock(start_frame(timeline), fps, "timeline_start", None, False)
    return Clock(chosen.zero_frame, fps, "audio_clip", chosen.name, wanted is not None)


def _matching(found: Sequence[MixShot], mix: Path | None) -> MixShot | None:
    """The audio shot holding the file the analysis ran on, by name or by stem."""
    if mix is None:
        return None
    names = {mix.name.casefold(), mix.stem.casefold()}
    for shot in found:
        if shot.name.casefold() in names or Path(shot.name).stem.casefold() in names:
            return shot
    return None


# --- reading the analysis ---------------------------------------------------------------------


def _music(
    beats: str,
    audio: str | None,
    tunes: str | None,
    solos: str | None,
    bars: str | None,
    roles: dict[str, str] | None,
) -> Music:
    """Every file the measurement joins — or a refusal naming the one that is not there."""
    return Music(
        beats=_rows(_path(beats, "beat grid", "analyze_music writes it"), "beats"),
        tunes=_optional_rows(tunes, "tune list", "tunes"),
        solos=_optional_rows(solos, "solo changes", "solos"),
        bars=_optional_rows(bars, "bar map", "bars", "detect_bars writes it"),
        roles=roles,
        audio=_path(audio, "master mix", "it is the file the analysis ran on") if audio else None,
    )


def _optional_rows(
    file: str | None,
    what: str,
    field: str,
    provenance: str = "the structure analysis writes it",
) -> tuple[dict[str, Any], ...] | None:
    if file is None:
        return None
    return _rows(_path(file, what, provenance), field)


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


def _levels(audio: Path | None, loudness: Levels | None) -> tuple[tuple[float, float], ...] | None:
    """The mix's level curve, or ``None`` when no mix was named.

    Same distinction the transients keep: a cut nobody could measure the music's loudness for
    has no gears to report, and reporting flat ones would say the music never moved.
    """
    if audio is None:
        return None
    read = loudness or measured_levels
    return tuple(sorted(read(audio)))


def measured_levels(path: Path) -> tuple[tuple[float, float], ...]:
    """The default curve: decode the mix and take a coarse RMS window by window.

    RMS rather than the LUFS curve ``analyze_energy`` writes, and this file's own read rather
    than that job's output, for the same reason: the only question asked of it is which
    windows are louder than which, and ranking survives the missing K-weighting intact. It
    also keeps the reading available on a concert nobody ran the energy job over.

    It costs a second decode of the mix, because the seam is a path in and numbers out and
    the transient detector behind the other one is injectable for the same reason. One extra
    read of a file this job already reads is the price of both seams staying testable without
    audio; if it ever stops being, the fix is one decode handed to both, not a curve this
    file computes inline.
    """
    return tuple(
        (float(point.seconds), float(point.rms_dbfs))
        for point in energy.rms_curve(decode.read(path), GEAR_WINDOW_SECONDS)
    )


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
    grid: GridTrust | None = None,
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
    bar_times = [float(row["t"]) for row in (music.bars or ())]
    roles = music.roles or {}
    trusted = (grid if grid is not None else trust(music.beats)).trusted
    widths = spacing(times)

    rows: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        seconds = clock.seconds(shot.record_in)
        found = nearest(times, seconds)
        beat = None if found is None else music.beats[found]
        # Only rows the beat gate let through are claimed by the reach rule, so ``gated`` keeps
        # meaning exactly what it meant: beats the grid describes badly, counted the same way
        # across passes.
        stranded = (
            found is not None
            and trusted[found]
            and _out_of_reach(seconds, times[found], widths[found])
        )
        # The bar line is read the way the beat is — nearest, with a signed offset — and for
        # the same reason: a cut twenty milliseconds *before* a downbeat is a cut on the one,
        # and a rule that assigned it to the bar it technically falls inside would file the
        # commonest placement in this material under the wrong bar every time.
        line = None if not bar_times else nearest(bar_times, seconds)
        bar_line = None if line is None else (music.bars or ())[line]
        rows.append(
            {
                "cut": index,
                "clip": shot.clip,
                "track": shot.track,
                "role": None if shot.clip is None else roles.get(shot.clip),
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
                # Whether the grid describes the music here at all (#112, #160). The
                # measurement itself is kept either way — a marked cut is a fact about the
                # detector, and dropping the row would hide the very thing the gate exists to
                # report.
                "in_grid": found is not None and trusted[found] and not stranded,
                "stranded": stranded,
                # From the bar map, when one was named: which bar of the form this cut is on,
                # where that bar sits in the four-bar group, and how far off the line it is.
                "map_bar": None if bar_line is None else bar_line.get("bar"),
                "in_group": None if bar_line is None else bar_line.get("in_group"),
                "bar_offset": (
                    None if bar_line is None else _rounded(seconds - float(bar_line["t"]))
                ),
                "transient_offset": _offset(transients, seconds),
                "tune": _tune_at(music.tunes, tune_times, seconds),
                "front": _front_at(music.solos, solo_times, seconds),
            }
        )
    return rows


def _out_of_reach(seconds: float, beat: float, width: float | None) -> bool:
    """Whether the grid reaches this cut, or only has a beat somewhere in the distance (#160).

    The #112 gate refuses *beats*, so it catches a cut whose nearest beat is one it refused.
    It cannot catch the other shape: where the grid stops — or leaves a hole — the nearest
    *surviving* beat is trusted and far away, and the row goes into the trusted columns
    carrying an offset measured across the silence. One such cut in 504 moved no histogram
    and dragged a mean to 3.6× its own median, which is the reading a style profile is
    written from.

    A grid that cannot say how wide a beat is — one lone beat, or two beats at the same time —
    is not a grid this can judge against, and inventing a verdict there would be the same
    mistake in the other direction. Such a grid answers to the #112 gate instead: a single
    beat is a meter of one, which that gate refuses whole.
    """
    return width is not None and width > 0 and abs(seconds - beat) > BEAT_REACH * width


def _opens(index: int, shot: Shot, shots: Sequence[Shot]) -> bool:
    """Whether this shot starts something rather than cutting away from something.

    The first shot is one. So is a shot that begins after a gap: there is no outgoing angle
    at that frame, so its distance from the nearest beat says nothing about how the director
    cuts, and averaging it in would quietly describe a style nobody has. Both are marked
    rather than dropped — a hand-edited timeline with three gaps in it is still a
    measurement, and the records say which shots were left out of the statistics.
    """
    return index == 1 or shot.record_in != shots[index - 2].record_out


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
    visible: dict[str, Any],
    transients: Sequence[float] | None,
    grid: Sequence[float],
    trusted: GridTrust,
    levels: Sequence[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """The list-free reading: what a style profile is written from, and a self-review read.

    Every stat is taken over the records as they were written, not over the unrounded
    arithmetic behind them, so the gist and the file never disagree: an agent that greps the
    file and averages the column gets the number it was already told.

    ``shot_rhythm`` is the one reading here that is about the cut rather than about the music
    — the metronome check a build cannot perform on itself by looking at its offsets, and the
    gearing check that joins the two.
    """
    cut_to_music = [row for row in rows if not row["opening"]]
    # The gate is applied here and nowhere else (#112). Transients need no grid, so they are
    # measured over every cut; the beat and bar statistics are taken over the cuts the grid
    # can actually describe, and the count of the rest is reported rather than swallowed.
    in_grid = [row for row in cut_to_music if row["in_grid"]]
    stranded = sum(1 for row in cut_to_music if row["stranded"])
    transient_offsets = (
        None if transients is None else _offsets([row["transient_offset"] for row in cut_to_music])
    )
    return {
        "timeline_fps": clock.fps,
        "alignment": clock.reading(),
        "visible": visible,
        "cuts": len(rows),
        "openings": sum(1 for row in rows if row["opening"]),
        # Three refusals, and none of them implies the others. ``outside_grid`` is a cut
        # beyond the ends of the analysed span, which means the times and the audio are not
        # the same recording; ``gated`` is a cut whose nearest beat the grid describes badly;
        # ``stranded`` is a cut with no beat near it at all, scored against a trusted one too
        # far away to be describing the music there (#160). A misaligned clock shows in the
        # first, rubato in the second, a detector that stopped early in the third. The first
        # and third overlap on a cut beyond the ends far enough to be out of reach, and that
        # is two facts about one cut rather than one counted twice: only ``stranded`` keeps it
        # out of the beat statistics, and only ``outside_grid`` says the clock is suspect.
        "outside_grid": _outside(rows, grid),
        "gated": len(cut_to_music) - len(in_grid) - stranded,
        "stranded": stranded,
        "grid_meter": trusted.meter,
        "grid_refused": trusted.reasons,
        "beat_offsets": _offsets([row["beat_offset"] for row in in_grid]),
        "transient_offsets": transient_offsets,
        "bars": _histogram(row["in_bar"] for row in in_grid),
        # The bar map's own histogram, and deliberately not gated on ``in_grid``: the beat
        # gate refuses beats the *grid* describes badly, and a bar map exists precisely for
        # the grids that get refused wholesale. Gating this on the grid's verdict would empty
        # the one reading that still had something to say (#180).
        "bar_groups": (
            _histogram(row["in_group"] for row in cut_to_music)
            if _measured("in_group", rows)
            else None
        ),
        "bar_offsets": (
            _offsets([row["bar_offset"] for row in cut_to_music])
            if _measured("bar_offset", rows)
            else None
        ),
        "tunes": _spread("tune", cut_to_music) if _measured("tune", rows) else None,
        "solos": _spread("front", cut_to_music) if _measured("front", rows) else None,
        "shot_seconds": _lengths([row["seconds"] for row in rows]),
        "shot_rhythm": _rhythm(rows, levels),
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


def _rhythm(
    rows: Sequence[dict[str, Any]],
    levels: Sequence[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """How varied the cutting is, in the three shapes a metronomic cut shows up in.

    A build reviews itself with this report, and the failure it cannot see from the inside is
    the one every critic names first: two cameras traded back and forth on a fixed length. It
    is invisible in the offsets — every cut can sit dead on its beat and still read as a
    metronome — so it needs its own reading: how the shot lengths spread, how strictly the
    angles alternate, how much of the cut sits in a single length bin, and how far it ramps.

    The ramp is the same mechanism wearing a disguise. A two-camera trade whose lengths walk
    steadily from ten seconds down to three varies every one of them, so the bin and the
    spread both call it varied — and a panel called it a mechanical metronome anyway, because
    a ladder is as countable a pattern as a fixed length (P3·R3: strict two-framing, 9.9 s to
    2.9 s without a step back up). The run of one-way lengths is what catches it.

    ``gears`` asks the other half of the same question. A cut can vary its lengths and still
    run at one speed through the whole concert — the intro cut as fast as the solo — and no
    reading over the shot list alone can see it, because the thing missing is the music's own
    dynamics. So the level curve is split into thirds by loudness and the cutting rate is
    reported per third: a build that changes gear shows it here, and one that does not is
    told so before a critic says it.

    Nothing here is a gate. ``reads_metronomic`` and ``one_speed`` are sentences the report
    says out loud along with the numbers and the rules that drew them (``HEURISTIC``,
    ``GEAR_HEURISTIC``), so a builder can disagree with them on the evidence rather than argue
    with a threshold. Trading two cameras for four minutes is a real edit some music asks for;
    the point is that the builder *decided* it.
    """
    lengths = [float(row["seconds"]) for row in rows]
    # The angle on screen is what alternation is about, so black counts as one source rather
    # than as a hole: cutting camera, black, camera, black is a pattern, not an absence.
    sources = [BLACK if row["clip"] is None else str(row["clip"]) for row in rows]
    histogram = _bins(lengths)
    alternation = _alternation(sources)
    uniformity = _uniformity(lengths, histogram)
    ramp = _ramp(lengths)
    return {
        "shots": len(rows),
        "lengths": {
            "histogram": histogram,
            # max/min over the shots as written; ``shot_seconds`` holds the two numbers it is
            # taken from. None when the shortest shot rounds to zero and the ratio would not
            # divide.
            "spread_ratio": _ratio(lengths),
            "mean": _rounded(statistics.fmean(lengths)) if lengths else None,
            "median": _rounded(statistics.median(lengths)) if lengths else None,
        },
        "alternation": alternation,
        "uniformity": uniformity,
        "ramp": ramp,
        "reads_metronomic": _metronomic(alternation, uniformity, ramp),
        "gears": _gears(rows, levels, uniformity),
        "heuristic": HEURISTIC,
    }


def _bins(lengths: Sequence[float]) -> dict[str, int]:
    """The corpus histogram, every bin present — a zero is a reading, not a missing key."""
    counted = dict.fromkeys((label for label, _ in RHYTHM_BINS), 0)
    for length in lengths:
        for label, edge in RHYTHM_BINS:
            if length < edge:
                counted[label] += 1
                break
    return counted


def _ratio(lengths: Sequence[float]) -> float | None:
    if not lengths or min(lengths) <= 0:
        return None
    return _rounded(max(lengths) / min(lengths))


def _alternation(sources: Sequence[str]) -> dict[str, Any]:
    """The longest strict A/B run in the sequence of angles, counted in cuts.

    Strict means each shot returns to the one before last and differs from the one before it:
    A B A B. A third angle ends the run, and so does the same angle twice — both are variety,
    which is the thing being looked for. The run is measured in cuts rather than shots so its
    fraction is of the same denominator the report counts cuts in.
    """
    cuts = max(len(sources) - 1, 0)
    longest = 0
    run = 0
    for index in range(1, len(sources)):
        if sources[index] == sources[index - 1]:
            run = 0
            continue
        returns = index >= 2 and sources[index] == sources[index - 2]
        run = run + 1 if returns and run else 1
        longest = max(longest, run)
    counted = longest if longest >= ALTERNATION_MIN else 0
    return {
        "cuts": cuts,
        "longest_run": counted,
        "fraction": _rounded(counted / cuts) if cuts else 0.0,
    }


def _uniformity(lengths: Sequence[float], histogram: Mapping[str, int]) -> dict[str, Any]:
    """How much of the cut is one length: the fullest bin's share, and the spread around it.

    Two readings because they miss different cuts. The bin catches shots clustered at one
    length even when the numbers wobble inside it; the coefficient of variation catches a cut
    whose lengths drift slowly across a boundary and so land in two bins while never varying.
    """
    if not lengths:
        return {"bin": None, "one_bin": None, "cv": None}
    fullest = max(RHYTHM_BINS, key=lambda one: histogram[one[0]])[0]
    mean = statistics.fmean(lengths)
    return {
        "bin": fullest,
        "one_bin": _rounded(histogram[fullest] / len(lengths)),
        "cv": _rounded(statistics.pstdev(lengths) / mean) if mean > 0 else None,
    }


def _ramp(lengths: Sequence[float]) -> dict[str, Any]:
    """The longest run of shots that only shorten, or only lengthen, counted in cuts.

    A tightening ladder is a pattern the bin count and the spread are both blind to: every
    length differs, so no bin holds the cut and the coefficient of variation reads as variety,
    while what the audience sees is one rule applied over and over. Direction rather than
    slope, because the shape is the *monotony*, not the rate — a ladder that steps 10, 8, 7,
    3 is as mechanical as one that halves each time, and a single step back up ends both.

    Equal neighbours end a run rather than continue one: a stretch of identical lengths is
    already what ``uniformity`` reads, and letting it feed this one would count the same cut
    twice. Measured in cuts, like ``_alternation``, so the two fractions share a denominator.
    """
    cuts = max(len(lengths) - 1, 0)
    longest = 0
    rising = falling = 0
    for index in range(1, len(lengths)):
        rising = rising + 1 if lengths[index] > lengths[index - 1] else 0
        falling = falling + 1 if lengths[index] < lengths[index - 1] else 0
        longest = max(longest, rising, falling)
    counted = longest if longest >= RAMP_MIN else 0
    return {
        "cuts": cuts,
        "longest_run": counted,
        "fraction": _rounded(counted / cuts) if cuts else 0.0,
    }


def _metronomic(
    alternation: Mapping[str, Any], uniformity: Mapping[str, Any], ramp: Mapping[str, Any]
) -> bool:
    """``HEURISTIC``, applied. A reading it cannot take is not a reading against the cut."""
    one_bin = uniformity["one_bin"]
    cv = uniformity["cv"]
    uniform = (one_bin is not None and one_bin > ONE_BIN_FLOOR) or (
        cv is not None and cv < CV_FLOOR
    )
    ramped = float(ramp["fraction"]) > RAMP_FLOOR
    return float(alternation["fraction"]) > ALTERNATION_FLOOR and (uniform or ramped)


def _gears(
    rows: Sequence[dict[str, Any]],
    levels: Sequence[tuple[float, float]] | None,
    uniformity: Mapping[str, Any],
) -> dict[str, Any] | None:
    """How the cutting rate changes with the music's loudness — the one-speed read.

    ``None`` when there is no level curve to read, or none of it covers the cut: not looking
    and finding no gearing are different answers, and only one of them is about the edit.

    The span the terciles are taken over is the cut's own, not the mix's. A four-minute song
    inside a two-hour concert is quiet *in that concert* from end to end, and thirds taken
    over the whole mix would drop every shot in it into one gear and report nothing.

    Where the cut runs past the curve — a tail after the analysed mix ends, a cold open ahead
    of its start — those shots are counted in ``outside_shots`` and left out of the terciles
    and out of the sub-2 s numbers with them. Every rate here divides by the seconds of music
    its tercile holds, so a shot placed where no window exists is a numerator with no
    denominator, and the one it borrows is the wrong one.
    """
    if not rows or not levels:
        return None
    span_start = min(float(row["t"]) for row in rows)
    span_end = max(float(row["t"]) + float(row["seconds"]) for row in rows)
    windows = [
        (start, level)
        for start, level in levels
        if start < span_end and start + GEAR_WINDOW_SECONDS > span_start
    ]
    if not windows:
        return None

    placed = _terciles(windows)
    starts = [start for start, _ in windows]
    held: dict[str, list[dict[str, Any]]] = {QUIET: [], MID: [], LOUD: []}
    outside = 0
    for row in rows:
        window = _window_at(starts, float(row["t"]))
        if window is None:
            outside += 1
            continue
        held[placed[window]].append(row)

    terciles = {
        gear: _gear(
            held[gear],
            [level for index, (_, level) in enumerate(windows) if placed[index] == gear],
        )
        for gear in (QUIET, MID, LOUD)
    }
    ratio = _rate_ratio(terciles[LOUD]["cuts_per_minute"], terciles[QUIET]["cuts_per_minute"])
    # The short shots are the ones a fast passage is made of, so where they sit is the
    # gearing read in its bluntest form: a build that saves them for the loud third has
    # changed gear even if the averages move less than the ratio floor.
    counted = [row for gear in (QUIET, MID, LOUD) for row in held[gear]]
    short = sum(1 for row in counted if float(row["seconds"]) < SUB_TWO_SECONDS)
    in_loud = sum(1 for row in held[LOUD] if float(row["seconds"]) < SUB_TWO_SECONDS)
    return {
        "window_seconds": GEAR_WINDOW_SECONDS,
        "terciles": terciles,
        "outside_shots": outside,
        "rate_ratio": ratio,
        "sub2s_count": short,
        "sub2s_in_loud": in_loud,
        "sub2s_loud_fraction": _rounded(in_loud / short) if short else None,
        "one_speed": _one_speed(ratio, uniformity["cv"]),
        "heuristic": GEAR_HEURISTIC,
    }


def _terciles(windows: Sequence[tuple[float, float]]) -> list[str]:
    """Which third of the loudness each window sits in, by rank rather than by level range.

    Ranking, because the levels themselves are not a scale anything can be split evenly on:
    a mix mastered hot spends most of its windows inside four decibels, and thirds of *that*
    range would put the whole concert in one gear. Thirds of the windows always split the
    span three ways, so the rate in each is a rate over comparable amounts of music.

    Ties break by time, which matters only on a curve flat enough that the split is arbitrary
    — and there the ratio it produces lands near one, which is exactly what a passage whose
    loudness never moves should read as.
    """
    order = sorted(range(len(windows)), key=lambda index: (windows[index][1], windows[index][0]))
    lower, upper = len(order) // 3, (2 * len(order)) // 3
    placed = [MID] * len(windows)
    for rank, index in enumerate(order):
        placed[index] = QUIET if rank < lower else LOUD if rank >= upper else MID
    return placed


def _window_at(starts: Sequence[float], seconds: float) -> int | None:
    """The window a shot's first frame lands in, or ``None`` when the curve does not reach it.

    A shot is counted whole in the gear it was cut *into*: that is the decision the editor
    made at that frame. Splitting a long shot across two gears would credit the loud third
    with screen time nobody cut there.

    Past the ends the answer is nothing rather than the nearest window. The rates are cuts
    over the *music* each tercile holds, and a curve that stops before the cut does — or
    starts after it, on a cold open — holds no music where those shots sit; clamping them
    into the first or last window would add cuts to a numerator whose denominator never grew,
    and a cut running a minute past the analysed mix would read as a gear change nobody made.
    """
    index = bisect_right(starts, seconds) - 1
    if index < 0 or seconds >= starts[-1] + GEAR_WINDOW_SECONDS:
        return None
    return index


def _gear(shots: Sequence[dict[str, Any]], levels: Sequence[float]) -> dict[str, Any]:
    """One tercile: how much music it holds, how many shots were cut in it, and how fast.

    ``seconds`` is counted in whole windows rather than in the shots' own lengths, because
    the denominator has to be the music: a gear where one long shot runs over a minute of
    loud music has a real cutting rate, and dividing by that shot's length would report the
    rate of the shot instead.
    """
    lengths = [float(shot["seconds"]) for shot in shots]
    seconds = len(levels) * GEAR_WINDOW_SECONDS
    return {
        "seconds": _rounded(seconds),
        "shots": len(shots),
        "cuts_per_minute": _rounded(len(shots) / (seconds / 60.0)) if seconds > 0 else None,
        "median_seconds": _rounded(statistics.median(lengths)) if lengths else None,
        "level_dbfs": _rounded(statistics.median(levels)) if levels else None,
    }


def _rate_ratio(loud: Any, quiet: Any) -> float | None:
    """How much faster the loud third is cut than the quiet one.

    ``None`` when nothing was cut in the quiet third: a ratio against zero is a number the
    report cannot mean anything by, and inventing one there would read as a verdict.
    """
    if not isinstance(loud, int | float) or not isinstance(quiet, int | float) or not quiet:
        return None
    return _rounded(float(loud) / float(quiet))


def _one_speed(ratio: float | None, cv: Any) -> bool:
    """``GEAR_HEURISTIC``, applied. A reading it cannot take is not a reading against the cut."""
    if ratio is None or not isinstance(cv, int | float):
        return False
    return ratio < RATE_RATIO_FLOOR and float(cv) < GEAR_CV_FLOOR


def _usage(rows: Sequence[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    """How much of the cut each angle — or each role — actually holds.

    Counted in both shots and seconds because they answer different questions: a role can
    take a third of the cuts and a tenth of the screen time, and which of those a style
    profile should say is the director's call, not this tool's.

    Black gets its own line rather than falling in with the unlabelled: how much of a cut is
    empty is a fact about the edit, and how much of it the sidecar has not named yet is a
    fact about the sidecar. Added together neither is readable.
    """
    total = sum(float(row["seconds"]) for row in rows) or 1.0
    usage: dict[str, dict[str, Any]] = {}
    for row in rows:
        held = row[field]
        named = UNLABELLED if held is None else str(held)
        key = BLACK if row["clip"] is None else named
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


