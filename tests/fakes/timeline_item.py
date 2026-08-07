"""``FakeTimelineItem`` — one clip on a track, with its markers, flags and Fusion comps."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .core import AnswersNone

if TYPE_CHECKING:
    from .connection import FakeResolve
    from .fusion import FakeFusionComp
    from .media import FakeMediaPoolItem
    from .timeline import FakeTimeline


class FakeTimelineItem(AnswersNone):
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
    (``GetTakeCount`` is not an API method at all; the real one is ``GetTakesCount``, which
    is why that particular name was the one caught being ``None``.)

    A name in :attr:`NOT_API_METHODS` is answered that way on *every* item, without a test
    opting in — see the constant for why that set is not the same thing as ``missing``.

    The take selector is modelled with the same suspicion as the append: ``AddTake`` and
    ``SelectTakeByIndex`` both answer ``Bool``, so ``takes_land`` and ``select_take_lands``
    model the answer that lies — a truthy return over a selector that did not change.
    ``FinalizeTake`` is deliberately absent: it collapses a selector permanently, so a
    wrapper that called it should fail loudly here rather than quietly on a real cut.
    """

    #: Names no Resolve build declares, as against ``missing``, which is a name some build
    #: has and this one does not: nothing can opt out of these, because there is no build
    #: to opt into. ``GetTakeCount`` is the singular a wrapper reaches for when it means
    #: ``GetTakesCount`` (#68), and reading it looked exactly like reading zero takes.
    NOT_API_METHODS = frozenset({"GetTakeCount"})

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
        add_take_result: bool = True,
        takes_land: bool = True,
        select_take_result: bool = True,
        select_take_lands: bool = True,
    ) -> None:
        self._name = name
        self._start = start
        self._duration = duration
        self._source_start = source_start
        self._left_offset = left_offset
        self._source_end = source_end
        self._media_item = media_item
        self._enabled = enabled
        self._selector: list[dict[str, Any]] = [self._own_take() for _ in range(takes)]
        self._selected = 1 if takes else 0
        # Public so a test can break a selector *after* a build has made one — the item is
        # created inside the build, so the constructor is not a seam a swap test can reach.
        self.add_take_result = add_take_result
        self.takes_land = takes_land
        self.select_take_result = select_take_result
        self.select_take_lands = select_take_lands
        self._supports_source_frames = supports_source_frames
        self._refuses = set(refuses or ())
        self._end_is_inclusive = end_is_inclusive
        self.comps: list[FakeFusionComp] = list(comps or ())
        self._missing = set(missing or ()) | FakeTimelineItem.NOT_API_METHODS
        self._owner = owner
        self._timeline: FakeTimeline | None = None

    SOURCE_GETTERS = ("GetSourceStartFrame", "GetSourceEndFrame")

    def __getattribute__(self, name: str) -> Any:
        """Hide the source getters entirely on a build that predates them.

        Absent is not the same as ``missing``: a Resolve older than 18.5 does not have
        these at all, so ``getattr`` misses them — which is the branch the wrapper takes.
        Everything else answers ``None`` the way :class:`AnswersNone` describes.
        """
        if name in FakeTimelineItem.SOURCE_GETTERS and not object.__getattribute__(
            self, "_supports_source_frames"
        ):
            raise AttributeError(name)
        return super().__getattribute__(name)

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for comp in self.comps:
            comp.adopt(owner)

    def held_by(self, timeline: FakeTimeline) -> None:
        """Remember which timeline this shot sits on — #84 is a fact about that timeline."""
        self._timeline = timeline

    def _reads_current(self) -> bool:
        """Whether a getter on this item would be answered truthfully (see #84).

        An item nothing has claimed reads truthfully: a test that never went through a
        timeline is not testing currency, and defaulting the other way would make every
        unrelated take assertion depend on project state it never set up.
        """
        timeline = self._timeline
        if timeline is None or not timeline.getters_need_current:
            return True
        return timeline._is_current()

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

    def _own_take(self) -> dict[str, Any]:
        """The clip already on the track, as the take Resolve seeds a new selector with."""
        start = self._source_start or 0
        return {
            "startFrame": start,
            "endFrame": start + self._duration,
            "mediaPoolItem": self._media_item,
        }

    def AddTake(  # noqa: N802
        self,
        media_item: FakeMediaPoolItem,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> bool:
        """Add a take, seeding the selector from the placed clip when there is none yet."""
        self._check("AddTake")
        if not self.add_take_result:
            return False
        if self.takes_land:
            if not self._selector:
                self._selector.append(self._own_take())
            self._selector.append(
                {
                    "startFrame": start_frame,
                    "endFrame": end_frame,
                    "mediaPoolItem": media_item,
                }
            )
            # *Unverified*: the README does not say where the selection lands after an add.
            # The fake takes the worse of the two possibilities — the new take, not the main
            # one — so a build that leaves the selection to chance shows up here rather than
            # as the wrong angle on a director's timeline.
            self._selected = len(self._selector)
        return True

    def GetTakesCount(self) -> int:  # noqa: N802
        """Zero for a clip that is not a take selector — not one for its own media.

        Also zero, whatever the selector holds, when the holding timeline is not current
        and models #84 — the reading that cost the live pass a wrong conclusion about
        whether a take selector had survived a swap.
        """
        self._check("GetTakesCount")
        if not self._reads_current():
            return 0
        return len(self._selector)

    def GetTakeByIndex(self, index: int) -> dict[str, Any] | None:  # noqa: N802
        self._check("GetTakeByIndex")
        if 1 <= index <= len(self._selector):
            return dict(self._selector[index - 1])
        return None

    def GetSelectedTakeIndex(self) -> int:  # noqa: N802
        """Zero when the clip is not a take selector, else the 1-based selection.

        Also zero off the current timeline, the same #84 lie ``GetTakesCount`` tells — the
        sweep measured this one drifting ``1 -> 0``, and zero is not a take at all.
        """
        self._check("GetSelectedTakeIndex")
        if not self._reads_current():
            return 0
        return self._selected

    def SelectTakeByIndex(self, index: int) -> bool:  # noqa: N802
        self._check("SelectTakeByIndex")
        if not self.select_take_result:
            return False
        if not 1 <= index <= len(self._selector):
            return False
        if self.select_take_lands:
            self._selected = index
        return True

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
