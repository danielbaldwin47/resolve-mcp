"""A fake DaVinci Resolve scripting API.

This is the project's single test seam: it substitutes at the point the connection
manager hands out the Resolve singleton, so every layer above it — wrappers, tools —
is exercised with Resolve closed.

The fakes mimic the real API's shape, including its quirks: getters return ``None``
rather than raising, ``LoadProject`` returns ``None`` for an unknown name, and settings
come back as strings.
"""

from __future__ import annotations

import contextlib
import math
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class DroppedHandleError(RuntimeError):
    """What a stale Resolve handle raises when the app has gone away."""


class FakeFusionTool:
    """One node inside a Fusion comp; for titling only the Text+ node matters.

    ``SetInput`` returns ``None`` in the real API — it reports nothing, so the only way to
    know a write landed is to read it back, which is what the probe does.
    """

    def __init__(
        self,
        tool_id: str = "TextPlus",
        name: str = "Template Text",
        inputs: dict[str, Any] | None = None,
        owner: FakeResolve | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.name = name
        self.inputs: dict[str, Any] = dict(inputs or {"StyledText": "TEMPLATE"})
        self._owner = owner

    def copy(self) -> FakeFusionTool:
        return FakeFusionTool(self.tool_id, self.name, dict(self.inputs), self._owner)

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def GetAttrs(self, key: str | None = None) -> Any:  # noqa: N802
        """Fusion answers a whole dict, or one key. ``TOOLS_RegID`` is the node's type."""
        self._check()
        attrs = {"TOOLS_RegID": self.tool_id, "TOOLS_Name": self.name}
        return attrs if key is None else attrs.get(key)

    def SetInput(self, key: str, value: Any) -> None:  # noqa: N802
        self._check()
        self.inputs[key] = value

    def GetInput(self, key: str) -> Any:  # noqa: N802
        self._check()
        return self.inputs.get(key)


class FakeFusionComp:
    """A timeline item's Fusion composition.

    ``GetToolList`` is filtered by node type in the real API and returns a *one-based dict*
    rather than a list — a comp with no matching node answers an empty dict, not ``None``.
    """

    def __init__(
        self,
        tools: Sequence[FakeFusionTool] | None = None,
        owner: FakeResolve | None = None,
    ) -> None:
        self.tools: list[FakeFusionTool] = list(tools if tools is not None else [FakeFusionTool()])
        self._owner = owner

    def copy(self) -> FakeFusionComp:
        """What placing a template instance does: the new instance gets its own comp."""
        return FakeFusionComp([tool.copy() for tool in self.tools], self._owner)

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for tool in self.tools:
            tool.adopt(owner)

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def GetToolList(  # noqa: N802
        self,
        selected_only: bool = False,
        tool_type: str = "",
    ) -> dict[int, FakeFusionTool]:
        self._check()
        matching = [tool for tool in self.tools if not tool_type or tool.tool_id == tool_type]
        return {index: tool for index, tool in enumerate(matching, start=1)}


class FakeTimelineItem:
    """A clip on a track.

    ``GetEnd`` is deliberately configurable: the scripting docs do not say whether it is
    the last frame or one past it, so a fake that only ever agreed with
    ``GetStart() + GetDuration()`` would hide a wrapper that trusted the wrong one.

    ``supports_source_frames=False`` models a Resolve older than 18.5, where the source
    getters are *absent* rather than failing — so ``getattr`` misses them, which is the
    branch the wrapper actually takes.

    ``missing`` models the same absence the way the real API expresses it, which is not an
    ``AttributeError``: fusionscript answers *every* attribute name, handing back ``None``
    for one it does not know. Verified live on Studio 21.0.3.7, where
    ``hasattr(item, "GetTakeCount")`` is ``True`` and the attribute is ``None`` — so a
    ``hasattr`` guard passes and the call then fails with ``NoneType is not callable``.
    """

    def __init__(
        self,
        name: str,
        start: int,
        duration: int,
        source_start: int | None = None,
        left_offset: int | None = None,
        source_end: int | None = None,
        media_item: FakeMediaPoolItem | None = None,
        enabled: bool = True,
        takes: int = 0,
        supports_source_frames: bool = True,
        refuses: frozenset[str] | set[str] | None = None,
        end_is_inclusive: bool = False,
        comps: Sequence[FakeFusionComp] | None = None,
        missing: frozenset[str] | set[str] | None = None,
        owner: FakeResolve | None = None,
    ) -> None:
        self._name = name
        self._start = start
        self._duration = duration
        self._source_start = source_start
        self._left_offset = left_offset
        self._source_end = source_end
        self._media_item = media_item
        self._enabled = enabled
        self._takes = takes
        self._supports_source_frames = supports_source_frames
        self._refuses = set(refuses or ())
        self._end_is_inclusive = end_is_inclusive
        self.comps: list[FakeFusionComp] = list(comps or ())
        self._missing = set(missing or ())
        self._owner = owner

    SOURCE_GETTERS = ("GetSourceStartFrame", "GetSourceEndFrame")

    def __getattribute__(self, name: str) -> Any:
        """Hide the source getters entirely on a build that predates them."""
        if name in FakeTimelineItem.SOURCE_GETTERS and not object.__getattribute__(
            self, "_supports_source_frames"
        ):
            raise AttributeError(name)
        if not name.startswith("_") and name in object.__getattribute__(self, "_missing"):
            return None
        return object.__getattribute__(self, name)

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for comp in self.comps:
            comp.adopt(owner)

    def _check(self, method: str = "") -> None:
        if method and method in self._refuses:
            raise RuntimeError(f"{method} is not supported for this clip type")
        if self._owner is not None:
            self._owner._check()

    def GetName(self) -> str:  # noqa: N802 - mirrors the Resolve API
        self._check()
        return self._name

    def GetStart(self) -> int:  # noqa: N802
        self._check()
        return self._start

    def GetDuration(self) -> int:  # noqa: N802
        self._check()
        return self._duration

    def GetEnd(self) -> int:  # noqa: N802
        self._check()
        end = self._start + self._duration
        return end - 1 if self._end_is_inclusive else end

    def GetLeftOffset(self) -> int:  # noqa: N802
        """How far into the media the shot begins. Set it apart from the source start to
        say which getter a reading came from."""
        self._check()
        return (self._left_offset if self._left_offset is not None else self._source_start) or 0

    def GetSourceStartFrame(self) -> int:  # noqa: N802
        self._check("GetSourceStartFrame")
        return self._source_start or 0

    def GetSourceEndFrame(self) -> int:  # noqa: N802
        """The last source frame — inclusive, and not derivable from duration on a retime."""
        self._check()
        if self._source_end is not None:
            return self._source_end
        return (self._source_start or 0) + self._duration - 1

    def GetMediaPoolItem(self) -> FakeMediaPoolItem | None:  # noqa: N802
        self._check("GetMediaPoolItem")
        return self._media_item

    def GetClipEnabled(self) -> bool:  # noqa: N802
        self._check("GetClipEnabled")
        return self._enabled

    def GetTakeCount(self) -> int:  # noqa: N802
        self._check("GetTakeCount")
        return self._takes

    def GetFusionCompCount(self) -> int:  # noqa: N802
        """Zero for an ordinary clip. A Text+ instance that answers zero has lost its comp."""
        self._check("GetFusionCompCount")
        return len(self.comps)

    def GetFusionCompByIndex(self, index: int) -> FakeFusionComp | None:  # noqa: N802
        """One-based; an index Resolve has no comp for returns ``None`` rather than raising."""
        self._check("GetFusionCompByIndex")
        if 1 <= index <= len(self.comps):
            return self.comps[index - 1]
        return None


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


class FakeTimeline:
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
        owner: FakeResolve | None = None,
    ) -> None:
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
        self.marker_writes: list[dict[str, Any]] = []
        self.refuse_markers = False
        # Refusing one marker by name is how a failed *replacement* is staged: Resolve
        # takes the delete, refuses the add, and the restore of the displaced marker has
        # to be free to succeed or the test could not tell a restore from a loss.
        self.refuse_marker_names: set[str] = set()
        self.exports: list[tuple[str, Any, tuple[Any, ...]]] = []
        self.export_result = True
        self.export_writes_the_file = True
        self.add_track_result = True

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for tracks in self._tracks.values():
            for track in tracks:
                for item in track.items:
                    item.adopt(owner)

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def _track(self, track_type: str, index: int) -> FakeTrack | None:
        tracks = self._tracks.get(track_type, [])
        return tracks[index - 1] if 1 <= index <= len(tracks) else None

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
        return list(track.items) if track else None

    def GetIsTrackEnabled(self, track_type: str, index: int) -> bool:  # noqa: N802
        self._check()
        track = self._track(track_type, index)
        return bool(track and track.enabled)

    def GetIsTrackLocked(self, track_type: str, index: int) -> bool:  # noqa: N802
        self._check()
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
        answers True and nothing lands on disk.
        """
        self._check()
        self.exports.append((file_name, export_type, subtype))
        if not self.export_result:
            return False
        if self.export_writes_the_file:
            target = Path(file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fake-export of {self._name}", encoding="utf-8")
        return True

    def AddTrack(self, track_type: str, *_: Any) -> bool:  # noqa: N802
        self._check()
        if not self.add_track_result:
            return False
        tracks = self._tracks.setdefault(track_type, [])
        tracks.append(FakeTrack(f"{track_type.capitalize()} {len(tracks) + 1}"))
        return True

    def place(self, track_type: str, index: int, item: FakeTimelineItem) -> None:
        """Put an item on a track, in start order — the pool's append reaches through here."""
        track = self._track(track_type, index)
        if track is None:
            return
        track.items.append(item)
        track.items.sort(key=lambda placed: placed.GetStart())


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".tga"}

