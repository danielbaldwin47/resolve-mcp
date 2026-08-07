"""The rough-cut substrate the P4 tests share: one talking head, said twice.

The concert fixture in ``cutfile.py`` is three shots over one continuous master mix. A
rough cut is the other shape — no mix, an A-roll camera that says a line, flubs it, says it
again, and a b-roll clip that exists only to cover the join. Everything the pillar has to
prove lives in that one take: a retake to choose between, a jump cut to cover, and a word
the transcriber was not sure of.

Times are in seconds because transcripts are; frames are 24ths of them because cut files
are. Both are written out below rather than computed, so a test that disagrees with the
arithmetic disagrees with something readable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.analysis import transcript
from resolve_mcp.analysis.transcript import Word

from .fakes import FakeMediaPoolItem, media_pool, studio

FPS = 24.0

SPOKEN: tuple[Word, ...] = (
    Word("we", 0.0, 0.4, 0.9),
    Word("start", 0.5, 1.0, 0.9),
    Word("uh", 1.2, 1.6, 0.8),
    Word("we", 2.0, 2.4, 0.9),
    Word("start", 2.5, 3.0, 0.9),
    Word("here", 3.2, 3.8, 0.9),
    Word("and", 5.0, 5.4, 0.9),
    Word("finish", 5.5, 6.2, 0.3),
)
"""One pass at camera: a false start, the good take, then a line the model half-heard.

At 24 fps these land on ``we`` [0, 10), ``start`` [12, 24), ``uh`` [28, 39), ``we`` [48,
58), ``start`` [60, 72), ``here`` [76, 92), ``and`` [120, 130), ``finish`` [132, 149) —
in-points floored and out-points ceiled, the same snapping the cut file uses.
"""

GOOD_TAKE = (48, 92)
"""``we start here`` — the second pass, whole."""

FALSE_START = (0, 26)
"""``we start`` — the first pass, abandoned."""

CLOSE = (120, 150)
"""``and finish`` — the line carrying the unsure word."""


def a_transcript(tmp_path: Path, words: tuple[Word, ...] = SPOKEN, name: str = "cam_a") -> str:
    """Write ``words`` as the document ``transcribe_audio`` writes, and return its path."""
    document = transcript.document(
        audio={"path": f"D:/media/{name}.mp4", "seconds": 8.0},
        params={"model": "large-v3"},
        words=words,
        silence=[],
        language="en",
    )
    return str(transcript.write(tmp_path / f"{name}.transcript.json", document))


def a_cut(tmp_path: Path, doc: Any, name: str = "interview.cut.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def delivered(**overrides: Any) -> dict[str, Any]:
    """The rough cut as delivered: the good take, the close, and b-roll over the join.

    The overlay is anchored 36 frames into ``s001`` and runs 16, so it opens before the
    seam at 44 and closes after it — covering a cut from one camera back to itself.
    """
    doc: dict[str, Any] = {
        "schema": 1,
        "timeline": {"name": "interview", "fps": FPS},
        "sources": {
            "cam_a": {"clip": "A001.mp4", "bin": "A-roll"},
            "broll_hands": {"clip": "B012.mp4", "bin": "B-roll"},
        },
        "segments": [
            {"id": "s001", "source": "cam_a", "in": GOOD_TAKE[0], "out": GOOD_TAKE[1]},
            {"id": "s002", "source": "cam_a", "in": CLOSE[0], "out": CLOSE[1]},
        ],
        "overlays": [
            {
                "id": "b01",
                "source": "broll_hands",
                "in": 0,
                "out": 16,
                "over": {"segment": "s001", "offset": 36},
            }
        ],
    }
    doc.update(overrides)
    return doc


def both_takes() -> dict[str, Any]:
    """The cut before the self-review: the abandoned pass still sitting in front of the good one."""
    doc = delivered()
    doc["segments"] = [
        {"id": "s000", "source": "cam_a", "in": FALSE_START[0], "out": FALSE_START[1]},
        *doc["segments"],
    ]
    doc["overlays"] = []
    return doc


def with_alternate() -> dict[str, Any]:
    """The delivered cut with the false start kept as a take the director can flip back to.

    An alternate must match its main frame for frame (E8), so the abandoned pass is offered
    at the good take's length rather than its own.
    """
    doc = delivered()
    length = GOOD_TAKE[1] - GOOD_TAKE[0]
    doc["segments"][0]["alternates"] = [
        {"source": "cam_a", "in": 0, "out": length},
    ]
    return doc


def a_pool(**clips: FakeMediaPoolItem) -> Any:
    """The pool the rough cut builds against — one A-roll camera, one b-roll clip."""
    cam = clips.get(
        "cam_a",
        FakeMediaPoolItem(
            "A001.mp4",
            file_path="D:/media/A001.mp4",
            properties={"FPS": "24", "Frames": "4000", "Start": "0", "End": "3999"},
        ),
    )
    broll = clips.get(
        "broll_hands",
        FakeMediaPoolItem(
            "B012.mp4",
            file_path="D:/media/B012.mp4",
            properties={"FPS": "24", "Frames": "900", "Start": "0", "End": "899"},
        ),
    )
    return media_pool({"A-roll": [cam], "B-roll": [broll]})


def a_project(**kwargs: Any) -> Any:
    """A project holding that pool and no timelines yet."""
    return studio(timeline=None, timelines=[], pool=a_pool(), **kwargs)


__all__ = [
    "CLOSE",
    "FALSE_START",
    "FPS",
    "GOOD_TAKE",
    "SPOKEN",
    "a_cut",
    "a_pool",
    "a_project",
    "a_transcript",
    "both_takes",
    "delivered",
    "with_alternate",
]
