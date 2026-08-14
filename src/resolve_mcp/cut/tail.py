"""The cut file's tail treatment: how the picture leaves, and how the mix goes with it.

A gap is a *hard* edge — the picture stops on one frame — and the finished work in this
corpus does not end that way: the picture ramps to black over seconds while the band plays
on, and the mix fades under it and outlives it (the concert style profile, §5b, five
deliverables). So the cut file carries one optional ``tail`` object, and this module is the
one reading of it. Validation and the build both come here, because a tail the rules
measured and the build then materialised differently would be a device that passes and
does not arrive.

Two frame counts, deliberately independent. The dissolve reaches *back* into the last shot
on V1 and lands on black at the cut's last picture frame; the audio fade ends where the
mix ends, which is normally later. That is not a coincidence to be tidied away — it is the
measured shape, and tying the two together would make the corpus's own tail inexpressible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

DISSOLVE_TO_BLACK: Final = "dissolve_to_black"
"""The picture ramps to black over ``duration_frames``, landing on the last picture frame."""

HARD_TO_BLACK: Final = "hard_to_black"
"""The picture stops dead and only the mix fades — two of the five deliverables do this."""

TYPES: Final = (DISSOLVE_TO_BLACK, HARD_TO_BLACK)

KEYS: Final = ("type", "duration_frames", "audio_fade_frames")
"""Every key a tail may carry. Anything else is a typo, and a typo here fades nothing."""


@dataclass(frozen=True)
class Tail:
    """One cut's tail, as both the rules and the build read it.

    ``frames`` is 0 for a hard out and ``audio_frames`` is 0 when the mix does not fade, so
    a caller asks "is there a dissolve" by the count rather than by re-reading the type.
    """

    kind: str
    frames: int
    audio_frames: int

    @property
    def dissolves(self) -> bool:
        return self.kind == DISSOLVE_TO_BLACK and self.frames > 0

    @property
    def fades_audio(self) -> bool:
        return self.audio_frames > 0

    def as_dict(self) -> dict[str, Any]:
        """What the build report says landed — the same names the cut file uses."""
        return {
            "type": self.kind,
            "duration_frames": self.frames,
            "audio_fade_frames": self.audio_frames,
        }


def read(doc: dict[str, Any]) -> Tail | None:
    """The tail this document asks for, or ``None`` when it ends the way v1 always did.

    Only ever called on a document the shape rules have already passed, so every field it
    reads is one E1 has been over.
    """
    tail = doc.get("tail")
    if not isinstance(tail, dict):
        return None
    return Tail(
        kind=str(tail.get("type")),
        frames=int(tail.get("duration_frames") or 0),
        audio_frames=int(tail.get("audio_fade_frames") or 0),
    )


__all__ = ["DISSOLVE_TO_BLACK", "HARD_TO_BLACK", "KEYS", "TYPES", "Tail", "read"]