DEFAULT_PROPERTIES: dict[str, str] = {
    "Type": "Video",
    "FPS": "59.94",
    "Resolution": "1920x1080",
    "Frames": "100",
    "Start": "0",
    "End": "99",
    "Duration": "00:00:01:16",
    "Audio Ch": "2",
    "Format": "MP4",
    "Video Codec": "H.264",
    "Start TC": "01:00:00:00",
}


class FakeMediaPoolItem:
    """A media pool clip. Properties and metadata are strings, as in the real API."""

    def __init__(
        self,
        name: str,
        file_path: str = "",
        properties: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
        markers: dict[float, dict[str, Any]] | None = None,
        audio_mapping: str | None = None,
        mark_in_out: dict[str, dict[str, int]] | None = None,
        template_comp: FakeFusionComp | None = None,
    ) -> None:
        # Deliberately not a getter: a pool item has no Fusion comp in the scripting API.
        # This is the comp the *placed instance* is given, which is the only way a Text+
        # template's comp can reach a timeline item at all.
        self.template_comp = template_comp
        self._name = name
        self._properties = {**DEFAULT_PROPERTIES, "Clip Name": name, "File Path": file_path}
        self._properties.update(properties or {})
        self._metadata = dict(metadata or {})
        self._markers = dict(markers or {})
        self._audio_mapping = audio_mapping
        self._mark_in_out = dict(mark_in_out or {})
        self.property_writes: list[tuple[str, str]] = []
        self.metadata_writes: list[tuple[str, str]] = []
        self.refuse_properties: set[str] = set()
        self.refuse_metadata: set[str] = set()

    def GetName(self) -> str:  # noqa: N802
        return self._name

    def GetClipProperty(self, name: str | None = None) -> str | dict[str, str] | None:  # noqa: N802
        if name is None:
            return dict(self._properties)
        return self._properties.get(name)

    def SetClipProperty(self, name: str, value: str) -> bool:  # noqa: N802
        if name in self.refuse_properties:
            return False
        self.property_writes.append((name, value))
        self._properties[name] = value
        return True

    def GetMetadata(self, key: str | None = None) -> str | dict[str, str] | None:  # noqa: N802
        if key is None:
            return dict(self._metadata)
        return self._metadata.get(key)

    def SetMetadata(self, key: str, value: str) -> bool:  # noqa: N802
        if key in self.refuse_metadata:
            return False
        self.metadata_writes.append((key, value))
        self._metadata[key] = value
        return True

    def GetMarkers(self) -> dict[float, dict[str, Any]]:  # noqa: N802
        return {frame: dict(marker) for frame, marker in self._markers.items()}

    def GetMarkInOut(self) -> dict[str, dict[str, int]]:  # noqa: N802
        return {kind: dict(bounds) for kind, bounds in self._mark_in_out.items()}

    def GetAudioMapping(self) -> str | None:  # noqa: N802 - a JSON *string* in the real API
        return self._audio_mapping

    def ReplaceClip(self, file_path: str) -> bool:  # noqa: N802
        if not Path(file_path).exists():
            return False
        self._properties["File Path"] = file_path
        return True


