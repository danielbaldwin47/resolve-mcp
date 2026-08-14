"""Materialising a cut file: a sequential V1 and anchored overlays, over one master mix.

The one write primitive Resolve gives us is append-with-exact-placement, and the #18 spike
found it full of silent failures. Everything in this file exists to make one guarantee:
*either the timeline holds the cut exactly, or the build says why it does not.*

* **Nothing starts on a file that will not build.** The same rules ``validate_cut`` runs
  run here first, over the same pool reading, and a single error aborts before a timeline
  is created — a timeline missing part of its own cut is the half-built outcome the
  pre-flight exists to prevent.
* **Overlays are placed, never positioned.** An overlay states an anchor entry and an
  offset, so its record frame is computed from the same layout E9 validated against
  (``overlay_positions``); the track it lands on is its own ``track``, V2 by default.
  Re-timing an earlier segment and rebuilding therefore leaves every overlay over the
  content it was anchored to, which is the whole point of the anchor: an absolute frame
  would have to be re-authored on every tightening pass.
* **A gap is an append that does not happen.** Black on V1 takes record time and places
  nothing, so it appears here only as the reason the next shot's ``recordFrame`` jumps —
  which is precisely the unclamped-``recordFrame`` case below, and why the read-back
  matters more for a cut with gaps than for one without.
* **A new version every time.** The name is ``<base> v<N+1>`` scanned off the project's
  existing names, so no build ever writes into a timeline someone has already reviewed.
* **A tail is built twice, because the API cannot cut a transition.** A cut whose ``tail``
  has a transition to cut in is appended to ``<base> v<N+1> (tail staging)`` and
  round-tripped through OTIO into its real name (:mod:`resolve_mcp.resolve.tail`); a hard
  out that does not fade the mix has nothing to inject and builds directly. Everything
  after the round trip — the placement read-back, takes, markers — is done on the timeline
  that came *back*, and a round trip that fails fails the build rather than delivering a
  cut with a hard edge where its tail should be.
* **Resolve's answer is never the evidence.** An append onto a locked track returns
  TimelineItems and places nothing; an append that overlaps existing media slides to the
  next free frame and reports success; a still ignores ``endFrame`` until an out point has
  been written to it once. So tracks are checked for locks before the append, stills are
  unlocked before it, and every placement is read back off the track afterwards.
* **Record frames are absolute.** ``recordFrame`` counts from the timeline's own start
  (an hour of timecode on a normal project) and is not clamped — a cut frame of 0 would
  land before the timeline begins. Every position is the timeline start plus the cut
  offset, and ``mediaType`` always travels with ``trackIndex``, which Resolve otherwise
  honours by dropping the clip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..cut import tail as cut_tail
from ..cut.validate import gaps as cut_gaps
from ..cut.validate import (
    is_gap,
    locked_track_finding,
    overlay_positions,
    overlay_track,
    placements,
)
from ..errors import BuildFailedError, CutInvalidError, TimelineNotFoundError
from ..logging_config import get_logger
from ..naming import latest_version, next_version_name, version_name
from . import cut, markers, media, mix, takes
from . import tail as tail_route
from . import timeline as timeline_read
from .connection import ResolveConnection
from .session import frame_rate

log = get_logger("build")

Clip = Any
Pool = Any
Project = Any
Timeline = Any

MEDIA_TYPES: Final[dict[str, int]] = {"video": 1, "audio": 2}
"""Resolve's ``mediaType``: 1 appends video only, 2 audio only. Never omitted."""

TRACK_PREFIXES: Final[dict[str, str]] = {"video": "V", "audio": "A"}
"""Also the order tracks are reported in, so every log and failure detail reads the same."""

AUDIO_TRACK_TYPE: Final = "stereo"
"""What a master mix is. ``AddTrack`` defaults to mono, which would fold a stereo mix."""

MISPLACED_CAP: Final = 20
"""A drifted build misplaces everything downstream of the blockage; the count says how many."""

REFUSED_MARKER_CAP: Final = 20
"""Carried markers that would not land are listed, not just counted — up to this many."""


@dataclass(frozen=True)
class Track:
    """One target track: a media type and a 1-based index, which never travel apart.

    Resolve drops an append naming a ``trackIndex`` without its ``mediaType``, and a lock
    check or a read-back is meaningless without both — so the pair is one value here.
    """

    type: str
    index: int

    @property
    def label(self) -> str:
        """What the timeline header calls it — the only name an agent or director sees."""
        return f"{TRACK_PREFIXES[self.type]}{self.index}"


