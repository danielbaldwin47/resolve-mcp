"""Measuring a cut against the music it was cut to.

This is the instrument style is learned with (#22, story 32) and the one a build is reviewed
with (story 47). Both directions need the same numbers: for every shot, how far its start
sits from the nearest beat and from the nearest transient, where in the bar that is, which
tune it happens in, who was out front, how long the shot runs, which angle it came from and
— where the sidecar labels its subject — whether the shot is on the player out front
(`subject.py`, #181).

Two rules shape everything here.

*Nothing is decided.* An offset of two frames late is reported as two frames late, never as
"good" or "loose": what counts as musical is the style profile's business, and that is a
Markdown document Claude writes, never server code (#21). The same goes for the roles: the
angle sidecar is the agent's own document and no server path touches it (#45), so the labels
arrive here as a mapping the caller already lifted out of it.

*Composed, not computed here.* The readings a caller quotes are owned by modules of their own
and this file joins them onto the shots: `subject.py` for what a shot is framed on (#181),
`rhythm.py` for the `shot_rhythm` block and everything under it — `gears`, `quiet_floor`,
`reads_metronomic` (#215) — and `barmap.py` for a cut's place in the form (#180). What is left
here is the join: reading the timeline, putting every shot on the music's clock, and writing
the file.

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
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

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
from ..timing import dual_time, to_frames
from ..video import supers
from . import barmap, decode, energy, records, rhythm, subject
from .beats import GridTrust, nearest, spacing, trust
from .stats import histogram, measured, offsets, rounded

if TYPE_CHECKING:  # pragma: no cover - numpy and scipy load with this module, in the worker
    from ..video import picture

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

READING = 11
"""What this measurement *is*; bumped whenever a rerun over unchanged inputs would differ.

