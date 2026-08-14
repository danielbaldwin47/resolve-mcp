"""``FakeMediaPool`` — the media pool itself, and the ``media_pool()`` builder for tests."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .media import AUDIO_TYPE, FakeFolder, _import_one, text_plus_template
from .timeline import FakeTimeline, FakeTrack, _appended_duration, _track_end
from .timeline_item import FakeTimelineItem

if TYPE_CHECKING:
    from .connection import FakeResolve
    from .fusion import FakeFusionComp
    from .media import FakeMediaPoolItem
    from .project import FakeProject


class FakeMediaPool:
    """The media pool: bins, imports, moves and relinks.

    Import and relink consult the real filesystem, because that is what the wrappers do
    to decide whether a clip is offline — the fake would be lying if it did not.
    """

    def __init__(self, owner: FakeResolve | None = None, root: FakeFolder | None = None) -> None:
        self._owner = owner
        self._root = root or FakeFolder("Master", owner)
        self._current = self._root
        self.import_result: list[FakeMediaPoolItem] | None = None
        self.relink_result: bool | None = None
        self.move_result = True
        self.calls: list[str] = []
        self.timeline_imports: list[tuple[str, dict[str, Any]]] = []
        self.imported_timeline: FakeTimeline | None = None
        self.refuses_timeline_import = False
        self._project: FakeProject | None = None
        self.create_timeline_result: bool = True
        self.new_timeline_start = 0
        self.new_timeline_tracks: tuple[int, int] = (1, 1)
        self.new_timeline_locked = False
        self.new_timeline_items: list[FakeTimelineItem] = []
        self.new_timeline_export_result = True
        # Held-open exports on the timeline a build makes for itself, which is the only
        # timeline whose export a tail build ever rewrites.
        self.new_timeline_locks_exports = False
        # An import that answers with a timeline and leaves the transitions out — the
        # failure a tail build cannot see from the return value, since the cut it hands
        # back is a perfectly good cut with a hard edge where its tail should be.
        self.import_drops_transitions = False
        # Resolve trims a dissolve to the handles the shot has, so an import can answer with
        # the fade *shortened* rather than missing — the outcome a check that only counted
        # transitions would confirm as the device the cut asked for.
        self.import_trims_transitions_to: int | None = None
        # An import that places every clip late. The round trip is a second placement, and
        # the return value cannot tell one that landed from one that slid.
        self.import_slides_clips = 0
        # An import that lands on a start timecode of its own — an hour in where the
        # document said zero — carrying its cut along unchanged. A correct round trip that
        # a read-back comparing absolute frames would call a cut that moved.
        self.import_starts_at: int | None = None
        self.add_track_result = True
        self.appends: list[dict[str, Any]] = []
        # The .drb route. Each knob is one outcome a real import has been seen to take:
        # a refusal, a success, and the one that costs an afternoon — True with an
        # untouched pool.
        self.folder_imports: list[tuple[str, str]] = []
        self.import_folder_result: bool | None = None
        self.import_lands_nothing = False
        self.imported_folder: FakeFolder | None = None
        # Take-selector knobs handed to every item this pool appends: an item a build
        # creates cannot be configured any other way, since the test never holds it.
        self.take_quirks: dict[str, Any] = {}
        self.append_calls: list[list[dict[str, Any]]] = []
        self.append_result: list[FakeTimelineItem] | None = None
        # ``startFrame`` read as an offset from the clip's own ``Start`` rather than as an
        # absolute media frame. Which reading Resolve takes is unmeasured: every clip the
        # pillar has built from reports ``Start = 0``, where the two coincide, so the
        # difference has never shown. The build sends absolute frames — the space ``Start``
        # and ``End`` report and E5 checks against — and this is the other reading, which
        # places every shot as far past its in point as the media's start stamp is from
        # zero while reporting a perfectly ordinary success.
        self.rebases_source_frames = False
        self.appends_share_one_comp = False
        self.appends_land_nowhere = False
        self.created_timelines: list[str] = []
        self.refuses_create_timeline = False
        self.switches_current_timeline = True
        self.deleted_timelines: list[FakeTimeline] = []
        # Timelines this pool answers ``False`` for. Resolve refuses a delete for reasons
        # a caller cannot see — a timeline open in another page, one inside a compound —
        # and the refusal is the only word it gives, so a sweep has to survive one.
        self.refuse_deleting: set[FakeTimeline] = set()
        self.deleted_folders: list[FakeFolder] = []
        self.delete_folders_result = True

    def attach_project(self, project: FakeProject) -> None:
        """The project this pool belongs to — appends land on *its* current timeline."""
        self._project = project

    def _reached_project(self) -> FakeProject | None:
        """The attached project, or the owner's current one for a pool never attached."""
        if self._project is not None:
            return self._project
        return self._owner.current_project if self._owner is not None else None

    def _check(self, method: str) -> None:
        self.calls.append(method)
        if self._owner is not None:
            self._owner._check()

    def adopt(self, owner: FakeResolve) -> None:
        """Wire the pool and its folders to a handle, so a dropped handle fails here too."""
        self._owner = owner
        folders = [self._root]
        while folders:
            folder = folders.pop()
            folder.adopt(owner)
            folders.extend(folder.subfolders)
        self.calls.clear()

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        self._check("GetRootFolder")
        return self._root

    def GetCurrentFolder(self) -> FakeFolder:  # noqa: N802
        self._check("GetCurrentFolder")
        return self._current

    def SetCurrentFolder(self, folder: FakeFolder) -> bool:  # noqa: N802
        self._check("SetCurrentFolder")
        self._current = folder
        return True

    def AddSubFolder(self, folder: FakeFolder, name: str) -> FakeFolder | None:  # noqa: N802
        self._check("AddSubFolder")
        if any(existing.GetName() == name for existing in folder.subfolders):
            return None
        created = FakeFolder(name, self._owner)
        folder.subfolders.append(created)
        return created

    def MoveClips(  # noqa: N802
        self,
        clips: list[FakeMediaPoolItem],
        target: FakeFolder,
    ) -> bool:
        self._check("MoveClips")
        if not self.move_result:
            return False
        for clip in clips:
            for folder in self._walk(self._root):
                if clip in folder.clips:
                    folder.clips.remove(clip)
            target.clips.append(clip)
        return True

    def ImportMedia(  # noqa: N802
        self,
        items: list[str | dict[str, Any]],
    ) -> list[FakeMediaPoolItem]:
        self._check("ImportMedia")
        if self.import_result is not None:
            return list(self.import_result)
        imported: list[FakeMediaPoolItem] = []
        for item in items:
            clip = _import_one(item)
            if clip is None:
                continue
            self._current.clips.append(clip)
            imported.append(clip)
        return imported

    def ImportTimelineFromFile(  # noqa: N802
        self,
        file_path: str,
        options: dict[str, Any] | None = None,
    ) -> FakeTimeline | None:
        """Materialise a timeline from an interchange file, as the real pool does.

        The filesystem is consulted for the same reason ``ImportMedia`` consults it: a path
        Resolve cannot read imports nothing and says so only by returning ``None``.
        ``imported_timeline`` overrides the result — set it to a timeline the project
        already holds to model the one outcome this must never be mistaken for, an import
        that landed on an existing cut.
        """
        self._check("ImportTimelineFromFile")
        asked = dict(options or {})
        self.timeline_imports.append((file_path, asked))
        if self.refuses_timeline_import or not Path(file_path).exists():
            return None
        if self.imported_timeline is not None:
            return self.imported_timeline
        name = str(asked.get("timelineName") or Path(file_path).stem)
        # An OTIO document is rebuilt into the cut it describes rather than replaced by a
        # canned one: the tail is built by editing a document between an export and an
        # import, so an import that ignored what it was given could not tell a landed
        # dissolve from a lost one.
        from .interchange import read_document, timeline_from

        document = read_document(file_path)
        if document is not None:
            timeline = timeline_from(
                document,
                name,
                not self.import_drops_transitions,
                trims_transitions_to=self.import_trims_transitions_to,
                slides_clips=self.import_slides_clips,
                starts_at=self.import_starts_at,
            )
        else:
            timeline = FakeTimeline(
                name,
                video=[[FakeTimelineItem("C0012.mp4", 0, 60, source_start=1000)]],
            )
        if self._owner is not None:
            timeline.adopt(self._owner)
            project = self._owner.current_project
            if project is not None:
                project.add_timeline(timeline)
        return timeline

    def RelinkClips(  # noqa: N802
        self,
        clips: list[FakeMediaPoolItem],
        folder_path: str,
    ) -> bool:
        self._check("RelinkClips")
        if self.relink_result is not None:
            return self.relink_result
        relinked = False
        for clip in clips:
            current = str(clip.GetClipProperty("File Path") or "")
            candidate = Path(folder_path) / Path(current).name
            if candidate.exists():
                clip.SetClipProperty("File Path", str(candidate))
                relinked = True
        return relinked

    def ImportFolderFromFile(  # noqa: N802
        self,
        file_path: str,
        source_clips_path: str = "",
    ) -> bool:
        """Unpack a ``.drb`` bin export under the current folder.

        The bin's *name* comes from the file, not from the caller — Resolve offers no way
        to name it — so a caller can only find what landed by comparing the current
        folder's children before and after. ``import_lands_nothing`` models the answer
        that hides a failure: ``True``, and the pool is untouched.
        """
        self._check("ImportFolderFromFile")
        self.folder_imports.append((file_path, source_clips_path))
        if self.import_folder_result is not None:
            landed = self.import_folder_result
        else:
            landed = Path(file_path).exists()
        if not landed:
            return False
        if self.import_lands_nothing:
            return True
        folder = self.imported_folder
        if folder is None:
            folder = FakeFolder(Path(file_path).stem)
            folder.clips.append(text_plus_template())
        if self._owner is not None:
            folder.adopt(self._owner)
        self._current.subfolders.append(folder)
        return True

    def CreateEmptyTimeline(self, name: str) -> FakeTimeline | None:  # noqa: N802
        """A fresh timeline, added to the project and made current — as Resolve does.

        Whether creating a timeline also *switches* to it is undocumented, so
        ``switches_current_timeline`` can withhold the switch: a caller that assumed it
        happened would append onto whatever cut was already open.
        """
        self._check("CreateEmptyTimeline")
        self.created_timelines.append(name)
        if self.refuses_create_timeline or not self.create_timeline_result:
            return None
        project = self._reached_project()
        video, audio = self.new_timeline_tracks
        project_fps = project.GetSetting("timelineFrameRate") if project is not None else None
        timeline = FakeTimeline(
            name,
            fps=project_fps or "59.94",
            start_frame=self.new_timeline_start,
            video=[
                FakeTrack(
                    f"Video {index + 1}",
                    list(self.new_timeline_items) if index == 0 else None,
                    locked=self.new_timeline_locked,
                )
                for index in range(video)
            ],
            audio=[
                FakeTrack(f"Audio {index + 1}", locked=self.new_timeline_locked)
                for index in range(audio)
            ],
            owner=self._owner,
        )
        timeline.add_track_result = self.add_track_result
        # A build that round-trips its tail exports the timeline it just created, so the
        # export failure has to be settable *before* the timeline the test never sees exists.
        timeline.export_result = self.new_timeline_export_result
        timeline.locks_written_exports = self.new_timeline_locks_exports
        if project is not None:
            project.add_timeline(timeline)
            if self.switches_current_timeline:
                project.SetCurrentTimeline(timeline)
        return timeline

    def AppendToTimeline(  # noqa: N802
        self,
        clips: list[dict[str, Any]],
    ) -> list[FakeTimelineItem]:
        """Append onto the project's *current* timeline, footguns and all (#18 spike (d)).

        Every confirmed failure mode is modelled, because each one returns a truthy item
        either way: the caller cannot tell an append that landed from one that vanished
        without re-reading the track, and that re-read is what the wrapper is for.
        """
        self._check("AppendToTimeline")
        infos = [
            dict(entry) if isinstance(entry, dict) else {"mediaPoolItem": entry}
            for entry in clips
        ]
        self.append_calls.append([dict(info) for info in infos])
        project = self._reached_project()
        timeline = project.GetCurrentTimeline() if project is not None else None
        if self.append_result is not None:
            return self._land(list(self.append_result), timeline)
        if timeline is None:
            return []
        placed = []
        shared: FakeFusionComp | None = None
        for info in infos:
            self.appends.append(dict(info))
            source: FakeMediaPoolItem = info["mediaPoolItem"]
            comps: list[FakeFusionComp] = []
            if source.template_comp is not None:
                # Each placed instance gets its *own copy* of the template's comp, which is
                # what makes per-instance text possible. ``appends_share_one_comp`` models
                # the opposite — one comp handed to every instance, so setting one title's
                # text rewrites every other — and is the reason the Text+ probe exists.
                if self.appends_share_one_comp:
                    shared = shared or source.template_comp
                    comps = [shared]
                else:
                    comps = [source.template_comp.copy()]
            placed.append(self._append_one(timeline, info, comps))
        return placed

    def _land(
        self,
        placed: list[FakeTimelineItem],
        target: FakeTimeline | None,
    ) -> list[FakeTimelineItem]:
        """Put overridden append results on the target's first video track.

        ``appends_land_nowhere`` models the answer that would fool a caller who trusted the
        return value: real timeline items handed back, and a timeline that holds none.
        """
        if target is not None and not self.appends_land_nowhere:
            target.first_video_track().items.extend(placed)
        return placed

    def DeleteTimelines(self, timelines: list[FakeTimeline]) -> bool:  # noqa: N802
        """Delete cuts, refusing the whole call if one of them is the cut now open.

        Resolve will not delete the timeline it is sitting on, and says so only by
        returning ``False`` — so a caller that deleted a scratch timeline without moving
        off it first would leave it behind and never hear why.
        """
        self._check("DeleteTimelines")
        if any(timeline in self.refuse_deleting for timeline in timelines):
            return False
        project = self._reached_project()
        if project is not None and any(
            timeline is project.GetCurrentTimeline() for timeline in timelines
        ):
            return False
        # Recorded only once the refusals are past: a cut Resolve would not delete has not
        # been deleted, and a fake that says otherwise lets a caller assert its own bug.
        self.deleted_timelines.extend(timelines)
        if project is None:
            return True
        for timeline in timelines:
            project.remove_timeline(timeline)
        return True

    def DeleteFolders(self, folders: list[FakeFolder]) -> bool:  # noqa: N802
        self._check("DeleteFolders")
        self.deleted_folders.extend(folders)
        if not self.delete_folders_result:
            return False
        for parent in self._walk(self._root):
            parent.subfolders = [sub for sub in parent.subfolders if sub not in folders]
        return True

    def _append_one(
        self,
        timeline: FakeTimeline,
        info: dict[str, Any],
        comps: Sequence[FakeFusionComp] = (),
    ) -> FakeTimelineItem:
        clip: FakeMediaPoolItem = info["mediaPoolItem"]
        source_start = int(info.get("startFrame", 0))
        duration = _appended_duration(clip, source_start, info.get("endFrame"))
        if self.rebases_source_frames:
            # The span is still the one that was asked for; only where it begins moves,
            # which is what makes the drift invisible to a check that reads durations.
            source_start += _clip_start(clip)
        media_type = info.get("mediaType")
        track_type = "audio" if media_type == AUDIO_TYPE else "video"
        index = int(info.get("trackIndex", 1))
        record = int(info.get("recordFrame", _track_end(timeline, track_type, index)))

        # A placed instance's comp is rendered over the instance, so its render range is
        # the clip's own length — which is the clock a keyframe on it is counted in.
        for comp in comps:
            if comp.render_range is not None:
                comp.render_range = (0, duration - 1)

        item = FakeTimelineItem(
            clip.GetName(),
            record,
            duration,
            source_start=source_start,
            media_item=clip,
            comps=comps,
            owner=self._owner,
            **self.take_quirks,
        )
        # An explicit track index without a media type: Resolve reports success and drops it.
        if media_type is None and "trackIndex" in info:
            return item
        count = timeline.GetTrackCount(track_type)
        if index == count + 1:
            timeline.AddTrack(track_type)
        elif index > count + 1:
            return item  # past the next free track: reported placed, never placed
        if timeline.GetIsTrackLocked(track_type, index):
            return item  # the silent one: a locked track swallows the append
        # No overwrite on overlap — the clip slides to the first free frame after the
        # blocker, so a placement is only exact if nothing was in the way.
        for existing in timeline.GetItemListInTrack(track_type, index) or []:
            if record < existing.GetStart() + existing.GetDuration() and existing.GetStart() < (
                record + duration
            ):
                record = existing.GetStart() + existing.GetDuration()
                item = FakeTimelineItem(
                    clip.GetName(),
                    record,
                    duration,
                    source_start=source_start,
                    media_item=clip,
                    comps=comps,
                    owner=self._owner,
                    **self.take_quirks,
                )
        if not self.appends_land_nowhere:
            timeline.place(track_type, index, item)
        return item

    def _walk(self, folder: FakeFolder) -> list[FakeFolder]:
        found = [folder]
        for sub in folder.subfolders:
            found.extend(self._walk(sub))
        return found


def _clip_start(clip: FakeMediaPoolItem) -> int:
    """The first frame of the clip's own media — zero on everything without a start stamp.

    Resolve answers with a string, and with an empty one on media that carries no stamp at
    all (audio, #46), so anything that is not a number reads as no stamp.
    """
    reported = clip.GetClipProperty("Start")
    if not isinstance(reported, str) or not reported.isdigit():
        return 0
    return int(reported)


def media_pool(bins: dict[str, list[FakeMediaPoolItem]] | None = None) -> FakeMediaPool:
    """A media pool from ``{"": [root clips], "Angles/Cam A": [clips]}``."""
    pool = FakeMediaPool()
    root = pool.GetRootFolder()
    for path, clips in (bins or {}).items():
        folder = root
        for segment in [part for part in path.split("/") if part]:
            existing = [sub for sub in folder.subfolders if sub.GetName() == segment]
            folder = existing[0] if existing else (pool.AddSubFolder(folder, segment) or folder)
        folder.clips.extend(clips)
    pool.calls.clear()
    return pool