V1: Final = Track("video", 1)
"""The sequential cut: segments in document order, with black where a gap says so.

The only track this module names. An overlay's is whatever its ``track`` says, defaulting
to V2 in ``overlay_track`` — one definition of "the layer above the cut", in the module
that validates it, rather than a constant here that could drift from the rule.
"""

A1: Final = Track("audio", 1)
"""One continuous master mix under the whole cut."""


@dataclass(frozen=True)
class Shot:
    """One append: which frames of which clip land where, on which track."""

    id: str
    track: Track
    clip: Clip
    name: str
    source_in: int
    record: int
    duration: int

    @property
    def source_out(self) -> int:
        """Half-open, and exactly what Resolve's ``endFrame`` means (#18 spike (a))."""
        return self.source_in + self.duration

    def clip_info(self) -> dict[str, Any]:
        return {
            "mediaPoolItem": self.clip,
            "startFrame": self.source_in,
            "endFrame": self.source_out,
            "mediaType": MEDIA_TYPES[self.track.type],
            "trackIndex": self.track.index,
            "recordFrame": self.record,
        }


def build_timeline(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
    carry_markers: bool = True,
) -> dict[str, Any]:
    """Build ``cut_file`` as a fresh ``<name> v<N>`` timeline and report what landed."""
    checked = cut.preflight(connection, cut_file, min_segment_frames)
    if checked.errors:
        raise CutInvalidError(
            cause=f"The cut file has {len(checked.errors)} error(s), so nothing was built.",
            detail={
                "cut_file": str(checked.loaded.path),
                "content_hash": checked.loaded.content_hash,
                "errors": [finding.as_dict() for finding in checked.errors],
                "warnings": [finding.as_dict() for finding in checked.warnings],
            },
        )
    # E1 is an error, so a pre-flight with no errors has a document that parsed and matched
    # the schema — every read of it below is a field the rules have already been over.
    doc: dict[str, Any] = checked.loaded.doc

    project = timeline_read.open_project(connection)
    pool = media.media_pool(connection)
    base = str(doc["timeline"]["name"])
    existing = timeline_read.timeline_names(project)
    # Read before the build, because creating the new version is what makes it the latest:
    # the version being superseded is the one holding the markers a human placed by hand.
    superseded = latest_version(base, existing)
    name = next_version_name(base, existing)
    clips = cut.clips_by_alias(checked)
    _unlock_stills(clips)

    # A cut whose tail has something to cut in is appended to a staging timeline and
    # round-tripped into its own name, because the scripting API cannot cut a transition
    # (see :mod:`.tail`). A hard out that does not fade the mix has nothing to inject, so it
    # builds straight into its own name rather than paying an export and an import to hand
    # back the same cut. Every step below therefore names the timeline it is actually
    # writing to, which for a round-tripped build is the staging one until the import lands.
    tail = cut_tail.read(doc)
    round_tripped = tail is not None and tail.needs_transitions
    writing = tail_route.staging_name(name, existing) if round_tripped else name

    built = _create(pool, project, writing)
    # The frame the shots are positioned against. Kept, rather than re-read per check: a
    # round-tripped build reads its placements back on a *second* timeline, whose own start
    # is Resolve's to choose, and the comparison there is offset against offset.
    origin = timeline_read.start_frame(built)
    shots = _shots(doc, clips, origin)
    _make_tracks(built, shots, writing)
    _refuse_locked_tracks(built, shots, writing)
    _append(pool, shots, writing)
    _verify(built, shots, writing, origin)
    applied: dict[str, Any] | None = None
    if tail is not None and round_tripped:
        # Before takes: the round trip makes a new timeline, and a selector attached to the
        # staging one would be alternates for a cut that is about to be deleted. ``_verify``
        # goes with it: the timeline checked above is the staging one, and the cut that
        # ships is the one the import made out of it.
        built, applied = tail_route.materialise(
            connection,
            project,
            pool,
            built,
            writing,
            name,
            tail,
            verify=lambda landed: _verify(landed, shots, name, origin),
        )
    elif tail is not None:
        # Nothing was injected and nothing was round-tripped, but the cut file did ask for a
        # tail — so the report says what it asked for and that it took no route.
        applied = {
            **tail.as_dict(),
            "video_tracks": [],
            "audio_tracks": [],
            "route": "direct",
            "confirmed": [],
        }
    # Takes hang off placed clips, so they are attached only once every placement has been
    # read back — a selector on a shot that slid somewhere else would be alternates for a
    # shot the cut file does not have. Read against the timeline actually being attached to,
    # which after a round trip is the import and need not start where the staging cut did.
    made = takes.attach_takes(
        built, _selectors(doc, clips, shots, timeline_read.start_frame(built) - origin), name
    )
    carried = _carry_markers(
        connection,
        project,
        built,
        name,
        version_name(base, superseded) if superseded else None,
        carry_markers,
    )

    log.info("Built %s: %d clips from %s", name, len(shots), checked.loaded.content_hash)
    return {
        "cut_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "timeline": timeline_read.summarise(timeline_read.Reader(connection), built, project, name),
        "placed": {
            "segments": _count(shots, V1),
            # Read off the document rather than the track, because black is the one thing
            # here that has no item to read back. What proves the hole is real is the
            # shots on either side of it: ``_verify`` has already confirmed they landed on
            # the record frames the gap put them at.
            "gaps": len(cut_gaps(doc)),
            "overlays": _overlay_count(shots),
            "audio": bool(_count(shots, A1)),
            "selectors": made,
        },
        # None when the cut has no tail — the hard out v1 always built. Never a bare
        # "false": what landed is the frame counts, and the report is where a review round
        # reads back whether the device it asked for is the device that arrived.
        "tail": applied,
        "markers": carried,
        "warnings": [finding.as_dict() for finding in checked.warnings],
    }