The cache is keyed on what was measured, not on the code that measured it, so a call whose
inputs have not changed is otherwise answered out of a file the previous version wrote — for
#142 that is a report with no track on its records and no ``visible`` on its header, handed
back as though it were this measurement rather than the one before it. Both the shape and
the reading count: 3 rather than 2 because the same shots now resolve to a different strip,
4 rather than 3 because the header now carries ``shot_rhythm``, 5 rather than 4 because that
block now carries ``gears``, 6 rather than 5 because ``reads_metronomic`` now reads the
ramp as well and the gearing no longer counts shots the level curve does not reach, 7
rather than 6 because the gearing now carries ``quiet_floor``, 8 rather than 7 because
every record now carries its place in the bar map, and 9 rather than 8 because every record
now carries ``delta`` and ``jump_cut``, which are ``None`` on a call that named no cut-delta
catalog — a cached hit from an earlier reading answers the self-review question with a file
that never asked it, and a cached reading-8 file would hand back a report with no such
columns at all. 10 rather than 9 for the same reason one step further on: every record now
carries the four image-quality columns and a ``quality_samples`` count (#182), and 11
rather than 10 because every record now carries ``straddles_super`` and ``super_kind`` on
the same terms (#183).
"""

DELTA_MATCH_SEC = 0.25
"""How near a cut-delta catalog row has to sit to count as this cut's.

Six frames at 24 fps. A scene detector puts a boundary within a frame or two of the edit that
made it, so the tolerance only has to absorb rounding — and it is kept too narrow to reach the
next cut on purpose. Were it wide enough to, a boundary the detector missed would silently
borrow its neighbour's number, which is the one failure here that would not look like one."""

SUPER_EDGE_FRAMES = 0.5
"""How close to a super's own edge a cut may sit and still count as outside it, in frames.

The catalog is measured off a render and the cuts are read off a timeline, so the two agree
to the frame rather than to the millisecond: the super that ended *on* the entrance it
cleared for is one rounding away from reading as landing just past it, which would report
the convention as the violation it exists to be told apart from. Half a frame rather than a
whole one so that a super genuinely carrying one frame over a cut is still a straddle —
which is why it is counted in frames of the timeline's own clock instead of in seconds, a
tolerance in seconds being a different number of frames on every project."""

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
    subjects: dict[str, subject.Subject] | None
    audio: Path | None


def correlate_timeline(
    connection: ResolveConnection,
    beats: str,
    timeline: str | None = None,
    audio: str | None = None,
    tunes: str | None = None,
    solos: str | None = None,
    deltas: str | None = None,
    supers: str | None = None,
    quality: str | None = None,
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

    ``deltas`` is a cut-delta catalog — the per-cut visual step measured off a rendered
    picture (``gauntlet/tools/ab_pack.py`` writes one as ``cuts.json``). Its ``t`` has to be
    in the same clock as this timeline's cuts, which is what a full-length render gives; a
    catalog measured off a span joins nothing, and the summary says how many rows joined
    rather than leaving a silent hole.

    ``supers`` is the other catalog off that render: when each burned-in graphic — lower
    third, title card, bug — is on screen. Every cut is measured against them and carries
    ``straddles_super`` where a graphic was up either side of it. That is a fact rather
    than a fault, and ``super_kind`` is what tells them apart: a lower third held across
    cuts is how titling works, a cut inside a title card is not. Same clock rule as
    ``deltas``, and the same summary.

    ``quality`` is an image-quality catalog — the per-sample reading ``analyze_quality``
    writes (#182). Its ``t`` has to be in the same clock as this timeline, which a scan of a
    full-length render of the cut gives; a scan of one angle's own range is in that clip's
    numbering and joins nothing. Unlike the cut deltas it is joined *over* each shot rather
    than at its boundary — the question is whether the shot held up while it was on screen,
    not what happened at the instant it came in — so a shot gets a reading whenever the scan
    covered any of it, and ``unjoined`` says how many got none.

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
    subjects = _subjects(angles)
    music = _music(beats, audio, tunes, solos, bars, roles, subjects)
    pictures = _optional_rows(deltas, "cut delta catalog", "cuts")
    graphics = _optional_rows(
        supers,
        "supers catalog",
        "supers",
        "gauntlet/tools/ab_pack.py writes one beside its cuts.json",
    )
    takes = _optional_rows(quality, "image-quality catalog", "samples", "analyze_quality writes it")
    floors = _floors(quality)
    cut = _read_cut(connection, timeline, track, music.audio, audio_at)

    params: dict[str, Any] = {
        "timeline": cut.name,
        "track": None if track is None else int(track),
        "audio_at": cut.clock.zero_frame if cut.clock.mode == GIVEN else None,
        "beats": _named(beats),
        "audio": _named(audio),
        "tunes": _named(tunes),
        "solos": _named(solos),
        "deltas": _named(deltas),
        "supers": _named(supers),
        "quality": _named(quality),
        "bars": _named(bars),
        "angles": dict(sorted(roles.items())) if roles is not None else None,
        # Carried apart from the roles so that relabelling a camera's subject — the one edit
        # that moves the on-soloist track and nothing else — is a different job rather than a
        # cache hit on the reading it replaces.
        "subjects": None if subjects is None else _labelling(subjects),
    }
    watched = [
        {"reading": READING},
        cut.fingerprint,
        *_fingerprints(beats, audio, tunes, solos, deltas, supers, quality, bars),
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
            pictures,
            graphics,
            takes,
            floors,
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
    pictures: Sequence[Mapping[str, Any]] | None = None,
    graphics: Sequence[Mapping[str, Any]] | None = None,
    takes: Sequence[Mapping[str, Any]] | None = None,
    floors: Mapping[str, float] | None = None,
) -> JobOutput:
    """The worker: transients, then one record per shot on disk and the stats inline."""
    config = config or get_config()

    progress(0.1, "reading the transients")
    transients = _transients(music.audio, onsets)

    progress(0.5, "reading the level curve")
    levels = _levels(music.audio, loudness)

    progress(0.7, "measuring the cut against the music")
    grid = trust(music.beats)
    rows = measure(shots, clock, music, transients, grid, pictures, graphics, takes)
    summary = _summary(
        rows,
        clock,
        visible,
        transients,
        [float(one["t"]) for one in music.beats],
        grid,
        levels,
        pictures,
        graphics,
        takes,
        floors,
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
    subjects: dict[str, subject.Subject] | None,
) -> Music:
    """Every file the measurement joins — or a refusal naming the one that is not there."""
    return Music(
        beats=_rows(_path(beats, "beat grid", "analyze_music writes it"), "beats"),
        tunes=_optional_rows(tunes, "tune list", "tunes"),
        solos=_optional_rows(solos, "solo changes", "solos"),
        bars=_optional_rows(bars, "bar map", "bars", "detect_bars writes it"),
        roles=roles,
        subjects=subjects,
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


def _subjects(angles: Mapping[str, Any] | None) -> dict[str, subject.Subject] | None:
    """Clip name to what that camera is framed on — the other half of the same sidecar.

    Separate from the roles because the two answer different questions and a sidecar can hold
    either alone: the role is how a cut spends its angles, the subject is who the shot is on.
    ``angles`` is validated by ``_roles``, which runs first on the same mapping.
    """
    if angles is None:
        return None
    return {
        str(clip): found
        for clip, entry in angles.items()
        if (found := subject.subject_of(entry)) is not None
    }


def _labelling(subjects: Mapping[str, subject.Subject]) -> dict[str, dict[str, str]]:
    """The subject labels as the job records them — plain JSON, in clip order."""
    return {
        clip: {"subject": one.name, "voice": one.voice} for clip, one in sorted(subjects.items())
    }


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
        for point in energy.rms_curve(decode.read(path), rhythm.GEAR_WINDOW_SECONDS)
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
    pictures: Sequence[Mapping[str, Any]] | None = None,
    graphics: Sequence[Mapping[str, Any]] | None = None,
    takes: Sequence[Mapping[str, Any]] | None = None,
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
    bar_times = barmap.times(music.bars)
    roles = music.roles or {}
    subjects = music.subjects or {}
    voices = subject.voices(music.solos)
    spans = subject.windows(music.solos)
    trusted = (grid if grid is not None else trust(music.beats)).trusted
    widths = spacing(times)
    picture_times = [float(row["t"]) for row in (pictures or ())]
    take_times = [float(row["t"]) for row in (takes or ())]

    rows: list[dict[str, Any]] = []
    for index, shot in enumerate(shots, start=1):
        seconds = clock.seconds(shot.record_in)
        framed = None if shot.clip is None else subjects.get(shot.clip)
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
        rows.append(
            {
                "cut": index,
                "clip": shot.clip,
                "track": shot.track,
                "role": None if shot.clip is None else roles.get(shot.clip),
                # What the shot is on, and who was playing while it held (#181). The subject
                # is the sidecar's; the join against the solo map is measured over the shot's
                # whole length, because a shot that outlives the solo it opened in is two
                # facts and reading the front at the cut alone records only the first.
                "subject": None if framed is None else framed.name,
                **subject.reading(
                    framed,
                    voices,
                    seconds,
                    seconds + shot.duration / clock.fps,
                    spans,
                    black=shot.clip is None,
                ),
                "opening": _opens(index, shot, shots),
                "t": rounded(seconds),
                # Dual time for the two timeline positions, because an outlier the agent
                # flags is one the director then has to find in Resolve, and a bare frame
                # number is the one form nobody can scrub to.
                "in": dual_time(shot.record_in, clock.fps),
                "out": dual_time(shot.record_out, clock.fps),
                "seconds": rounded(shot.duration / clock.fps),
                "beat_offset": None if beat is None else rounded(seconds - float(beat["t"])),
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
                # where that bar sits in the four-bar group, and how far off the line it is
                # (``barmap``, #180).
                **barmap.reading(music.bars, bar_times, seconds),
                "transient_offset": _offset(transients, seconds),
                "tune": _tune_at(music.tunes, tune_times, seconds),
                "front": _front_at(music.solos, solo_times, seconds),
                # How far the picture steps at this cut, joined from a catalog measured off
                # the render (#184). ``None`` when no catalog was named, and also when one
                # was but has nothing within reach of this cut — a cut the scene detector
                # missed has no delta, and inventing one from the nearest boundary a second
                # away would be a number about a different edit.
                **_delta_at(pictures, picture_times, seconds),
                # Whether a burned-in graphic is on screen either side of this cut (#183).
                # Not "is a super up here": a super that arrives with this shot, or clears
                # for it, is the convention rather than the fault, and the interval is read
                # strictly at both ends so those two land outside it.
                **_super_at(graphics, seconds, SUPER_EDGE_FRAMES / clock.fps),
                # How the picture held up while this shot was on screen (#182), joined from
                # an image-quality scan of the render. Measured over the shot rather than at
                # its boundary: a take that goes soft halfway through is soft, and reading
                # only the first frame of it would report the take before the focus slipped.
                **_quality_over(
                    takes, take_times, seconds, seconds + shot.duration / clock.fps
                ),
            }
        )
    return rows


def _super_at(
    graphics: Sequence[Mapping[str, Any]] | None,
    seconds: float,
    guard: float,
) -> dict[str, Any]:
    """Whether this cut lands inside a burned-in graphic, and which kind of one.

    Strict at both ends on purpose. A super whose first frame *is* this cut arrived with the
    new shot, and one whose last frame is the frame before it cleared for the shot — the two
    edits the human's own deliverables are made of, and an inclusive test would report both
    of those conventions as findings.

    What the column means is left to the reader, which is why ``super_kind`` rides beside
    it: measured on the corpus, a lower third held across a cut is ordinary craft and a cut
    inside a title card is not.
    """
    if not graphics:
        return {"straddles_super": None, "super_kind": None}
    kinds = []
    for row in graphics:
        start, end = row.get("t"), row.get("end")
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            continue
        if supers.inside(float(start), float(end), seconds, guard):
            kinds.append(str(row.get("kind") or ""))
    if not kinds:
        return {"straddles_super": False, "super_kind": None}
    # A cut can sit inside two supers at once, and then the card is the one worth naming:
    # taking whichever row came first would let catalog order decide whether a report shows
    # the finding or the lower third that happened to be up over it.
    kind = supers.CARD if supers.CARD in kinds else kinds[0]
    return {"straddles_super": True, "super_kind": kind or None}


def _quality_over(
    takes: Sequence[Mapping[str, Any]] | None,
    times: Sequence[float],
    seconds: float,
    until: float,
) -> dict[str, Any]:
    """The image-quality catalog rows this shot covers, as four columns and a count.

    Aggregated the same way ``video.picture`` summarises a stretch, and for the reasons given
    there: middles for sharpness and exposure, which are properties of the take, and the worst
    moment for clipping and stability, which are the two a shot is only as good as.

    A shot no sample landed inside is left null rather than borrowing its neighbour's
    reading. A scan is sampled several times a second, so the only shots this loses are ones
    shorter than a sample interval — and inventing a number for them would be inventing it
    for exactly the cuts a fast passage is made of.
    """
    empty = {
        "sharpness": None,
        "exposure": None,
        "clipped": None,
        "stability": None,
        "quality_samples": 0,
    }
    if not takes:
        return empty
    first, last = bisect_left(times, seconds), bisect_left(times, until)
    inside = [takes[index] for index in range(first, last)]
    if not inside:
        return empty

    # The aggregation rule itself belongs to ``video.picture`` and is taken from there rather
    # than repeated: a shot in a correlate report and a shot in a blind pack have to be the
    # same number, and two copies of a rule are two rules waiting to disagree. The import is
    # here rather than at module scope because numpy and scipy load with that module, and this
    # is the one path in this file that needs them (and only when a catalog was named).
    from ..video.picture import summarize  # noqa: PLC0415 - see above

    summary = summarize([_scanned(one) for one in inside])
    return {
        "sharpness": summary["sharpness"],
        "exposure": summary["exposure"],
        "clipped": summary["clipped"],
        "stability": summary["stability"],
        "quality_samples": len(inside),
    }


def _scanned(row: Mapping[str, Any]) -> picture.Reading:
    """One catalog row read back as the reading that wrote it.

    The catalog is JSON on disk, and the aggregation lives in a module that knows nothing
    about files, so something has to carry a row across that line. This is the only place
    that knows both shapes.

    Every column is read with a default rather than by subscript. A row that is missing one is
    a hand-edited or older catalog, and the job's answer to that should be a reading with a
    hole in it — not a ``KeyError`` from inside a worker, two seconds after a call that looked
    like it had been accepted.
    """
    from ..video.picture import Reading  # noqa: PLC0415 - numpy loads with that module

    return Reading(
        sharpness=_number(row, "sharpness"),
        exposure=_number(row, "exposure"),
        contrast=_number(row, "contrast"),
        clipped=_number(row, "clipped"),
        crushed=_number(row, "crushed"),
        stability=None if row.get("stability") is None else float(row["stability"]),
        discontinuity=bool(row.get("discontinuity")),
    )


def _number(row: Mapping[str, Any], field: str) -> float:
    found = row.get(field)
    return float(found) if isinstance(found, int | float) else 0.0


def _delta_at(
    pictures: Sequence[Mapping[str, Any]] | None,
    times: Sequence[float],
    seconds: float,
) -> dict[str, Any]:
    """The catalog row that lands on this cut, as ``delta`` and ``jump_cut`` columns."""
    if not pictures:
        return {"delta": None, "jump_cut": None}
    found = nearest(times, seconds)
    if found is None or abs(times[found] - seconds) > DELTA_MATCH_SEC:
        return {"delta": None, "jump_cut": None}
    row = pictures[found]
    # Two shapes reach here: the pack's ``cuts.json``, where the reading is nested under
    # ``delta``, and a flat catalog that spreads the same fields across the row.
    reading = row.get("delta")
    if isinstance(reading, Mapping):
        return {"delta": reading.get("delta"), "jump_cut": reading.get("jump_cut")}
    return {"delta": reading, "jump_cut": row.get("jump_cut")}


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
    return None if found is None else rounded(seconds - times[found])


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
    pictures: Sequence[Mapping[str, Any]] | None = None,
    graphics: Sequence[Mapping[str, Any]] | None = None,
    takes: Sequence[Mapping[str, Any]] | None = None,
    floors: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """The list-free reading: what a style profile is written from, and a self-review read.

    Every stat is taken over the records as they were written, not over the unrounded
    arithmetic behind them, so the gist and the file never disagree: an agent that greps the
    file and averages the column gets the number it was already told.

    ``shot_rhythm`` is the one reading here that is about the cut rather than about the music
    — the metronome check a build cannot perform on itself by looking at its offsets, and the
    gearing check that joins the two. It is ``rhythm.read``'s, as ``bar_groups`` and
    ``bar_offsets`` are ``barmap.summary``'s and ``on_soloist`` is ``subject.summary``'s: this
    function decides which readings the report carries, not what any of them says.
    """
    cut_to_music = [row for row in rows if not row["opening"]]
    # The gate is applied here and nowhere else (#112). Transients need no grid, so they are
    # measured over every cut; the beat and bar statistics are taken over the cuts the grid
    # can actually describe, and the count of the rest is reported rather than swallowed.
    in_grid = [row for row in cut_to_music if row["in_grid"]]
    stranded = sum(1 for row in cut_to_music if row["stranded"])
    transient_offsets = (
        None if transients is None else offsets([row["transient_offset"] for row in cut_to_music])
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
        "beat_offsets": offsets([row["beat_offset"] for row in in_grid]),
        "transient_offsets": transient_offsets,
        "bars": histogram(row["in_bar"] for row in in_grid),
        # ``bar_groups`` and ``bar_offsets``, from the join that owns them (``barmap``).
        **barmap.summary(rows, cut_to_music),
        "tunes": _spread("tune", cut_to_music) if measured("tune", rows) else None,
        "solos": _spread("front", cut_to_music) if measured("front", rows) else None,
        "shot_seconds": _lengths([row["seconds"] for row in rows]),
        "shot_rhythm": rhythm.read(rows, levels),
        # Keyed on whether a catalog was *named*, not on whether anything joined: a catalog
        # that lines up with nothing is the failure this block is here to make loud, and
        # answering None would report it as a call that never asked.
        "visual_delta": None if pictures is None else _deltas(rows, pictures),
        "supers": None if graphics is None else _supers(rows, graphics),
        # Keyed the same way and for the same reason: a catalog measured off a different span
        # joins nothing, and a null here would report that as a call that never asked.
        "picture_quality": None if takes is None else _quality(rows, takes, floors),
        "clips": _usage(rows, "clip"),
        "roles": _usage(rows, "role") if measured("role", rows) else None,
        "subjects": _usage(rows, "subject") if measured("subject", rows) else None,
        # Taken over every shot, openings included: the question is where the screen time
        # went, and a shot that starts the film is screen time like any other (#181).
        "on_soloist": subject.summary(rows),
    }


def _deltas(
    rows: Sequence[dict[str, Any]], pictures: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """How far the picture steps across this cut's boundaries, over the whole timeline.

    ``unjoined`` is the number the reading lives or dies by. A catalog measured off a
    different span, or off a render whose cuts the detector missed, still produces a
    ``visual_delta`` block — one drawn from a handful of rows that happened to land. Said
    out loud, that is a mismatch to fix; left out, it is a distribution nobody doubts.
    """
    found = [float(row["delta"]) for row in rows if row["delta"] is not None]
    flagged = [row for row in rows if row["jump_cut"]]
    return {
        "catalog_rows": len(pictures),
        "joined": len(found),
        "unjoined": len(rows) - len(found),
        "jump_cuts": len(flagged),
        "flagged_cuts": [row["cut"] for row in flagged[:INLINE_CUTS]],
        "delta": _lengths([round(one, 4) for one in found]) if found else None,
        "match_tolerance_sec": DELTA_MATCH_SEC,
    }


def _supers(
    rows: Sequence[dict[str, Any]], graphics: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The burned-in graphics this cut was measured against, and what it did to them.

    ``straddled`` is the finding; the rest is what says whether to believe it. A catalog
    measured off a different render joins nothing and would report a clean bill of health
    from a measurement that never met this timeline, so the number of supers it holds and
    the span they cover are reported beside the count of violations.
    """
    flagged = [row for row in rows if row.get("straddles_super")]
    return {
        "catalog_rows": len(graphics),
        "cards": sum(1 for row in graphics if row.get("kind") == supers.CARD),
        "overlays": sum(1 for row in graphics if row.get("kind") == supers.OVERLAY),
        "straddled": len(flagged),
        # Split, because they are different claims. A lower third held across a cut is how
        # titling works and the human deliverables are full of them; a cut inside a title
        # card — a graphic that is itself the shot — is the one nothing in the corpus does.
        "straddled_cards": sum(1 for row in flagged if row.get("super_kind") == supers.CARD),
        "straddled_overlays": sum(
            1 for row in flagged if row.get("super_kind") == supers.OVERLAY
        ),
        "flagged_cuts": [row["cut"] for row in flagged[:INLINE_CUTS]],
        "covered_sec": _covered(graphics),
    }


def _covered(graphics: Sequence[Mapping[str, Any]]) -> float:
    """How much of the timeline has a graphic on it, counting overlap once.

    Summed row by row it would not be that: two supers up together — a bug over a lower
    third — would report more seconds of graphic than the piece has seconds.
    """
    spans = sorted(
        (float(row["t"]), float(row["end"]))
        for row in graphics
        if isinstance(row.get("t"), int | float) and isinstance(row.get("end"), int | float)
    )
    total, reached = 0.0, float("-inf")
    for opens, closes in spans:
        total += max(0.0, closes - max(opens, reached))
        reached = max(reached, closes)
    return round(total, 3)


def _quality(
    rows: Sequence[dict[str, Any]],
    takes: Sequence[Mapping[str, Any]],
    floors: Mapping[str, float] | None,
) -> dict[str, Any]:
    """How the picture held up across the cut, shot by shot (#182).

    ``unjoined`` is what this reading lives or dies by, exactly as it is for the cut deltas: a
    catalog scanned off one angle's own range, or off a render of a different length, still
    produces a block — one drawn from whichever handful of shots happened to land inside it.

    The three lists are the actionable part. A distribution says the cut is fine on average,
    which is not a thing anybody watches; the shots that missed a floor are the ones to look
    at, named by cut number so the builder can find them in the report and in Resolve.
    """
    joined = [row for row in rows if row["quality_samples"]]
    return {
        "catalog_rows": len(takes),
        "joined": len(joined),
        "unjoined": len(rows) - len(joined),
        "floors": None if floors is None else dict(floors),
        "sharpness": _lengths([float(row["sharpness"]) for row in joined]),
        "exposure": _lengths([float(row["exposure"]) for row in joined]),
        "clipped": _lengths([float(row["clipped"]) for row in joined]),
        "stability": _lengths(
            [float(row["stability"]) for row in joined if row["stability"] is not None]
        ),
        "soft_shots": _missing(joined, floors, "sharpness", "min_sharpness", below=True),
        "blown_shots": _missing(joined, floors, "clipped", "max_clipped", below=False),
        "shaky_shots": _missing(joined, floors, "stability", "min_stability", below=True),
    }


def _missing(
    rows: Sequence[dict[str, Any]],
    floors: Mapping[str, float] | None,
    field: str,
    floor: str,
    below: bool,
) -> dict[str, Any] | None:
    """The cuts that missed one floor — ``None`` when the catalog named no floor to miss.

    The count travels with the list because the list is capped. Twelve cut numbers and no
    total reads as twelve bad shots whether there were twelve or forty, and the difference
    between those two is the difference between fixing a few shots and rebuilding the cut.
    """
    if floors is None or floor not in floors:
        return None
    limit = float(floors[floor])
    missed = [
        int(row["cut"])
        for row in rows
        if row[field] is not None
        and (float(row[field]) < limit if below else float(row[field]) > limit)
    ]
    return {"count": len(missed), "floor": limit, "cuts": missed[:INLINE_CUTS]}


def _floors(file: str | None) -> dict[str, float] | None:
    """The floors an image-quality catalog was scanned against, off its own header.

    Read from the file rather than defaulted here on purpose: a catalog scanned with a floor
    the director set is the only thing that can say which shots missed it, and re-deriving the
    answer from this module's idea of a default would report shots against a rule nobody ran.
    A catalog without the header — a hand-built one, an older scan — reports no floors and no
    lists, rather than lists measured against a guess.
    """
    if file is None:
        return None
    doc = json.loads(Path(file).expanduser().read_text(encoding="utf-8"))
    held = doc.get("floors") if isinstance(doc, Mapping) else None
    if not isinstance(held, Mapping):
        return None
    return {str(name): float(value) for name, value in held.items()}


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




def _lengths(seconds: Sequence[float]) -> dict[str, Any]:
    if not seconds:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": rounded(statistics.fmean(seconds)),
        "median": rounded(statistics.median(seconds)),
        "min": rounded(min(seconds)),
        "max": rounded(max(seconds)),
    }






















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
        entry["share"] = rounded(entry["seconds"] / total)
        entry["seconds"] = rounded(entry["seconds"])
    return usage


def _spread(field: str, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How the cuts fall across the tunes, or across who was in front."""
    placed = [row[field] for row in rows if row[field] is not None]
    return {
        "covered": len(set(placed)),
        "outside": len(rows) - len(placed),
        "cuts": histogram(iter(placed)),
    }
