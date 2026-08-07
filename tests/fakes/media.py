"""Media pool contents: clips, bins, and the import helpers that build clips from paths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fusion import FakeFusionComp, FakeFusionTool

if TYPE_CHECKING:
    from .connection import FakeResolve


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
        """Mimic the real call's side effect: the pool clip is renamed after the new file.

        Verified on Resolve Studio 21.0.3.7 (#85): after a replace, the clip no longer
        answers to the name the caller used.
        """
        if not Path(file_path).exists():
            return False
        self._properties["File Path"] = file_path
        self._name = Path(file_path).name
        self._properties["Clip Name"] = self._name
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


AUDIO_TYPE = 2
STILL_DEFAULT_FRAMES = 120


def _import_one(item: str | dict[str, Any]) -> FakeMediaPoolItem | None:
    """Mimic ImportMedia: a path that is not there imports nothing.

    An imported sequence is named and pathed by folding the index range into the ``%0Nd``
    token — ``shot_%04d.png`` with frames 1–24 becomes ``shot_[0001-0024].png`` — which is
    what Resolve Studio 21.0.3.7 really reports (#85): a label, not a path that exists on
    disk. ``File Path`` and the clip name both carry the bracketed form.

    ``Type`` is ``Video``, live-verified twice (#85 body, #95 probe): Resolve does not
    separate a sequence from moving footage here, which is why
    :func:`resolve_mcp.resolve.media.is_still` keys off the file suffix instead.
    """
    if isinstance(item, dict):
        pattern = str(item.get("FilePath", ""))
        start = int(item.get("StartIndex", 0))
        end = int(item.get("EndIndex", 0))
        frames = max(end - start + 1, 0)
        if not _sequence_exists(pattern, start):
            return None
        label = _sequence_label(pattern, start, end)
        return FakeMediaPoolItem(
            Path(label).name,
            label,
            {"Type": "Video", "Frames": str(frames), "Start": "0", "End": str(frames - 1)},
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


def _sequence_label(pattern: str, start: int, end: int) -> str:
    """The bracketed name Resolve gives an imported sequence: ``shot_[0001-0024].png``."""
    token = re.search(r"%0?\d*d", pattern)
    if token is None:
        return pattern
    return pattern.replace(token.group(), f"[{token.group() % start}-{token.group() % end}]")


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