def _count(shots: list[Shot], track: Track) -> int:
    """How many of this build's appends one track took — segments and overlays are both V."""
    return sum(1 for shot in shots if shot.track == track)


def _overlay_count(shots: list[Shot]) -> int:
    """Every video append above the cut's own track, however many layers they spread over."""
    return sum(1 for shot in shots if shot.track.type == "video" and shot.track != V1)


# --- markers --------------------------------------------------------------------------------


def _carry_markers(
    connection: ResolveConnection,
    project: Project,
    built: Timeline,
    name: str,
    previous: str | None,
    asked: bool,
) -> dict[str, Any]:
    """Move the superseded version's markers onto the new one, through the mix under both.

    Blue markers name the songs a titles file anchors its events to, and a human places
    them by hand. A rebuild makes an *empty* timeline, so before #130 every rebuild cost
    another pass of hand re-marking every song boundary before the titles file could be
    re-applied — the one manual step left in that loop.

    The carry is a derivation, not a copy. Record frames are exactly what a rebuild moves:
    re-time one segment and everything after it slides, which is the whole reason a build
    makes a new version. What does not move is the master mix — one continuous clip nobody
    re-times — so a marker is carried by *the frame of the mix it sat over*, and the two
    versions' readings of that (:mod:`resolve_mcp.resolve.mix`) differ by one constant.

    A cut with no mix under it has no such shared axis. There is no honest answer for where
    its markers go, so nothing is carried and the report says why: markers landing at
    positions that merely look plausible would be worse than markers a human knows are
    missing. Same for a previous version whose mix is a different clip, or two clips of the
    same name — an anchor that cannot be identified is not an anchor.
    """
    if not asked:
        return _no_carry("carry_markers was off, so the earlier version's markers stayed there.")
    if previous is None:
        return _no_carry("Nothing to carry: this is the first version of this cut.")

    reader = timeline_read.Reader(connection)
    try:
        earlier = timeline_read.find_timeline(project, previous)
    except TimelineNotFoundError:
        # The name came off this project moments ago, so this is a timeline deleted mid-build
        # rather than a mistake — and a build that placed every clip must not fail over it.
        return _no_carry(f"{previous} was gone by the time its markers were read.", previous)

    found = markers.markers_on(connection, earlier, frame_rate(project, earlier))
    if not found:
        return _no_carry(f"{previous} carries no markers.", previous)

    here = mix.anchor(mix.audio_shots(reader, built))
    if here is None:
        return _no_carry(
            f"This cut has no single master mix under it, so it shares no axis with "
            f"{previous}: its {len(found)} marker(s) have to be placed by hand.",
            previous,
        )
    there = mix.anchor(mix.audio_shots(reader, earlier), here.name)
    if there is None:
        return _no_carry(
            f"{previous} does not agree where {here.name} starts under it, so there is no "
            f"reading of where its {len(found)} marker(s) sit in the mix.",
            previous,
        )

    shift = here.zero_frame - there.zero_frame
    entries = [_write_entry(marker, shift) for marker in found]
    results = markers.set_markers(connection, entries, name=name)["results"]
    refused = [
        {
            "name": entry["name"],
            "color": entry["color"],
            "record": entry["frame"],
            "error": result.get("error"),
        }
        for entry, result in zip(entries, results, strict=True)
        if not result.get("ok")
    ]
    log.info(
        "Carried %d of %d markers from %s onto %s, shifted %+d frames",
        len(entries) - len(refused),
        len(entries),
        previous,
        name,
        shift,
    )
    landed = [entry for entry, result in zip(entries, results, strict=True) if result.get("ok")]
    return {
        "carried": len(landed),
        "skipped": len(refused),
        "from": previous,
        "shift": shift,
        # Not decoration: the carry is exact for a marker that means a *musical* moment and
        # only approximate for one that means a *picture* moment, and the two are told apart
        # by colour alone. Blue names a song and rides the mix exactly; a director's coloured
        # note was put over a shot, and this rebuild is what moved the shots. Splitting the
        # count is what lets an agent re-read the notes without re-reading the anchors.
        "by_color": _colours(landed),
        # A marker whose moment was cut out of this version lands outside the timeline and is
        # refused by name, because "which song lost its marker" is the actionable half.
        "refused": refused[:REFUSED_MARKER_CAP],
        "reason": None,
    }


