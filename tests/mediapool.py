"""Media pool builders the two media test files share.

``test_media_pool.py`` and ``test_media_tools.py`` were one file until the module they
cover split in two; these are the fixtures both halves still need, kept in one place so
the pair cannot drift apart on what a clip or a shadowed copy looks like.
"""

from __future__ import annotations

from pathlib import Path

from .fakes import FakeMediaPool, FakeMediaPoolItem, media_pool


def a_file(tmp_path: Path, name: str, content: bytes = b"media") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def a_clip(path: Path | str, **properties: str) -> FakeMediaPoolItem:
    return FakeMediaPoolItem(Path(path).name, str(path), properties)


def a_shallow_copy_pool(tmp_path: Path) -> FakeMediaPool:
    """The #134 shape: one clip name held by a bin *and* by a bin nested inside it."""
    same = a_file(tmp_path, "C0012.mp4")
    return media_pool(
        bins={
            "Angles": [a_clip(same, Description="in Angles")],
            "Angles/Cam A": [a_clip(same, Description="nested")],
        }
    )
