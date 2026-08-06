"""Take selectors: the angle choices a built cut keeps, and the one edit made in place.

Everything else in the cut model is declarative — change the file, build a new version.
Takes are the exception, and they earn it: a review round is a director saying "try the
other angle on that shot", and rebuilding a whole concert to answer that would cost
minutes for a change that alters one frame range.

Two rules make the exception safe, and both are the schema's, not this file's:

* **Equal durations.** An alternate is the same length as the main take, so selecting it
  cannot ripple the sequential V1. An unequal-length choice is a main-segment edit and a
  rebuild — there is no in-place answer to it.
* **Main is the selection.** The selector is ``[main, *alternates]`` and the build leaves
  it sitting on take 1, so a freshly built timeline always shows what the cut file says.
  After a swap the two disagree until the agent edits the file, which is why every swap
  reports the exact edit that puts them back in step. The server never writes the file.

``AddTake`` and ``SelectTakeByIndex`` both answer a bare ``Bool``, so — as everywhere else
at this seam — the answer is counted and the selector read back, never believed. The one
call this module deliberately never makes is ``FinalizeTake``: it collapses a selector into
a plain clip permanently, which would throw away every alternate the cut file describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..cut.document import read_cut_file
from ..cut.validate import placements, validate_structure
from ..document import LoadedDocument
from ..findings import Finding
from ..errors import (
    BuildFailedError,
    CutInvalidError,
    InvalidRequestError,
    ResolveMcpError,
    TimelineOperationError,
)
from ..logging_config import get_logger
from ..timing import dual_time
from . import timeline as timeline_read
from .connection import ResolveConnection

log = get_logger("takes")

Clip = Any
Timeline = Any
TimelineItem = Any

MAIN_TAKE: Final = 1
"""The main clip's slot. The build ends every selector here; a swap counts from here."""

VIDEO_TRACK: Final = timeline_read.FIRST_TRACK
"""Sequential V1: a segment's selector is always on the video track the build placed it on."""


@dataclass(frozen=True)
class Take:
    """One alternate, resolved to the media it plays and the frames it plays of it."""

    source: str
    clip: Clip
    name: str
    source_in: int
    duration: int

    @property
    def source_out(self) -> int:
        """Half-open, the same as an append's ``endFrame`` — the cut file's only convention."""
        return self.source_in + self.duration


@dataclass(frozen=True)
class Selector:
    """One segment's alternates, and the record frame of the shot they hang off."""

    segment: str
    record: int
    takes: tuple[Take, ...]

    @property
    def wanted(self) -> int:
        """Takes the finished selector must hold: the main clip plus every alternate."""
        return len(self.takes) + MAIN_TAKE


# --- building ---------------------------------------------------------------------------------


def attach_takes(timeline: Timeline, selectors: list[Selector], name: str) -> int:
    """Turn each segment that has alternates into a take selector sitting on its main clip."""
    if not selectors:
        return 0
    placed = _items_by_record(timeline)
    for selector in selectors:
        item = placed.get(selector.record)
        if item is None:
            # _verify has already matched every placement, so this is unreachable by a
            # build that got here — and still worth refusing rather than skipping.
            raise BuildFailedError(
                cause=f"The shot for segment {selector.segment!r} is no longer on V1 of "
                f"{name!r}, so its alternates could not be attached.",
                fix="Delete the version this build made and build again.",
                detail={"timeline": name, "segment": selector.segment},
            )
        _add_takes(item, selector, name)
        _select(item, MAIN_TAKE, _selector_failure(selector, name))
    log.info("Attached take selectors to %d segment(s) of %s", len(selectors), name)
    return len(selectors)


def _add_takes(item: TimelineItem, selector: Selector, name: str) -> None:
    for take in selector.takes:
        if not item.AddTake(take.clip, take.source_in, take.source_out):
            raise BuildFailedError(
                cause=f"Resolve refused to add {take.name!r} as an alternate take on segment "
                f"{selector.segment!r}.",
                fix="Check the alternate's source clip is online and its range is inside the "
                "media, then build again — inspect_clip reports both.",
                detail={
                    "timeline": name,
                    "segment": selector.segment,
                    "source": take.source,
                    "clip": take.name,
                },
            )
    found = _takes_count(item)
    if found != selector.wanted:
        raise BuildFailedError(
            cause=f"Segment {selector.segment!r} holds {found} take(s) after adding "
            f"{len(selector.takes)}, not the {selector.wanted} the cut file describes.",
            fix="Delete the version this build made and build again; if it repeats, the "
            "alternates cannot be attached on this footage and the cut needs them removed.",
            detail={
                "timeline": name,
                "segment": selector.segment,
                "takes": {"wanted": selector.wanted, "found": found},
            },
        )