def _no_carry(reason: str, source: str | None = None) -> dict[str, Any]:
    """The same shape as a carry that happened — a reader parses one block, not two."""
    return {
        "carried": 0,
        "skipped": 0,
        "from": source,
        "shift": None,
        "by_color": {},
        "refused": [],
        "reason": reason,
    }


def _colours(entries: list[dict[str, Any]]) -> dict[str, int]:
    """How many of each colour came across, the same histogram ``list_markers`` reports."""
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["color"]] = counts.get(entry["color"], 0) + 1
    return counts


def _write_entry(marker: dict[str, Any], shift: int) -> dict[str, Any]:
    """One read marker as the write that puts it over the same moment of the mix."""
    return {
        "frame": marker["record"]["frames"] + shift,
        "color": marker["color"],
        "name": marker["name"],
        "note": marker["note"],
        "duration": marker["duration"]["frames"],
        "custom_data": marker["custom_data"],
    }


# --- placement ------------------------------------------------------------------------------


def _shots(doc: dict[str, Any], clips: dict[str, media.LocatedClip], start: int) -> list[Shot]:
    """Every append the cut asks for, positioned absolutely from the timeline start."""
    placed = placements(doc, start)
    shots = []
    for segment in doc["segments"]:
        if is_gap(segment):
            # Black is the absence of a clip, so a gap is an append that does not happen.
            # It is still in ``placed``, which is what makes the next shot start late: the
            # hole comes from the record frames on either side of it, never from a clip.
            continue
        id = str(segment["id"])
        record, duration = placed[id]
        shots.append(
            _shot(
                id=id,
                track=V1,
                located=clips[str(segment["source"])],
                source_in=int(segment["in"]),
                record=record,
                duration=duration,
            )
        )
    anchored = overlay_positions(doc)
    for overlay in doc.get("overlays") or []:
        # The anchor resolves against the layout above, so an overlay is positioned by the
        # cut it covers rather than by a frame number anyone had to keep up to date.
        id = str(overlay["id"])
        offset, duration = anchored[id]
        shots.append(
            _shot(
                id=id,
                track=Track("video", overlay_track(overlay)),
                located=clips[str(overlay["source"])],
                source_in=int(overlay["in"]),
                record=start + offset,
                duration=duration,
            )
        )
    audio = doc.get("audio")
    if isinstance(audio, dict):
        # One continuous clip under the whole cut: the substrate the segments are laid over,
        # and the reason a concert cut stays in sync with the mix the director approved.
        shots.append(
            _shot(
                id="audio",
                track=A1,
                located=clips[str(audio["source"])],
                source_in=int(audio["in"]),
                record=start,
                duration=int(audio["out"]) - int(audio["in"]),
            )
        )
    return shots


