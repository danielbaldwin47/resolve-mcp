"""PNG title assets: what an event points at on disk, and how many frames stand behind it.

The PNG route consumes designed cards; it never makes them (#14 §4). A card is exported
as frames — the alpha ramp baked in at the head, the hold frozen in the middle, the ramp
out at the tail — and this module is the one place that answers what is actually there.

Two decisions live here rather than in the rules that use them:

* **A relative asset path is relative to the titles file, never to the server's cwd.** The
  file and its cards travel together in a project folder; resolving against the process
  would make the same file valid from one directory and broken from another.
* **Frames are counted off disk, not declared in the file.** ``ImportMedia`` needs a start
  and end index, and a count the file merely *claims* would be a second source of truth
  that drifts the moment a card is re-baked. Counting is also what lets T10 and T11 say
  "the sequence is not there" and "it is 480 frames, not the 500 asked for" before Resolve
  is touched at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .schema import ASSET_BIN

SEQUENCE_TOKEN: Final = "%"
BIN_SEPARATOR: Final = "/"
# Both mirror ``resolve.pool`` deliberately rather than importing it: this layer is the
# pure one, and a titles file has to be judged on a machine with no Resolve on it.

SEQUENCE_INDEX: Final = re.compile(r"%0?\d*d")
"""The printf token an exported sequence is named with: ``title_%04d.png``."""


@dataclass(frozen=True)
class Asset:
    """One event's card, resolved against disk.

    ``frames`` is what is really there — zero when nothing is — so a missing sequence and
    a mis-baked one are the same reading answered by two different rules, and the apply
    gets the start index it needs for ``ImportMedia`` without looking at the disk again.
    """

    event: str
    song: str
    declared: str
    path: Path
    start_index: int | None
    frames: int
    bin_path: str

    @property
    def is_sequence(self) -> bool:
        """Whether the asset is a ``%0Nd`` frame run rather than one still image."""
        return self.start_index is not None

    @property
    def missing(self) -> bool:
        return self.frames == 0

    def request(self) -> str | dict[str, Any]:
        """The ``ImportMedia`` argument for this asset: a path, or a sequence descriptor."""
        if self.start_index is None:
            return str(self.path)
        return {
            "FilePath": str(self.path),
            "StartIndex": self.start_index,
            "EndIndex": self.start_index + self.frames - 1,
        }

    def first_frame(self) -> str:
        """The one file that really exists — the identity a re-run recognises the clip by.

        A sequence's ``File Path`` is a label rather than a file (``card_[0001-0480].png``,
        #85), and the ``%0Nd`` pattern is not a file either. The first frame is the only
        form of the address that is the same string before the import and after it.
        """
        if self.start_index is None:
            return str(self.path)
        return _frame_path(self.path, self.start_index)


def resolve_asset(event: Mapping[str, Any], song: str, *, base: Path) -> Asset:
    """What one PNG event points at, counted off disk. Never raises on a missing card."""
    declared = str(event.get("asset", ""))
    path = Path(declared)
    resolved = path if path.is_absolute() else (base / path)
    start, frames = frames_on_disk(resolved)
    return Asset(
        event=str(event["id"]),
        song=song,
        declared=declared,
        path=resolved,
        start_index=start,
        frames=frames,
        bin_path=bin_for(event, song),
    )


def bin_for(event: Mapping[str, Any], song: str) -> str:
    """Where the card lands in the media pool: the event's own bin, or the convention.

    The convention is ``04_Assets/Text/<song key>`` (#57), which is where a human titling
    the same set by hand would have put it. An explicit ``bin`` always wins, and nothing is
    ever refused for being off-convention.
    """
    declared = event.get("bin")
    if isinstance(declared, str) and declared.strip():
        return declared
    return f"{ASSET_BIN}{BIN_SEPARATOR}{song}"


def frames_on_disk(path: Path) -> tuple[int | None, int]:
    """``(start index, frame count)`` for an asset path — ``(None, n)`` for a still image.

    A sequence is counted as the *contiguous* run from its lowest frame: Resolve imports a
    start and an end index and reads every frame between them, so a run with a hole in it
    is not a shorter sequence, it is a broken one — and reporting the run that Resolve
    would actually get is what makes T11's frame count mean something.
    """
    if SEQUENCE_TOKEN not in path.name:
        return None, 1 if path.is_file() else 0

    token = SEQUENCE_INDEX.search(path.name)
    if token is None:
        return None, 0
    found = _indices_on_disk(path, token.group())
    if not found:
        return None, 0

    start = min(found)
    frames = 0
    while start + frames in found:
        frames += 1
    return start, frames


def _indices_on_disk(path: Path, token: str) -> set[int]:
    """Every frame number present in the asset's folder, read from the file names."""
    if not path.parent.is_dir():
        return set()
    pattern = re.compile(
        "".join(
            r"(\d+)" if part == token else re.escape(part)
            for part in _split_once(path.name, token)
        )
        + "$"
    )
    found: set[int] = set()
    for entry in path.parent.iterdir():
        match = pattern.match(entry.name)
        if match is not None and entry.is_file():
            found.add(int(match.group(1)))
    return found


def _split_once(name: str, token: str) -> list[str]:
    head, _, tail = name.partition(token)
    return [head, token, tail]


def _frame_path(path: Path, index: int) -> str:
    token = SEQUENCE_INDEX.search(path.name)
    if token is None:
        return str(path)
    return str(path.with_name(path.name.replace(token.group(), token.group() % index, 1)))


__all__ = ["Asset", "bin_for", "frames_on_disk", "resolve_asset"]