def _selector_failure(selector: Selector, name: str) -> BuildFailedError:
    return BuildFailedError(
        cause=f"The take selector on segment {selector.segment!r} would not settle on its "
        f"main take, so {name!r} may be showing an alternate the cut file does not.",
        fix="Delete the version this build made and build again.",
        detail={"timeline": name, "segment": selector.segment},
    )


# --- swapping ---------------------------------------------------------------------------------


def swap_take(
    connection: ResolveConnection,
    cut_file: str,
    segment: str,
    take: int,
    timeline: str | None = None,
) -> dict[str, Any]:
    """Select take ``take`` on ``segment``'s shot, in place, and report the cut-file edit."""
    loaded = _checked_cut(cut_file)
    # E1 is an error, so a cut file that gets here parsed and matched the schema.
    doc: dict[str, Any] = loaded.doc
    chosen = _segment(doc, segment)
    listed = _selector_of(doc, chosen)
    _refuse_index(segment, take, listed)

    project = timeline_read.open_project(connection)
    target = timeline_read.find_timeline(project, timeline)
    name = timeline_read.name_of(target)
    record, duration = placements(doc, timeline_read.start_frame(target))[segment]
    item = _shot_at(target, record, duration, segment, name)
    _refuse_drift(item, segment, listed, name)

    previous = _selected_take(item)
    changed = previous != take
    if changed:
        _select(item, take, _swap_failure(segment, take, name))
        log.info("Swapped segment %s of %s from take %d to take %d", segment, name, previous, take)

    fps = float(doc["timeline"]["fps"])
    return {
        "cut_file": str(loaded.path),
        "content_hash": loaded.content_hash,
        "timeline": timeline_read.summarise(
            timeline_read.Reader(connection), target, project, name
        ),
        "segment": segment,
        "changed": changed,
        "selected": listed[take - 1],
        "previous": listed[previous - 1] if 1 <= previous <= len(listed) else None,
        "duration": dual_time(duration, fps),
        "selector": listed,
        "sync": _sync(chosen, take),
    }


def _checked_cut(cut_file: str) -> LoadedDocument:
    """The cut file, refused unless it still validates.

    Read once and carried, so the hash the report echoes is the hash of the very bytes the
    swap was computed from — a second read could pick up an edit made in between and put a
    provenance stamp on the report that nothing here ever looked at.
    """
    loaded = read_cut_file(cut_file)
    findings: list[Finding] = (
        [loaded.parse_error] if loaded.parse_error is not None else validate_structure(loaded.doc)
    )
    errors = [finding for finding in findings if finding.severity == "error"]
    if errors:
        raise CutInvalidError(
            cause=f"The cut file has {len(errors)} error(s), so no take was swapped.",
            detail={
                "cut_file": str(loaded.path),
                "content_hash": loaded.content_hash,
                "errors": [finding.as_dict() for finding in errors],
            },
        )
    return loaded


def _segment(doc: dict[str, Any], segment: str) -> dict[str, Any]:
    for candidate in doc["segments"]:
        if str(candidate["id"]) == segment:
            found: dict[str, Any] = candidate
            return found
    available = [str(candidate["id"]) for candidate in doc["segments"]]
    raise InvalidRequestError(
        cause=f"The cut file has no segment {segment!r}.",
        fix=f"Segment ids come from the cut file and must match exactly: {', '.join(available)}.",
        detail={"requested": segment, "available": available},
    )


def _selector_of(doc: dict[str, Any], segment: dict[str, Any]) -> list[dict[str, Any]]:
    """``[main, *alternates]`` as the agent sees it: 1-based, with the clip each take plays."""
    entries = [segment, *(segment.get("alternates") or [])]
    return [
        {
            "index": index,
            "source": str(entry["source"]),
            "clip": str(doc["sources"][str(entry["source"])]["clip"]),
            "in": int(entry["in"]),
            "out": int(entry["out"]),
        }
        for index, entry in enumerate(entries, start=MAIN_TAKE)
    ]


def _refuse_index(segment: str, take: int, listed: list[dict[str, Any]]) -> None:
    """A segment with no alternates has no selector at all, whatever index is asked for.

    Take 1 on such a segment is not a harmless no-op to wave through: the shot on the
    timeline is a plain clip, so there is nothing there to select and nothing the swap
    could confirm. It is refused with the same answer as any other index — add an
    alternate to the cut file, or you are asking for a rebuild rather than a swap.
    """
    if len(listed) == MAIN_TAKE:
        raise InvalidRequestError(
            cause=f"Segment {segment!r} has no alternates, so it has no takes to swap between.",
            fix="Add an equal-duration alternate to this segment in the cut file and build a "
            "new version — a shot with no alternates is a plain clip, not a take selector.",
            detail={"segment": segment, "requested": take, "selector": listed},
        )
    if MAIN_TAKE <= take <= len(listed):
        return
    raise InvalidRequestError(
        cause=f"Segment {segment!r} has {len(listed)} take(s), so take {take} does not exist.",
        fix="Takes are 1-based: 1 is the segment's own source, 2 onwards are its alternates "
        "in the order the cut file lists them. detail.selector says which is which.",
        detail={"segment": segment, "requested": take, "selector": listed},
    )