def _shot(
    id: str,
    track: Track,
    located: media.LocatedClip,
    source_in: int,
    record: int,
    duration: int,
) -> Shot:
    return Shot(
        id=id,
        track=track,
        clip=located.clip,
        name=_clip_name(located),
        source_in=source_in,
        record=record,
        duration=duration,
    )


def _clip_name(located: media.LocatedClip) -> str:
    """What to call the clip in a failure — read once, since a dead handle answers nothing."""
    return str(located.clip.GetName() or "")


def _selectors(
    doc: dict[str, Any],
    clips: dict[str, media.LocatedClip],
    shots: list[Shot],
    shift: int = 0,
) -> list[takes.Selector]:
    """The alternates each segment carries, against the record frame its shot landed on.

    ``shift`` is how far the timeline being attached to starts from the one the shots were
    positioned against — zero on a direct build, and whatever a round trip's import chose
    for itself otherwise. A selector is found by its record frame, so a shift left out here
    would look for every shot an hour before the cut that holds it.
    """
    records = {shot.id: shot.record + shift for shot in shots if shot.track == V1}
    found = []
    for segment in doc["segments"]:
        alternates = segment.get("alternates") or []
        if not alternates:
            continue
        found.append(
            takes.Selector(
                segment=str(segment["id"]),
                record=records[str(segment["id"])],
                takes=tuple(_take(alternate, clips) for alternate in alternates),
            )
        )
    return found


def _take(alternate: dict[str, Any], clips: dict[str, media.LocatedClip]) -> takes.Take:
    source = str(alternate["source"])
    located = clips[source]
    return takes.Take(
        source=source,
        clip=located.clip,
        name=_clip_name(located),
        source_in=int(alternate["in"]),
        duration=int(alternate["out"]) - int(alternate["in"]),
    )


# --- the writes -------------------------------------------------------------------------------


def _create(pool: Pool, project: Project, name: str) -> Timeline:
    """The empty timeline this build fills, made current so appends can reach it."""
    created = pool.CreateEmptyTimeline(name)
    if not created:
        raise BuildFailedError(
            cause=f"Resolve refused to create the timeline {name!r}.",
            detail={"timeline": name},
        )
    if not project.SetCurrentTimeline(created):
        # AppendToTimeline writes to whatever is current, so a failed switch would build
        # the cut into someone else's timeline. Stop while the new one is still empty.
        raise BuildFailedError(
            cause=f"Resolve created {name!r} but would not open it, so nothing was appended.",
            fix="Close any modal dialog in the Resolve GUI, delete the empty timeline, and "
            "build again.",
            detail={"timeline": name},
        )
    log.info("Created and opened timeline %s", name)
    return created


def _make_tracks(timeline: Timeline, shots: list[Shot], name: str) -> None:
    """Pre-create the tracks the cut needs: an index past the next free one is dropped.

    A track this build adds is made stereo, because ``AddTrack`` defaults to mono and a
    master mix folded to one channel is a silent wrong answer. A track the new timeline
    came with keeps whatever the project's timeline template gave it — the API cannot
    restyle an existing track, so the layout is logged rather than assumed.
    """
    for track in _tracks(shots):
        # Only an audio track takes a sub-type; passing one to video is meaningless.
        extra = (AUDIO_TRACK_TYPE,) if track.type == "audio" else ()
        while _track_count(timeline, track.type) < track.index:
            if not timeline.AddTrack(track.type, *extra):
                # Refused rather than merely unanswered: retrying would spin forever, and
                # appending to a track that is not there drops the clip silently.
                raise BuildFailedError(
                    cause=f"Resolve refused to add a {track.type} track to {name!r}.",
                    fix="Close any modal dialog in the Resolve GUI, delete the empty "
                    "timeline, and build again.",
                    detail={"timeline": name, "track": track.label},
                )
            log.info("Added %s to %s", track.label, name)
    log.info(
        "%s targets %s (%d video, %d audio tracks)",
        name,
        ", ".join(track.label for track in _tracks(shots)),
        _track_count(timeline, "video"),
        _track_count(timeline, "audio"),
    )


def _track_count(timeline: Timeline, track_type: str) -> int:
    try:
        return int(timeline.GetTrackCount(track_type) or 0)
    except (TypeError, ValueError):
        log.warning("Resolve gave an unreadable %s track count", track_type)
        return 0


