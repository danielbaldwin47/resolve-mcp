"""Materialising a cut file: sequential V1 over one master-audio clip, as a new version.

The one write primitive Resolve gives us is append-with-exact-placement, and the #18 spike
found it full of silent failures. Everything in this file exists to make one guarantee:
*either the timeline holds the cut exactly, or the build says why it does not.*

* **Nothing starts on a file that will not build.** The same rules ``validate_cut`` runs
  run here first, over the same pool reading, and a single error aborts before a timeline
  is created. A cut that is valid but describes something this build cannot place is
  refused for the same reason — a timeline missing part of its own cut is the half-built
  outcome the pre-flight exists to prevent.
* **A new version every time.** The name is ``<base> v<N+1>`` scanned off the project's
  existing names, so no build ever writes into a timeline someone has already reviewed.
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

from ..cut.validate import locked_track_finding, positions
from ..errors import BuildFailedError, CutInvalidError, UnsupportedCutFeatureError
from ..logging_config import get_logger
from ..naming import next_version_name
from . import cut, media
from . import timeline as timeline_read
from .connection import ResolveConnection

log = get_logger("build")

Clip = Any
Pool = Any
Project = Any
Timeline = Any

MEDIA_TYPES: Final[dict[str, int]] = {"video": 1, "audio": 2}
"""Resolve's ``mediaType``: 1 appends video only, 2 audio only. Never omitted."""

TRACK_INDEX: Final = 1
"""Sequential V1 is one video track and one audio track; both are the first of their kind."""

TRACK_LABELS: Final[dict[str, str]] = {"video": "V1", "audio": "A1"}

AUDIO_TRACK_TYPE: Final = "stereo"
"""What a master mix is. ``AddTrack`` defaults to mono, which would fold a stereo mix."""

MISPLACED_CAP: Final = 20
"""A drifted build misplaces everything downstream of the blockage; the count says how many."""


@dataclass(frozen=True)
class Shot:
    """One append: which frames of which clip land where, on which kind of track."""

    id: str
    track_type: str
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
            "mediaType": MEDIA_TYPES[self.track_type],
            "trackIndex": TRACK_INDEX,
            "recordFrame": self.record,
        }


def build_timeline(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
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
    _refuse_what_cannot_be_placed(doc)

    project = timeline_read.open_project(connection)
    pool = media.media_pool(connection)
    name = next_version_name(
        str(doc["timeline"]["name"]),
        timeline_read.timeline_names(project),
    )
    clips = cut.clips_by_alias(checked)
    _unlock_stills(clips)

    built = _create(pool, project, name)
    shots = _shots(doc, clips, _start_frame(built))
    _make_tracks(built, shots)
    _refuse_locked_tracks(built, shots, name)
    _append(pool, shots, name)
    _verify(built, shots, name)

    log.info("Built %s: %d clips from %s", name, len(shots), checked.loaded.content_hash)
    return {
        "cut_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "timeline": timeline_read.summarise(timeline_read.Reader(connection), built, project, name),
        "placed": {
            "segments": sum(1 for shot in shots if shot.track_type == "video"),
            "audio": any(shot.track_type == "audio" for shot in shots),
        },
        "warnings": [finding.as_dict() for finding in checked.warnings],
    }


# --- what this build will not attempt ------------------------------------------------------


def _refuse_what_cannot_be_placed(doc: dict[str, Any]) -> None:
    """Overlays validate, but only the V1 substrate is built here — say so, build nothing."""
    overlays = doc.get("overlays") or []
    if not overlays:
        return
    raise UnsupportedCutFeatureError(
        cause=f"This cut has {len(overlays)} overlay(s); build_timeline places the sequential "
        f"V1 and the master audio only.",
        fix="Remove the overlays block to build the V1 cut now — anchored overlays are placed "
        "by a later tool, and building without them would leave a timeline that does not "
        "match its own cut file.",
        detail={"overlays": [str(overlay["id"]) for overlay in overlays]},
    )


# --- placement ------------------------------------------------------------------------------


def _shots(doc: dict[str, Any], clips: dict[str, media.LocatedClip], start: int) -> list[Shot]:
    """Every append the cut asks for, positioned absolutely from the timeline start."""
    placed = positions(doc)
    shots = [
        _shot(
            id=str(segment["id"]),
            track_type="video",
            located=clips[str(segment["source"])],
            source_in=int(segment["in"]),
            record=start + placed[str(segment["id"])][0],
            duration=placed[str(segment["id"])][1],
        )
        for segment in doc["segments"]
    ]
    audio = doc.get("audio")
    if isinstance(audio, dict):
        # One continuous clip under the whole cut: the substrate the segments are laid over,
        # and the reason a concert cut stays in sync with the mix the director approved.
        shots.append(
            _shot(
                id="audio",
                track_type="audio",
                located=clips[str(audio["source"])],
                source_in=int(audio["in"]),
                record=start,
                duration=int(audio["out"]) - int(audio["in"]),
            )
        )
    return shots


def _shot(
    id: str,
    track_type: str,
    located: media.LocatedClip,
    source_in: int,
    record: int,
    duration: int,
) -> Shot:
    return Shot(
        id=id,
        track_type=track_type,
        clip=located.clip,
        name=str(located.clip.GetName() or ""),
        source_in=source_in,
        record=record,
        duration=duration,
    )


def _start_frame(timeline: Timeline) -> int:
    """The timeline's own first frame. A record frame below it is *not* clamped (#18 (d))."""
    try:
        return int(float(timeline.GetStartFrame()))
    except (TypeError, ValueError):
        log.warning("Resolve gave an unreadable start frame; placing from 0")
        return 0


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


def _make_tracks(timeline: Timeline, shots: list[Shot]) -> None:
    """Pre-create the tracks the cut needs: an index past the next free one is dropped."""
    for track_type in sorted({shot.track_type for shot in shots}):
        # Only an audio track has a sub-type, and its default is mono — which would fold a
        # stereo master mix down on the way in.
        extra = (AUDIO_TRACK_TYPE,) if track_type == "audio" else ()
        while int(timeline.GetTrackCount(track_type) or 0) < TRACK_INDEX:
            timeline.AddTrack(track_type, *extra)
            log.info("Added a %s track to %s", track_type, timeline.GetName())


def _refuse_locked_tracks(timeline: Timeline, shots: list[Shot], name: str) -> None:
    """E11: a locked track accepts the append, reports items, and places nothing."""
    locked = [
        locked_track_finding(TRACK_LABELS[track_type])
        for track_type in sorted({shot.track_type for shot in shots})
        if timeline.GetIsTrackLocked(track_type, TRACK_INDEX)
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


def _verify(timeline: Timeline, shots: list[Shot], name: str) -> None:
    """Read the tracks back: the only way to know an append landed where it was told to."""
    misplaced = [shot for track in TRACK_LABELS for shot in _adrift(timeline, shots, track)]
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


def _adrift(timeline: Timeline, shots: list[Shot], track_type: str) -> list[Shot]:
    wanted = [shot for shot in shots if shot.track_type == track_type]
    if not wanted:
        return []
    landed = {
        (item.GetStart(), item.GetDuration())
        for item in timeline.GetItemListInTrack(track_type, TRACK_INDEX) or []
    }
    return [shot for shot in wanted if (shot.record, shot.duration) not in landed]


def _expected(shot: Shot) -> dict[str, Any]:
    return {
        "id": shot.id,
        "clip": shot.name,
        "track": TRACK_LABELS[shot.track_type],
        "record_frame": shot.record,
        "duration": shot.duration,
    }


__all__ = ["Shot", "build_timeline"]
