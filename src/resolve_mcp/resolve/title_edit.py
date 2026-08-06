"""Editing one title that is already on the timeline, without re-placing anything.

``apply.py`` is declarative: the file is the truth and every apply clears the Titles track
and re-places it. That is the right shape for authoring a set, and the wrong shape for a
typo — a one-word fix should not cost every title its identity, its Fusion comp and its
fade, and it should not require the songs to still be marked on the timeline.

So this module is the other half of the pair, and the differences from an apply are all
deliberate:

* **Nothing is created, cleared, placed or made current.** The track is found or the edit
  is refused; the clip already there is written *through*. That is what "no rebuild, no
  re-apply" means, and it is why the target timeline is never opened in the GUI: an
  ``AppendToTimeline`` lands on whatever is current, but a Fusion input is written through
  the item's own handle and needs no such switch.
* **The timeline is the truth, not a file.** A title is named by the words it says, which
  is what the caller has in front of them when they see the typo. ``titles.json`` is not
  read at all, so an edit works on a timeline whose markers are gone and whose file has
  moved on. The cost is real and is the caller's to weigh: re-applying the file afterwards
  puts the old wording back, and the tool's docstring says so.
* **Exactly one title, or none.** Two titles reading the same words is a refusal with both
  their frames rather than a guess, because the wrong guess edits a title nobody was
  looking at and reports success.
* **The neighbours are the evidence.** Every other title on the track is read before the
  write and again after it. If a template's instances share one Fusion comp — the failure
  #41 exists to catch — then editing one edits them all, and here that is *visible*, where
  an apply could only see it by writing every title first. A neighbour that moved is
  reported as the shared comp it is, not as a successful edit.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..errors import (
    InvalidRequestError,
    TitleEditFailedError,
    TitleNotFoundError,
    TitleTemplateError,
)
from ..logging_config import get_logger
from ..timing import dual_time, to_frames
from ..titles.schema import TRACK_NAME
from . import apply, fusion, session
from . import timeline as timeline_read
from .connection import ResolveConnection

log = get_logger("titles")

Item = Any
Timeline = Any

Scalar = str | int | float
"""What a Fusion input takes from here. ``bool`` rides in as an ``int`` subclass."""


@dataclass(frozen=True)
class Placed:
    """One clip standing on the Titles track, read as far as it could be.

    ``node`` is ``None`` for a clip on the track that is not a Text+ title at all — a
    listing must still be able to report it, because "there is something on the Titles
    track I did not put there" is exactly the thing worth telling the caller.
    """

    position: int
    item: Item
    record_in: int | None
    duration: int | None
    node: fusion.TitleNode | None
    text: str | None
    unreadable: str | None

    @property
    def where(self) -> str:
        return where_of(self.position)

    def as_dict(self, fps: float | None, params: fusion.Params | None) -> dict[str, Any]:
        return {
            "position": self.position,
            "record": dual_time(self.record_in, fps),
            "duration": dual_time(self.duration, fps),
            "text": self.text,
            "node": None
            if self.node is None
            else {"name": self.node.name, "text_plus_in_comp": self.node.of_how_many},
            "params": None if params is None else params.as_dict(),
            "unreadable": self.unreadable,
        }


def list_titles(connection: ResolveConnection, timeline: str | None = None) -> dict[str, Any]:
    """Read the Titles track back: what each placed title says, and what it exposes."""
    project = timeline_read.open_project(connection)
    target = timeline_read.find_timeline(project, timeline)
    name = timeline_read.name_of(target)
    fps = session.frame_rate(project, target)

    track = apply.find_track(target, name)
    standing = [] if track is None else _standing(target, track)
    log.info("Read %d title(s) off %s of %r", len(standing), TRACK_NAME, name)
    return {
        "timeline": name,
        "track": None if track is None else track.as_dict(),
        "titles": [
            placed.as_dict(fps, None if placed.node is None else fusion.read_params(placed.node))
            for placed in standing
        ],
    }


def edit_title(
    connection: ResolveConnection,
    title: str | None = None,
    *,
    text: str | None = None,
    params: dict[str, Any] | None = None,
    at: Any = None,
    timeline: str | None = None,
) -> dict[str, Any]:
    """Write new words and/or new input values into one already-placed Text+ instance."""
    wanted = _wanted(params)
    if text is None and not wanted:
        raise InvalidRequestError(
            cause="edit_title was asked to change nothing: neither text nor params was given.",
            fix="Pass text= for the words, params= for exposed inputs, or both.",
            detail={"title": title},
        )
    if title is None and at is None:
        raise InvalidRequestError(
            cause="edit_title was not told which title to edit.",
            fix="Pass title= the exact words the title says now, or at= its record frame — "
            "list_titles reports both for every title on the track.",
            detail={},
        )

    project = timeline_read.open_project(connection)
    target = timeline_read.find_timeline(project, timeline)
    name = timeline_read.name_of(target)
    fps = session.frame_rate(project, target)

    track = apply.find_track(target, name)
    if track is None:
        raise TitleNotFoundError(
            cause=f"{name!r} has no {TRACK_NAME} track, so there is no title to edit.",
            fix="Run apply_titles to place the titles first — this tool only edits titles "
            "that are already on the timeline.",
            detail={"timeline": name, "track": TRACK_NAME},
        )
    standing = _standing(target, track)
    chosen, node = _pick(standing, title, at, fps, track)

    before = _snapshot(standing, wanted)
    written = _write(node, text, wanted)
    _refuse_strays(chosen, text, wanted, track)
    untouched = _refuse_shared_comp(standing, chosen, before, track)

    log.info("Edited %s of %r: %s", chosen.where, name, ", ".join(written))
    # Read the clip once more rather than reporting what was asked for: the report says
    # what the timeline holds now, and it is walked to from the item like every other read.
    edited = _read_one(chosen.item, chosen.position)
    return {
        "timeline": name,
        "track": track.as_dict(),
        "title": edited.as_dict(
            fps, None if edited.node is None else fusion.read_params(edited.node)
        ),
        "edited": written,
        "was": chosen.as_dict(fps, None),
        "other_titles_unchanged": untouched,
    }


# --- reading the track ------------------------------------------------------------------


def _standing(timeline: Timeline, track: apply.OwnedTrack) -> list[Placed]:
    """Every clip on the owned track, in timeline order, each read as far as it goes.

    Ordered by record frame rather than by the order Resolve hands them out: ``position``
    is quoted back to the caller in every refusal, so it has to mean "third from the left"
    and not "third in whatever list came back".
    """
    items = timeline_read.items_in_track(timeline, apply.VIDEO, track.index)
    ordered = sorted(items, key=lambda item: timeline_read.read_frames(item.GetStart()) or 0)
    return [_read_one(item, position) for position, item in enumerate(ordered, start=1)]


def where_of(position: int) -> str:
    """How a title is named in every message about it: by where it is, not by what it says.

    Its words are the thing being changed, so they cannot also be the thing that identifies
    it in the report of the change.
    """
    return f"the title at position {position} on the {TRACK_NAME} track"


def _read_one(item: Item, position: int) -> Placed:
    record_in = timeline_read.read_frames(item.GetStart())
    duration = timeline_read.read_frames(item.GetDuration())
    where = where_of(position)
    try:
        node = fusion.title_node(item, where)
    except TitleTemplateError as exc:
        # A listing must survive a clip that is not a title at all; an *edit* of that clip
        # still refuses, because _pick will not choose one whose node is None.
        return Placed(position, item, record_in, duration, None, None, exc.cause)
    return Placed(position, item, record_in, duration, node, fusion.read_text(node), None)


def _pick(
    standing: list[Placed],
    title: str | None,
    at: Any,
    fps: float | None,
    track: apply.OwnedTrack,
) -> tuple[Placed, fusion.TitleNode]:
    """The one title the caller means, or a refusal naming everything on the track."""
    at_frame = to_frames(at, fps, "at")
    matching = [
        placed
        for placed in standing
        if (title is None or placed.text == title)
        and (at_frame is None or placed.record_in == at_frame)
    ]
    asked = {"title": title, "at": None if at_frame is None else dual_time(at_frame, fps)}

    if not matching:
        raise TitleNotFoundError(
            cause=f"No title on {track.name!r} of {track.timeline!r} matches "
            f"{_asked_for(title, at_frame)}.",
            fix="Match the text exactly, character for character — trailing spaces and line "
            "breaks count. detail.on_track lists what is there.",
            detail=track.detail(asked=asked, on_track=_listing(standing, fps)),
        )
    if len(matching) > 1:
        raise TitleNotFoundError(
            cause=f"{len(matching)} titles on {track.name!r} of {track.timeline!r} match "
            f"{_asked_for(title, at_frame)}, so none was edited.",
            fix="Pass at= the record frame of the one you mean; detail.matching lists them.",
            detail=track.detail(asked=asked, matching=_listing(matching, fps)),
        )

    chosen = matching[0]
    if chosen.node is None:
        raise TitleEditFailedError(
            cause=f"The clip at position {chosen.position} on {track.name!r} of "
            f"{track.timeline!r} is not a Text+ title: {chosen.unreadable}",
            detail=track.detail(position=chosen.position),
        )
    return chosen, chosen.node


def _asked_for(title: str | None, at_frame: int | None) -> str:
    parts = []
    if title is not None:
        parts.append(f"the text {title!r}")
    if at_frame is not None:
        parts.append(f"record frame {at_frame}")
    return " at ".join(parts)


def _listing(standing: list[Placed], fps: float | None) -> list[dict[str, Any]]:
    return [
        {
            "position": placed.position,
            "record": dual_time(placed.record_in, fps),
            "text": placed.text,
            "unreadable": placed.unreadable,
        }
        for placed in standing
    ]


# --- writing one, and proving it was only one -------------------------------------------


def _wanted(params: dict[str, Any] | None) -> dict[str, Scalar]:
    """The params as something writable, or a refusal — checked before Resolve is touched."""
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise InvalidRequestError(
            cause=f"params={params!r} is not a mapping of input id to value.",
            fix='Pass params as an object, e.g. {"Size": 0.08}. list_titles reports the ids '
            "this template exposes.",
            detail={"params": repr(params)},
        )
    wanted: dict[str, Scalar] = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key.strip():
            raise InvalidRequestError(
                cause=f"params has a key that is not an input id: {key!r}.",
                fix="Keys are Fusion input ids as list_titles reports them.",
                detail={"key": repr(key)},
            )
        if key == fusion.STYLED_TEXT:
            raise InvalidRequestError(
                cause=f"{fusion.STYLED_TEXT} is the title's words, so it is not a param here.",
                fix=f"Pass the words as text= instead — two ways to set {fusion.STYLED_TEXT} "
                "is how the report and the timeline drift apart.",
                detail={"key": key},
            )
        if not isinstance(value, str | int | float):
            raise InvalidRequestError(
                cause=f"params[{key!r}]={value!r} is not a value a Fusion input takes.",
                fix="Fusion inputs take a number, a string or a boolean — nothing nested.",
                detail={"key": key, "value": repr(value)},
            )
        wanted[key] = value
    return wanted


def _snapshot(standing: list[Placed], wanted: dict[str, Scalar]) -> dict[int, dict[str, Any]]:
    """What every readable title on the track says now, in the inputs this edit will write.

    Taken before anything is written and over *all* the titles, including the target: the
    comparison afterwards is what turns "the write landed" into "the write landed on one
    title", and a snapshot taken any later would already have the damage in it.
    """
    return {
        placed.position: _values_of(placed.node, wanted)
        for placed in standing
        if placed.node is not None
    }


def _values_of(node: fusion.TitleNode, keys: Iterable[str]) -> dict[str, Any]:
    """The text plus each named input, so a before and an after compare key for key."""
    return {
        fusion.STYLED_TEXT: fusion.read_text(node),
        **{key: fusion.read_input(node, key) for key in keys if key != fusion.STYLED_TEXT},
    }


def _write(node: fusion.TitleNode, text: str | None, wanted: dict[str, Scalar]) -> list[str]:
    written = []
    if text is not None:
        fusion.set_text(node, text)
        written.append(fusion.STYLED_TEXT)
    for key, value in wanted.items():
        fusion.set_input(node, key, value)
        written.append(key)
    return written


def _refuse_strays(
    chosen: Placed,
    text: str | None,
    wanted: dict[str, Scalar],
    track: apply.OwnedTrack,
) -> None:
    """Read every written input back off the clip, walking to the node again to do it.

    The node is re-fetched from the timeline item rather than reused, for the reason
    ``apply._write_titles`` gives: a Fusion handle can go on answering for a comp the
    timeline has since replaced, so a read through it proves only that the handle remembers.
    """
    fresh = _walk_back(chosen, track)
    strayed: list[dict[str, Any]] = []
    if text is not None:
        read = fusion.read_text(fresh)
        if read != text:
            strayed.append({"input": fusion.STYLED_TEXT, "wrote": text, "reads": read})
    for key, value in wanted.items():
        read = fusion.read_input(fresh, key)
        if not fusion.same_value(value, read):
            strayed.append({"input": key, "wrote": value, "reads": read})
    if not strayed:
        return
    raise TitleEditFailedError(
        cause=f"{len(strayed)} input(s) of the title at position {chosen.position} did not "
        f"read back as written: {strayed[0]['input']} was given {strayed[0]['wrote']!r} and "
        f"reads {strayed[0]['reads']!r}.",
        fix="An input that reads back as nothing is one this template has not got — pick an "
        "id from detail.editable, which is every input this node will take. An input that "
        "keeps its old value is a refused write: unlock the Titles track and edit again.",
        # The full id list, not the summary list_titles reports: an id that summary passed
        # over is exactly the id someone is most likely to have got wrong.
        detail=track.detail(
            position=chosen.position,
            strayed=strayed,
            editable=fusion.editable_ids(fresh),
        ),
    )


def _walk_back(placed: Placed, track: apply.OwnedTrack) -> fusion.TitleNode:
    """Reach a placed title's node again, turning a comp that has gone unreadable into a
    failure about *this edit* rather than a bare template error.

    It matters most for the neighbours: a comp that will not answer after the write is the
    shared-comp damage the caller has to hear about, and ``TitleTemplateError`` on its own
    would name a template without naming the edit that disturbed it.
    """
    try:
        return fusion.title_node(placed.item, placed.where)
    except TitleTemplateError as exc:
        raise TitleEditFailedError(
            cause=f"After the write, {placed.where} of {track.timeline!r} would not answer "
            f"for its Text+ node: {exc.cause}",
            fix="Re-run apply_titles to rebuild the track from the file, then check the "
            "template carries one Text+ node per placed instance.",
            detail=track.detail(position=placed.position),
        ) from exc


def _refuse_shared_comp(
    standing: list[Placed],
    chosen: Placed,
    before: dict[int, dict[str, Any]],
    track: apply.OwnedTrack,
) -> int:
    """Re-read the neighbours, and put back anything the edit reached before refusing.

    This is the one check an apply cannot make cheaply and an edit gets for free, and it
    is the whole of "neighbouring titles unaffected": instances of a template that share
    one Fusion comp all answer for the same inputs, so a title fixed here would silently
    re-word the one before it.

    Two limits on it, stated because the report must not read wider than the check is.
    Only the inputs this call *wrote* are compared, plus the text — reading all 194 of a
    Text+'s external inputs on every neighbour would cost more bridge calls than the edit
    itself, and a shared comp shows up in the written ones first. And only neighbours whose
    node could be read take part; a clip on the track that is not a title has nothing to
    compare, which is why the returned count is of neighbours *verified* rather than of
    clips present.

    The restore is what makes this an unwound edit rather than a reported one. On a shared
    comp, writing a neighbour's old values back also puts the target's own text back —
    same comp, same inputs — so the track ends where it started, which is a better place
    to hand back than half-edited. It is best-effort and its outcome is reported either
    way: this always raises, because an edit that could not be confined is not an edit
    anyone should be told succeeded.
    """
    moved = []
    for placed in standing:
        if placed.position == chosen.position or placed.position not in before:
            continue
        was = before[placed.position]
        now = _values_of(_walk_back(placed, track), was)
        for key, old in was.items():
            if not fusion.same_value(old, now.get(key)):
                moved.append(
                    {"position": placed.position, "input": key, "was": old, "reads": now.get(key)}
                )
    if moved:
        raise TitleEditFailedError(
            cause=f"Editing the title at position {chosen.position} changed "
            f"{len({entry['position'] for entry in moved})} other title(s) on "
            f"{track.name!r} of {track.timeline!r}, so the edit was put back.",
            fix="The placed instances share one Fusion comp, so this template cannot carry "
            "per-instance titles and no in-place edit of it is safe. Author a fresh Text+ "
            "template in the GUI and export its bin again. detail.restored says whether the "
            "old wording went back; run apply_titles if it did not.",
            detail=track.detail(
                position=chosen.position,
                changed=moved,
                restored=_put_back(standing, before, moved),
            ),
        )
    # Every entry in `before` is a readable node, and _pick guarantees the target is one
    # of them, so the neighbours verified are simply the rest.
    return len(before) - 1


def _put_back(
    standing: list[Placed],
    before: dict[int, dict[str, Any]],
    moved: list[dict[str, Any]],
) -> bool:
    """Write the disturbed neighbours' old values back. Never raises; says if it worked.

    Called only on the way into a failure, so a Resolve that has stopped answering must
    not turn the diagnosis into a different exception — the caller is owed the finding
    about the shared comp far more than it is owed this.
    """
    by_position = {placed.position: placed for placed in standing}
    disturbed = []
    for position in sorted({int(entry["position"]) for entry in moved}):
        placed = by_position.get(position)
        if placed is None or placed.node is None:
            return False
        disturbed.append((placed.node, before[position]))

    try:
        for node, was in disturbed:
            for key, old in was.items():
                if key == fusion.STYLED_TEXT:
                    fusion.set_text(node, "" if old is None else str(old))
                else:
                    fusion.set_input(node, key, old)
        # Read back, for the same reason every other write here is read back.
        for node, was in disturbed:
            now = _values_of(node, was)
            if any(not fusion.same_value(old, now.get(key)) for key, old in was.items()):
                return False
    except Exception:
        log.warning("Could not put back the title(s) the edit disturbed", exc_info=True)
        return False
    return True


__all__ = ["Placed", "edit_title", "list_titles"]