def _tracks(shots: list[Shot]) -> list[Track]:
    """The tracks this cut needs, in a fixed order so the logs read the same way each time."""
    order = list(TRACK_PREFIXES)
    return sorted(
        {shot.track for shot in shots},
        key=lambda track: (order.index(track.type), track.index),
    )


def _refuse_locked_tracks(timeline: Timeline, shots: list[Shot], name: str) -> None:
    """E11: a locked track accepts the append, reports items, and places nothing.

    A timeline this build just created should have no locked track — but it is created
    from the project's timeline template, which is the director's to configure, and the
    check costs one call against a failure mode that leaves no trace anywhere else.
    """
    locked = [
        locked_track_finding(track.label)
        for track in _tracks(shots)
        if timeline.GetIsTrackLocked(track.type, track.index)
    ]
    if not locked:
        return
    raise BuildFailedError(
        cause=f"{len(locked)} target track(s) of {name!r} are locked, so nothing was appended.",
        fix="Unlock the track in the timeline header and build again.",
        detail={"timeline": name, "errors": [finding.as_dict() for finding in locked]},
    )


def _unlock_stills(clips: dict[str, media.LocatedClip]) -> None:
    """Write each still's out point once, so its ``endFrame`` is honoured exactly (#18 (a))."""
    seen: set[int] = set()
    for located in clips.values():
        if id(located.clip) in seen:
            continue
        seen.add(id(located.clip))
        if media.apply_still_workaround(located.clip, media.properties(located.clip)):
            log.info("Unlocked exact durations on the still %s", located.clip.GetName())


def _append(pool: Pool, shots: list[Shot], name: str) -> None:
    """One call for the whole cut. Its return value is counted, never believed."""
    appended = pool.AppendToTimeline([shot.clip_info() for shot in shots])
    if not appended:
        raise BuildFailedError(
            cause=f"Resolve appended nothing to {name!r}.",
            detail={"timeline": name, "clips": len(shots)},
        )
    log.info("Appended %d clips to %s; Resolve returned %d", len(shots), name, len(appended))


def _verify(timeline: Timeline, shots: list[Shot], name: str, origin: int) -> None:
    """Read the tracks back: the only way to know an append landed where it was told to.

    ``origin`` is the timeline start the shots were positioned against. Placement is judged
    as an offset from each timeline's own first frame, never as an absolute frame, because
    the cut a tail delivers is read back on a *different* timeline from the one it was
    appended to: the round trip imports the document, and Resolve is free to start what it
    imports at a timecode of its own choosing. Compared absolutely, an import that begins
    one hour later than the staging cut reports every shot in a correct cut as misplaced —
    and the build then deletes it.
    """
    landed_origin = timeline_read.start_frame(timeline)
    if landed_origin != origin:
        log.info(
            "%s starts at frame %d, not the %d its shots were placed against; "
            "placement is checked as offsets from each timeline's own start",
            name,
            landed_origin,
            origin,
        )
    misplaced = [
        shot
        for track in _tracks(shots)
        for shot in _adrift(timeline, shots, track, origin, landed_origin)
    ]
    if not misplaced:
        return
    raise BuildFailedError(
        cause=f"{len(misplaced)} of {len(shots)} clips did not land where the cut puts them in "
        f"{name!r}, so the timeline does not match the cut file.",
        fix="Resolve slides an append that overlaps existing media and drops one onto a track "
        "it cannot reach, both while reporting success. Delete the timeline it made, check "
        "the cut file's ranges, and build again.",
        detail={
            "timeline": name,
            "misplaced": [_expected(shot) for shot in misplaced[:MISPLACED_CAP]],
            "misplaced_total": len(misplaced),
        },
    )


def _adrift(
    timeline: Timeline,
    shots: list[Shot],
    track: Track,
    origin: int,
    landed_origin: int,
) -> list[Shot]:
    """The shots this track does not hold, matched on ``(offset from start, duration)``."""
    wanted = [shot for shot in shots if shot.track == track]
    if not wanted:
        return []
    landed = {
        (item.GetStart() - landed_origin, item.GetDuration())
        for item in timeline.GetItemListInTrack(track.type, track.index) or []
    }
    return [shot for shot in wanted if (shot.record - origin, shot.duration) not in landed]


def _expected(shot: Shot) -> dict[str, Any]:
    return {
        "id": shot.id,
        "clip": shot.name,
        "track": shot.track.label,
        "record_frame": shot.record,
        "duration": shot.duration,
    }


__all__ = ["Shot", "Track", "build_timeline"]
