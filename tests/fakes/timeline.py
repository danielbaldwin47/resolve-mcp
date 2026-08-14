"""Timelines and their tracks.

``FakeTrack``, ``FakeTimeline``, the ``TrackSpec`` shorthand, and the frame arithmetic an
append lands on.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core import AnswersNone
from .media import IMAGE_SUFFIXES, STILL_DEFAULT_FRAMES

READ_ONLY = 0o444
"""What a file Resolve is holding open behaves like to a rewrite — see ``Export``."""

if TYPE_CHECKING:
    from .connection import FakeResolve
    from .media import FakeMediaPoolItem
    from .timeline_item import FakeTimelineItem


class FakeTrack:
    """One track's worth of state: the API reaches it only through the timeline."""

    def __init__(
        self,
        name: str,
        items: list[FakeTimelineItem] | None = None,
        enabled: bool = True,
        locked: bool = False,
    ) -> None:
        self.name = name
        self.items = list(items or [])
        self.enabled = enabled
        self.locked = locked


TrackSpec = Sequence["FakeTrack | list[FakeTimelineItem]"]


def _as_tracks(spec: TrackSpec | None, label: str) -> list[FakeTrack]:
    tracks: list[FakeTrack] = []
    for index, entry in enumerate(spec or [], start=1):
        tracks.append(
            entry if isinstance(entry, FakeTrack) else FakeTrack(f"{label} {index}", entry)
        )
    return tracks