class FakeFolder:
    def __init__(self, name: str, owner: FakeResolve | None = None) -> None:
        self._name = name
        self._owner = owner
        self.clips: list[FakeMediaPoolItem] = []
        self.subfolders: list[FakeFolder] = []

    def GetName(self) -> str:  # noqa: N802
        return self._name

    def GetClipList(self) -> list[FakeMediaPoolItem]:  # noqa: N802
        self._check()
        return list(self.clips)

    def GetSubFolderList(self) -> list[FakeFolder]:  # noqa: N802
        self._check()
        return list(self.subfolders)

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()


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
        self.add_track_result = True
        self.appends: list[dict[str, Any]] = []
        # The .drb route. Each knob is one outcome a real import has been seen to take:
        # a refusal, a success, and the one that costs an afternoon — True with an
        # untouched pool.
        self.folder_imports: list[tuple[str, str]] = []
        self.import_folder_result: bool | None = None
        self.import_lands_nothing = False
        self.imported_folder: FakeFolder | None = None
        self.append_calls: list[list[dict[str, Any]]] = []
        self.append_result: list[FakeTimelineItem] | None = None
        self.appends_share_one_comp = False
        self.appends_land_nowhere = False
        self.created_timelines: list[str] = []
        self.refuses_create_timeline = False
        self.switches_current_timeline = True
        self.deleted_timelines: list[FakeTimeline] = []
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
        timeline = FakeTimeline(
            str(asked.get("timelineName") or Path(file_path).stem),
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
        self.deleted_timelines.extend(timelines)
        project = self._reached_project()
        if project is None:
            return True
        if any(timeline is project.GetCurrentTimeline() for timeline in timelines):
            return False
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
        media_type = info.get("mediaType")
        track_type = "audio" if media_type == AUDIO_TYPE else "video"
        index = int(info.get("trackIndex", 1))
        record = int(info.get("recordFrame", _track_end(timeline, track_type, index)))

        item = FakeTimelineItem(
            clip.GetName(),
            record,
            duration,
            source_start=source_start,
            media_item=clip,
            comps=comps,
            owner=self._owner,
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
                )
        if not self.appends_land_nowhere:
            timeline.place(track_type, index, item)
        return item

    def _walk(self, folder: FakeFolder) -> list[FakeFolder]:
        found = [folder]
        for sub in folder.subfolders:
            found.extend(self._walk(sub))
        return found


AUDIO_TYPE = 2
STILL_DEFAULT_FRAMES = 120


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


def _import_one(item: str | dict[str, Any]) -> FakeMediaPoolItem | None:
    """Mimic ImportMedia: a path that is not there imports nothing.

    An imported sequence is named and pathed after its first frame rather than after the
    ``%0Nd`` pattern. Nothing on record says what Resolve really calls it (#18 verified the
    frame count only), so the fake deliberately does not echo the pattern back — code that
    matched on it would be relying on an assumption no source supports.
    """
    if isinstance(item, dict):
        pattern = str(item.get("FilePath", ""))
        start = int(item.get("StartIndex", 0))
        end = int(item.get("EndIndex", 0))
        frames = max(end - start + 1, 0)
        if not _sequence_exists(pattern, start):
            return None
        first = _first_frame(pattern, start)
        return FakeMediaPoolItem(
            Path(first).name,
            first,
            {"Type": "Image Sequence", "Frames": str(frames), "Start": "0", "End": str(frames - 1)},
        )

    path = Path(item)
    if not path.exists():
        return None
    still = path.suffix.lower() in IMAGE_SUFFIXES
    return FakeMediaPoolItem(
        path.name,
        str(path),
        {"Type": "Still", "Frames": "1", "Start": "0", "End": "0"} if still else {},
    )


def _first_frame(pattern: str, start: int) -> str:
    try:
        return pattern % start
    except (TypeError, ValueError):
        return pattern


def _sequence_exists(pattern: str, start: int) -> bool:
    return Path(_first_frame(pattern, start)).exists()


class FakeProject:
    """A project, including its render queue.

    The queue models what makes the real one hard to drive: settings live on the project
    rather than on the job, ``StartRendering`` returns before the render is done, and a job
    reports a status per poll — so ``render_statuses`` is a sequence handed out one per
    ``GetRenderJobStatus`` call, the last one repeating. ``render_writes_the_file=False``
    models the failure the return value hides: every call answers True and nothing lands.
    """

    def __init__(
        self,
        name: str,
        timeline: FakeTimeline | None = None,
        fps: str = "24",
        media_pool: FakeMediaPool | None = None,
        timelines: list[FakeTimeline | None] | None = None,
    ) -> None:
        """``timelines`` may hold a ``None``: Resolve sometimes answers an index with one."""
        self._name = name
        self._timeline = timeline
        self._fps = fps
        self._media_pool = media_pool
        if timelines is not None:
            self._timelines = list(timelines)
        else:
            self._timelines = [timeline] if timeline is not None else []
        self.render_settings: dict[str, Any] = {}
        self.render_format: tuple[str, str] | None = None
        self.render_mode: int | None = None
        self.render_queue: list[str] = []
        self.render_jobs: list[dict[str, Any]] = []
        self.render_statuses: list[str] = ["Complete"]
        self.render_seconds = 2.0
        self.render_writes_the_file = True
        self.accepts_format = True
        self.accepts_settings = True
        self.accepts_job = True
        self.starts_rendering = True
        self.refuse_set_current = False
        self.timeline_switches: list[str] = []
        self._status_calls = 0
        if media_pool is not None:
            media_pool.attach_project(self)

    def SetCurrentRenderMode(self, mode: int) -> bool:  # noqa: N802
        self.render_mode = mode
        return True

    def SetCurrentRenderFormatAndCodec(self, format_: str, codec: str) -> bool:  # noqa: N802
        if not self.accepts_format:
            return False
        self.render_format = (format_, codec)
        return True

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:  # noqa: N802
        if not self.accepts_settings:
            return False
        self.render_settings = dict(settings)
        return True

    def AddRenderJob(self) -> str | None:  # noqa: N802
        """Returns the new job's id, or ``None`` when Resolve refuses it."""
        if not self.accepts_job:
            return None
        job_id = f"render-{len(self.render_jobs) + 1}"
        self.render_jobs.append({"id": job_id, "settings": dict(self.render_settings)})
        self.render_queue.append(job_id)
        return job_id

    def StartRendering(self, *job_ids: str) -> bool:  # noqa: N802
        if not self.starts_rendering:
            return False
        if self.render_writes_the_file:
            self._write_the_render()
        return True

    def GetRenderJobStatus(self, job_id: str) -> dict[str, Any]:  # noqa: N802
        """One status per poll, the last one repeating — a render is watched, not awaited."""
        index = min(self._status_calls, len(self.render_statuses) - 1)
        self._status_calls += 1
        status = self.render_statuses[index]
        return {
            "JobStatus": status,
            "CompletionPercentage": 100 if status == "Complete" else index * 10,
        }

    def DeleteRenderJob(self, job_id: str) -> bool:  # noqa: N802
        if job_id in self.render_queue:
            self.render_queue.remove(job_id)
            return True
        return False

    def _write_the_render(self) -> None:
        target = Path(str(self.render_settings.get("TargetDir", "")))
        name = str(self.render_settings.get("CustomName", "render"))
        write_wav(
            target / f"{name}.wav",
            seconds=self.render_seconds,
            sample_rate=int(self.render_settings.get("AudioSampleRate", 48000)),
            bit_depth=int(self.render_settings.get("AudioBitDepth", 24)),
        )

    def GetName(self) -> str:  # noqa: N802
        return self._name

    def GetCurrentTimeline(self) -> FakeTimeline | None:  # noqa: N802
        return self._timeline

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:  # noqa: N802
        """Resolve only appends to the current timeline, so the build has to set it.

        ``refuse_set_current`` models a Resolve that will not switch.
        """
        if self.refuse_set_current:
            return False
        self.timeline_switches.append(str(timeline.GetName()))
        self._timeline = timeline
        return True

    def add_timeline(self, timeline: FakeTimeline) -> None:
        """What creating a timeline does to a project — the pool reaches through here."""
        self._timelines.append(timeline)

    def remove_timeline(self, timeline: FakeTimeline) -> None:
        """Deleting a cut the project never held is a no-op, as in Resolve."""
        self._timelines = [held for held in self._timelines if held is not timeline]
        if self._timeline is timeline:
            self._timeline = None

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline | None:  # noqa: N802
        """One-based, as in the real API; out of range returns ``None`` rather than raising."""
        if 1 <= index <= len(self._timelines):
            return self._timelines[index - 1]
        return None

    def GetSetting(self, key: str) -> str | None:  # noqa: N802
        return self._fps if key == "timelineFrameRate" else None

    def GetMediaPool(self) -> FakeMediaPool | None:  # noqa: N802
        return self._media_pool


class FakeProjectManager:
    def __init__(self, owner: FakeResolve) -> None:
        self._owner = owner
        self.exports: list[tuple[str, str, bool]] = []
        self.export_result = True
        self.save_result = True
        self.calls: list[str] = []

    def _check(self, method: str) -> None:
        self.calls.append(method)
        self._owner._check()

    def GetProjectListInCurrentFolder(self) -> list[str]:  # noqa: N802
        self._check("GetProjectListInCurrentFolder")
        return list(self._owner.projects)

    def GetCurrentProject(self) -> FakeProject | None:  # noqa: N802
        self._check("GetCurrentProject")
        return self._owner.current_project

    def SaveProject(self) -> bool:  # noqa: N802
        self._check("SaveProject")
        return self.save_result

    def LoadProject(self, name: str) -> FakeProject | None:  # noqa: N802
        self._check("LoadProject")
        if name not in self._owner.projects:
            return None
        self._owner.current_project = self._owner.projects[name]
        return self._owner.current_project

    def ExportProject(  # noqa: N802
        self,
        project_name: str,
        file_path: str,
        with_stills_and_luts: bool = False,
    ) -> bool:
        self._check("ExportProject")
        self.exports.append((project_name, file_path, with_stills_and_luts))
        if self.export_result:
            Path(file_path).write_bytes(b"fake-drp")
        return self.export_result


EXPORT_TYPES: tuple[str, ...] = (
    # First in the tuple, so its value is 0 — the export constants are plain numbers and
    # one of them is falsy, which is the trap a "constant or default" lookup falls into.
    "EXPORT_AAF",
    "EXPORT_DRT",
    "EXPORT_EDL",
    "EXPORT_FCP_7_XML",
    "EXPORT_FCPXML_1_3",
    "EXPORT_FCPXML_1_4",
    "EXPORT_FCPXML_1_5",
    "EXPORT_FCPXML_1_6",
    "EXPORT_FCPXML_1_7",
    "EXPORT_FCPXML_1_8",
    "EXPORT_FCPXML_1_9",
    "EXPORT_FCPXML_1_10",
    "EXPORT_OTIO",
)


class FakeResolve:
    """A stand-in for the object ``scriptapp("Resolve")`` returns."""

    def __init__(
        self,
        projects: dict[str, FakeProject] | None = None,
        current: str | None = None,
        version: list[Any] | None = None,
        export_types: Sequence[str] | None = None,
    ) -> None:
        self.projects: dict[str, FakeProject] = projects or {}
        self.current_project: FakeProject | None = self.projects.get(current or "")
        self.version = version or [21, 0, 3, 15, ""]
        # Export types are attributes on the app object itself, and an older build simply
        # does not have the newer ones — so a narrowed tuple models that build exactly.
        self.export_types = tuple(EXPORT_TYPES if export_types is None else export_types)
        for value, export_type in enumerate(self.export_types):
            setattr(self, export_type, value)
        self.alive = True
        self.probe_count = 0
        self.fail_version_string = False
        self._calls_left: int | None = None
        self._project_manager = FakeProjectManager(self)

    def drop(self) -> None:
        """Simulate Resolve quitting: every call through this handle now fails."""
        self.alive = False

    def die_after(self, calls: int) -> None:
        """Survive ``calls`` more calls, then die — Resolve quitting mid-operation.

        With ``calls=1`` the handle passes the connection's probe and dies on the very
        next call, which is the case a probe alone cannot catch.
        """
        self._calls_left = calls

    def _check(self) -> None:
        if self._calls_left is not None:
            if self._calls_left <= 0:
                self.alive = False
            else:
                self._calls_left -= 1
        if not self.alive:
            raise DroppedHandleError("The object is no longer valid")

    def GetVersion(self) -> list[Any]:  # noqa: N802
        self.probe_count += 1
        self._check()
        return list(self.version)

    def GetVersionString(self) -> str:  # noqa: N802
        self._check()
        if self.fail_version_string:
            raise RuntimeError("version string unavailable")
        return ".".join(str(part) for part in self.version[:3])

    def GetProductName(self) -> str:  # noqa: N802
        self._check()
        return "DaVinci Resolve Studio"

    def GetProjectManager(self) -> FakeProjectManager:  # noqa: N802
        self._check()
        return self._project_manager


class FakeConnector:
    """A ``connect`` callable that hands out fakes and counts attempts.

    ``handles`` is consumed one entry per connect attempt; ``None`` models a failed
    connect (Resolve not running), which is exactly what ``scriptapp`` returns.
    """

    def __init__(self, *handles: FakeResolve | None) -> None:
        self._handles: list[FakeResolve | None] = list(handles)
        self.attempts = 0

    def __call__(self) -> FakeResolve | None:
        self.attempts += 1
        if not self._handles:
            return None
        return self._handles.pop(0)


def write_wav(
    path: Path,
    seconds: float = 2.0,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    frequency: float = 440.0,
    silence: Sequence[tuple[float, float]] = (),
) -> Path:
    """A real WAV of a sine tone — the fixture audio the worker tier is tested on.

    Real audio rather than a stub file, because the workers read the header back: a fake
    that wrote ``b"RIFF"`` would pass a duration assertion that means nothing.

    ``silence`` zeroes the given ``(start, end)`` second-ranges, which is what makes this
    fixture usable by the analysis tier: a tone that never stops has no breathing room to
    find, so a silence detector run over it can only ever be asserted to find nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    width = bit_depth // 8
    peak = int(2 ** (bit_depth - 1) * 0.3)
    quiet = [(start * sample_rate, end * sample_rate) for start, end in silence]
    frames = bytearray()
    for index in range(int(seconds * sample_rate)):
        if any(start <= index < end for start, end in quiet):
            frames.extend(b"\x00" * width * channels)
            continue
        sample = int(peak * math.sin(2 * math.pi * frequency * index / sample_rate))
        frames.extend(sample.to_bytes(width, "little", signed=True) * channels)
    with contextlib.closing(wave.open(str(path), "wb")) as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return path


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


def text_plus_template(name: str = "Song Title") -> FakeMediaPoolItem:
    """A GUI-authored Text+ template as it sits in the media pool after a ``.drb`` import.

    It has no file path — a title is generated, not read off disk — so anything that
    treats a pathless clip as broken would trip here.
    """
    return FakeMediaPoolItem(
        name,
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool()]),
    )


def sync_reference(
    name: str = "sunset-set sync",
    fps: str = "59.94",
    angles: dict[str, tuple[int, int, int]] | None = None,
    start_frame: int = 0,
) -> FakeTimeline:
    """The director's stacked layout: one angle per video track, each landing on its own frame.

    ``angles`` maps a track name to ``(record_start, source_start, duration)``. That is the
    shape a hand-synced reference has, and the per-angle sync offset is the difference
    between the two starts.
    """
    angles = angles or {"Cam A": (0, 1000, 500), "Cam B": (120, 3000, 400)}
    video = [
        FakeTrack(
            track,
            [FakeTimelineItem(f"{track}.mp4", record, duration, source_start=source)],
        )
        for track, (record, source, duration) in angles.items()
    ]
    return FakeTimeline(name, fps, start_frame=start_frame, video=video)


def studio(
    project: str | None = "sunset-set",
    timeline: str | FakeTimeline | None = "sunset-set v3",
    fps: str = "59.94",
    extra_projects: tuple[str, ...] = ("holiday-gig",),
    pool: FakeMediaPool | None = None,
    timelines: list[FakeTimeline | None] | None = None,
    export_types: Sequence[str] | None = None,
) -> FakeResolve:
    """A conventional fake: Studio running, one project open, one timeline current.

    ``timeline`` is the current one, by name or as a built ``FakeTimeline``; ``timelines``
    is everything the project holds, defaulting to the current one alone. Passing
    ``timelines`` makes the first of them current, unless ``timeline`` says otherwise —
    ``None`` for a project whose timelines are all closed.
    """
    if isinstance(timeline, FakeTimeline) or timeline is None:
        current = timeline
    elif timelines is not None:
        current = timelines[0] if timelines else None
    else:
        current = FakeTimeline(timeline, fps)
    projects: dict[str, FakeProject] = {}
    for name in extra_projects:
        projects[name] = FakeProject(name, fps=fps)
    if project is not None:
        projects[project] = FakeProject(
            project,
            current,
            fps=fps,
            media_pool=pool,
            timelines=timelines,
        )
    resolve = FakeResolve(projects, current=project, export_types=export_types)
    if pool is not None:
        pool.adopt(resolve)
    owned = [one for one in (timelines or []) if one is not None]
    if current is not None and current not in owned:
        owned.append(current)
    for one in owned:
        one.adopt(resolve)
    return resolve