def _shot_at(
    timeline: Timeline,
    record: int,
    duration: int,
    segment: str,
    name: str,
) -> TimelineItem:
    """The shot this segment built, identified by where it sits and how long it runs.

    Position is identity on a sequential V1, and the length is checked with it: a cut whose
    earlier segments happen to sum to the same frame would otherwise hand back a shot that
    is not this segment's at all, and the swap would land on the wrong angle.
    """
    item = _items_by_record(timeline).get(record)
    if item is not None and timeline_read.read_frames(item.GetDuration()) == duration:
        return item
    raise InvalidRequestError(
        cause=f"No {duration}-frame shot starts at frame {record} on V1 of {name!r}, where this "
        f"cut file puts segment {segment!r}.",
        fix="This timeline was not built from this cut file, or was built from an earlier "
        "state of it. Name the version that was, or build_timeline the file again and swap "
        "on the new version.",
        detail={
            "timeline": name,
            "segment": segment,
            "record_frame": record,
            "duration": duration,
        },
    )


def _refuse_drift(
    item: TimelineItem,
    segment: str,
    listed: list[dict[str, Any]],
    name: str,
) -> None:
    """A selector that is not the shape the cut file describes means the two have drifted."""
    found = _takes_count(item)
    if found == len(listed):
        return
    raise InvalidRequestError(
        cause=f"Segment {segment!r} on {name!r} holds {found} take(s), not the "
        f"{len(listed)} this cut file describes.",
        fix="The timeline and the cut file have drifted apart — build_timeline the file "
        "again and swap on the version that comes out.",
        detail={
            "timeline": name,
            "segment": segment,
            "takes": {"wanted": len(listed), "found": found},
        },
    )


def _swap_failure(segment: str, take: int, name: str) -> TimelineOperationError:
    return TimelineOperationError(
        cause=f"Resolve would not select take {take} on segment {segment!r} of {name!r}.",
        fix="Check the timeline is not locked or mid-render in the Resolve GUI, then retry. "
        "The shot still shows whichever take detail.selected names.",
        detail={"timeline": name, "segment": segment, "requested": take},
    )


def _sync(segment: dict[str, Any], take: int) -> dict[str, Any] | None:
    """The cut-file edit this swap needs: the alternate promoted, the old main demoted.

    The two trade slots rather than the list shuffling up, so the selector a rebuild produces
    is the same length and the same shape as the one on the timeline now: only slots 1 and
    ``take`` change hands, and every other alternate keeps the index it has today. Take 1
    needs no edit at all — the file already says main is the selection.
    """
    if take == MAIN_TAKE:
        return None
    alternates = [_range(entry) for entry in segment.get("alternates") or []]
    slot = take - MAIN_TAKE - 1
    promoted = alternates[slot]
    alternates[slot] = _range(segment)
    return {
        "segment": str(segment["id"]),
        "source": promoted["source"],
        "in": promoted["in"],
        "out": promoted["out"],
        "alternates": alternates,
    }


def _range(entry: dict[str, Any]) -> dict[str, Any]:
    return {"source": str(entry["source"]), "in": int(entry["in"]), "out": int(entry["out"])}


# --- the seam itself --------------------------------------------------------------------------


def _select(item: TimelineItem, index: int, failure: ResolveMcpError) -> None:
    """Select a take and read the selection back: the return value alone proves nothing."""
    if not item.SelectTakeByIndex(index) or _selected_take(item) != index:
        failure.detail["selected"] = _selected_take(item)
        raise failure


def _takes_count(item: TimelineItem) -> int:
    """How many takes the selector holds — zero for a clip that is not a selector at all."""
    return _reading(item, "GetTakesCount")


def _selected_take(item: TimelineItem) -> int:
    """The selected take's 1-based index, or zero when the clip is not a take selector."""
    return _reading(item, "GetSelectedTakeIndex")


def _reading(item: TimelineItem, method: str) -> int:
    try:
        return int(getattr(item, method)() or 0)
    except (AttributeError, TypeError, ValueError):
        # fusionscript hands back None for a name it does not know, so a build old enough
        # to lack the method calls None — which is a TypeError, not an AttributeError.
        log.warning("Resolve gave no answer to %s; reading it as 0", method)
        return 0


def _items_by_record(timeline: Timeline) -> dict[int, TimelineItem]:
    """V1 keyed by start frame — how a segment's own shot is found, position being identity."""
    return {
        int(item.GetStart()): item
        for item in timeline.GetItemListInTrack("video", VIDEO_TRACK) or []
    }


__all__ = ["MAIN_TAKE", "Selector", "Take", "attach_takes", "swap_take"]