class FakeTimeline(AnswersNone):
    def __init__(
        self,
        name: str,
        fps: str = "59.94",
        start_frame: int = 0,
        video: TrackSpec | None = None,
        audio: TrackSpec | None = None,
        # Marker keys come back across a C bridge as whatever Resolve put there — floats
        # normally, but the type is not the fake's to promise, and the wrapper parses them.
        markers: dict[Any, dict[str, Any]] | None = None,
        end_frame: int | None = None,
        missing: frozenset[str] | set[str] | None = None,
        owner: FakeResolve | None = None,
    ) -> None:
        self._missing = set(missing or ())
        self._name = name
        self._fps = fps
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._tracks: dict[str, list[FakeTrack]] = {
            "video": _as_tracks(video, "Video"),
            "audio": _as_tracks(audio, "Audio"),
            "subtitle": [],
        }
        self._markers = dict(markers or {})
        self._owner = owner
        #: #84's defect, opt-in — see :meth:`GetIsTrackEnabled` for what it models.
        self.getters_need_current = False
        self.marker_writes: list[dict[str, Any]] = []
        self.refuse_markers = False
        # Refusing one marker by name is how a failed *replacement* is staged: Resolve
        # takes the delete, refuses the add, and the restore of the displaced marker has
        # to be free to succeed or the test could not tell a restore from a loss.
        self.refuse_marker_names: set[str] = set()
        #: Transitions this cut carries, as ``{track, kind, name, in_offset}``. Not a
        #: Resolve attribute and deliberately not reachable through any API method: the
        #: scripting API has no getter for a transition at all, which is why a caller that
        #: wants to know reads them back out of an interchange export. They land here on
        #: import and go back out on export, so a fake round trip carries them the way the
        #: real one does.
        self.transitions: list[dict[str, Any]] = []
        self.exports: list[tuple[str, Any, tuple[Any, ...]]] = []
        self.export_result = True
        self.export_writes_the_file = True
        # Export type *values* that answer True and write a zero-byte file — Resolve 21.0.3
        # does exactly this for EXPORT_FCPXML_1_10 (#26, live).
        self.export_types_that_write_nothing: set[Any] = set()
        # Paths one of those types has touched. Resolve keeps the handle for the life of
        # the process, so the name is spent whatever type asks for it next (#26, live).
        self.export_paths_held_open: set[str] = set()
        # Every file this timeline exports comes back read-only, which is what a file
        # Resolve is holding open looks like to anything that tries to rewrite it on
        # Windows (#26). Modelled on the filesystem rather than in the fake because the
        # code under test writes with ``Path.write_text`` and never asks the fake first.
        self.locks_written_exports = False
        self.add_track_result = True
        self.set_track_name_result = True
        # A clear that answers True and leaves the clips standing is the failure a caller
        # can only find by re-reading the track — the same shape as a locked-track append.
        self.delete_clips_leaves_them = False
        self.deleted_clips: list[tuple[list[FakeTimelineItem], bool]] = []

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for tracks in self._tracks.values():
            for track in tracks:
                for item in track.items:
                    item.adopt(owner)
                    # Belt and braces with ``GetItemListInTrack``: a path that reaches an
                    # item some other way still finds it knowing its timeline, so #84 is
                    # modelled there too rather than silently reading truthful.
                    item.held_by(self)

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def _track(self, track_type: str, index: int) -> FakeTrack | None:
        tracks = self._tracks.get(track_type, [])
        return tracks[index - 1] if 1 <= index <= len(tracks) else None

    def tracks_of(self, track_type: str) -> list[FakeTrack]:
        """Every track of one kind, in order — what an interchange export walks."""
        return list(self._tracks.get(track_type, []))

    def first_video_track(self) -> FakeTrack:
        """The track an append lands on; a timeline Resolve made always has one."""
        tracks = self._tracks["video"]
        if not tracks:
            tracks.append(FakeTrack("Video 1"))
        return tracks[0]

    def GetName(self) -> str:  # noqa: N802 - mirrors the Resolve API
        self._check()
        return self._name

    def GetUniqueId(self) -> str:  # noqa: N802
        """Resolve's own identity for a cut — the only way to tell two proxies apart."""
        self._check()
        return f"fake-timeline-{id(self):x}"

    def GetSetting(self, key: str) -> str | None:  # noqa: N802
        self._check()
        return self._fps if key == "timelineFrameRate" else None

    def GetStartFrame(self) -> int:  # noqa: N802
        self._check()
        return self._start_frame

    def GetEndFrame(self) -> int:  # noqa: N802
        """Where the timeline ends.

        ``end_frame`` is settable and independent of the items, so a test can say what
        Resolve reports rather than have the fake derive a number the wrapper is bound to
        agree with — the timeline's own duration is the one reading taken on trust.
        """
        self._check()
        if self._end_frame is not None:
            return self._end_frame
        ends = [
            item.GetStart() + item.GetDuration()
            for tracks in self._tracks.values()
            for track in tracks
            for item in track.items
        ]
        return max(ends, default=self._start_frame)

    def GetTrackCount(self, track_type: str) -> int:  # noqa: N802
        self._check()
        return len(self._tracks.get(track_type, []))

    def GetTrackName(self, track_type: str, index: int) -> str | None:  # noqa: N802
        self._check()
        track = self._track(track_type, index)
        return track.name if track else None

    def GetItemListInTrack(  # noqa: N802
        self,
        track_type: str,
        index: int,
    ) -> list[FakeTimelineItem] | None:
        self._check()
        track = self._track(track_type, index)
        if track is None:
            return None
        # The only route the read path takes to an item, so the cheapest place to tell each
        # one which timeline holds it — and the only one that also catches items a build
        # appended after ``adopt``. An item needs that link to model #84 (see
        # ``GetIsTrackEnabled``): whether ``GetTakesCount`` lies is a fact about its
        # timeline, not about the item.
        for item in track.items:
            item.held_by(self)
        return list(track.items)

    def GetIsTrackEnabled(self, track_type: str, index: int) -> bool:  # noqa: N802
        """Whether the track is on — and, opted into, the lie Resolve tells about that.

        ``getters_need_current`` models #84: on Studio 21.0.3.7 a handful of getters answer
        the *falsy value of their own type* for a timeline that is not the project's
        current one — no error, no ``None`` to distinguish "genuinely off" from "you did
        not ask the current timeline". The #84 sweep read every Timeline and TimelineItem
        getter on a non-current timeline and again while current; exactly three of the ones
        this repo reads drift, and they share this knob because they share one cause:

        ========================  =============  ============
        getter                    non-current    current
        ========================  =============  ============
        ``GetIsTrackEnabled``     ``False``      true state
        ``GetIsTrackLocked``      ``False``      true state
        ``GetTakesCount``         ``0``          true count
        ========================  =============  ============

        The other 90 getters — frames, names, source bounds, ``GetClipEnabled``,
        ``GetMarkers`` — were read with non-falsy true values and did not drift, so they
        are proven safe rather than merely untested.

        Off by default because most tests are not about this. A test that turns it on is
        saying "this timeline is being read the way an agent surveying several timelines
        reads it", and then a wrapper that trusts the number goes red here rather than on
        the live machine.
        """
        self._check()
        if self.getters_need_current and not self._is_current():
            return False
        track = self._track(track_type, index)
        return bool(track and track.enabled)

    def _is_current(self) -> bool:
        project = self._owner.current_project if self._owner is not None else None
        return project is not None and project.GetCurrentTimeline() is self

    def GetIsTrackLocked(self, track_type: str, index: int) -> bool:  # noqa: N802
        self._check()
        if self.getters_need_current and not self._is_current():
            return False
        track = self._track(track_type, index)
        return bool(track and track.locked)

    def GetMarkers(self) -> dict[Any, dict[str, Any]]:  # noqa: N802
        """Markers keyed by frame *relative to the timeline start*, as Resolve keys them."""
        self._check()
        return {frame: dict(marker) for frame, marker in self._markers.items()}

    def AddMarker(  # noqa: N802
        self,
        frame: float,
        color: str,
        name: str,
        note: str,
        duration: float,
        custom_data: str = "",
    ) -> bool:
        """Add one marker, refusing a frame that already carries one — as Resolve does."""
        self._check()
        if self.refuse_markers or name in self.refuse_marker_names:
            return False
        if float(frame) in self._markers:
            return False
        self.marker_writes.append(
            {
                "frame": float(frame),
                "color": color,
                "name": name,
                "note": note,
                "duration": duration,
                "customData": custom_data,
            }
        )
        self._markers[float(frame)] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": duration,
            "customData": custom_data,
        }
        return True

    def DeleteMarkerAtFrame(self, frame: float) -> bool:  # noqa: N802
        self._check()
        return self._markers.pop(float(frame), None) is not None

    def Export(self, file_name: str, export_type: Any, *subtype: Any) -> bool:  # noqa: N802
        """Write an interchange file. The subtype is variadic because Resolve's is optional.

        ``export_writes_the_file=False`` models the failure the return value hides: Resolve
        answers True and nothing lands on disk. ``export_types_that_write_nothing`` models
        the same failure for one export type only, which is the real shape of it.

        A type that writes nothing also *poisons the path it touched*, exactly as the real
        one does: Resolve holds that zero-byte file open for the life of the process, so
        every later export to the same name fails no matter which type is asked for. That
        is why the real ladder never reuses a scratch filename, and modelling it here is
        what makes reuse fail a test instead of only failing on the machine.
        """
        self._check()
        self.exports.append((file_name, export_type, subtype))
        if not self.export_result:
            return False
        if file_name in self.export_paths_held_open:
            return False
        if export_type in self.export_types_that_write_nothing:
            self.export_paths_held_open.add(file_name)
            return True
        if self.export_writes_the_file:
            target = Path(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            # An .otio target gets a real document, because the tail device is built by
            # editing one — a placeholder string there would let a tail test pass over a
            # document no import could ever have taken.
            from .interchange import document_of, is_otio

            if is_otio(file_name):
                target.write_text(json.dumps(document_of(self), indent=1), encoding="utf-8")
            else:
                target.write_text(f"fake-export of {self._name}", encoding="utf-8")
            if self.locks_written_exports:
                target.chmod(READ_ONLY)
        return True

    def AddTrack(self, track_type: str, *_: Any) -> bool:  # noqa: N802
        self._check()
        if not self.add_track_result:
            return False
        tracks = self._tracks.setdefault(track_type, [])
        tracks.append(FakeTrack(f"{track_type.capitalize()} {len(tracks) + 1}"))
        return True

    def SetTrackName(self, track_type: str, index: int, name: str) -> bool:  # noqa: N802
        """Rename a track. Resolve refuses with a bare ``False`` and never says why."""
        self._check()
        track = self._track(track_type, index)
        if track is None or not self.set_track_name_result:
            return False
        track.name = name
        return True

    def DeleteClips(  # noqa: N802
        self,
        items: list[FakeTimelineItem],
        ripple: bool = False,
    ) -> bool:
        """Remove items from whichever track holds them, recording the ripple flag.

        The flag is recorded rather than acted on: what matters to a caller that owns one
        track is that it never asks for a ripple, because that would drag the cut on the
        tracks below along with the titles it deleted.
        """
        self._check()
        self.deleted_clips.append((list(items), bool(ripple)))
        if self.delete_clips_leaves_them:
            return True
        wanted = {id(item) for item in items}
        for tracks in self._tracks.values():
            for track in tracks:
                track.items = [held for held in track.items if id(held) not in wanted]
        return True

    def place(self, track_type: str, index: int, item: FakeTimelineItem) -> None:
        """Put an item on a track, in start order — the pool's append reaches through here."""
        track = self._track(track_type, index)
        if track is None:
            return
        track.items.append(item)
        track.items.sort(key=lambda placed: placed.GetStart())


def _appended_duration(clip: FakeMediaPoolItem, source_start: int, end_frame: Any) -> int:
    """``endFrame - startFrame``, except on a still that has never had an out point written.

    That is the (a) spike verbatim: a freshly imported still ignores ``endFrame`` entirely
    and lands at the default duration until any ``Out`` write unlocks it.
    """
    still = Path(str(clip.GetClipProperty("File Path") or "")).suffix.lower() in IMAGE_SUFFIXES
    unlocked = any(key == "Out" for key, _ in clip.property_writes)
    if still and not unlocked:
        return STILL_DEFAULT_FRAMES
    if end_frame is None:
        return STILL_DEFAULT_FRAMES if still else 1
    return max(int(end_frame) - source_start, 1)


def _track_end(timeline: FakeTimeline, track_type: str, index: int) -> int:
    ends = [
        item.GetStart() + item.GetDuration()
        for item in timeline.GetItemListInTrack(track_type, index) or []
    ]
    return max(ends, default=timeline.GetStartFrame())
