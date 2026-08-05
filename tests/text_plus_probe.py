"""The Text+ template-append probe (#41).

One question, asked once, before ``apply_titles`` is designed: does a GUI-authored Text+
template survive a ``.drb`` round trip, land on a timeline through the API, and give each
placed instance *its own* text? Everything downstream — a titles track rebuilt per song,
a typo fixed in one card — assumes yes. Nothing in the API documents it.

So this is a probe, not a wrapper: it lives in ``tests/`` because its output is a finding
for a ticket, not a tool for an agent. It runs at both seams. Against fakes
(``test_text_plus_probe.py``) it proves it walks the route in the right order and names
each failure; against real Resolve (``test_live_smoke.py``) it answers the question.

Five things here are decisions rather than API calls:

* **The imported bin is recognised by name, not by identity.** ``ImportFolderFromFile``
  returns a bare ``True`` and names the bin after the ``.drb``, and Resolve hands out a
  fresh proxy object per call — so the only way to find what landed is to diff the root's
  child *names*. A name already in use is therefore indistinguishable from an import that
  landed nothing, and is reported as such rather than answered with the older bin.
* **The scratch timeline is made current and checked, before anything is appended.**
  ``AppendToTimeline`` appends to the project's *current* timeline, not to the one just
  created. Assuming ``CreateEmptyTimeline`` also switched would, on a build that does not,
  quietly append two Text+ instances onto the operator's open cut — and every assertion
  below would still pass, because the instances would be real.
* **The instances are read back off the timeline, not off the append.** What the append
  returned proves a call succeeded; what the timeline holds proves a title was placed. The
  second is what titling needs, so the placed items are re-read from the track.
* **Every title is read back after every write, never one at a time.** A probe that set a
  title and immediately read it would pass whether the instances have their own comps or
  share one; the second write is what exposes the difference.
* **Cleanup never costs the answer.** The probe mutates a real project — a scratch bin, a
  scratch timeline, the current folder and the current timeline — and puts all four back in
  a ``finally``. A cleanup that fails is recorded in the report, not raised over the
  finding the live run was made for.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from resolve_mcp.logging_config import get_logger
from resolve_mcp.naming import timestamped_name
from resolve_mcp.resolve.connection import ResolveConnection
from resolve_mcp.resolve.media import media_pool

log = get_logger("probe.textplus")

TEMPLATE_ENV = "RESOLVE_MCP_TEXTPLUS_TEMPLATE"
TEXT_PLUS = "TextPlus"
STYLED_TEXT = "StyledText"
SCRATCH_LABEL = "textplus-probe"
DEFAULT_TEXTS = ("Sunset Boulevard", "Bass — Ana Ruiz")
DEFAULT_DURATION = 120
VIDEO = "video"


class ProbeFailed(AssertionError):
    """A step of the route did not do what titling needs it to do.

    Carries the step it failed on and the trail up to it, because the live run happens on
    a machine nobody is watching and the trail is the whole diagnosis.
    """

    def __init__(self, step: str, detail: str, trail: Sequence[str]) -> None:
        self.step = step
        self.detail = detail
        self.trail = tuple(trail)
        walked = "\n".join(f"  {done}" for done in self.trail)
        super().__init__(f"{step}: {detail}\nwalked:\n{walked}")


class TextPlusNode(NamedTuple):
    """An instance's Text+ node, with how many its comp holds.

    The count travels with the node because a template may hold several Text+ nodes, and
    which one answered is the part ``apply_titles`` cannot guess later.
    """

    name: str
    tool: Any
    of_how_many: int


@dataclass(frozen=True)
class Placed:
    """One appended template instance, and what its Text+ node said afterwards."""

    index: int
    name: str
    record_in: int
    duration: int
    tool_name: str
    tool_count: int
    asked: str
    read_back: str | None

    @property
    def kept_its_own_text(self) -> bool:
        return self.read_back == self.asked


@dataclass(frozen=True)
class Report:
    """What the live run pastes onto the ticket."""

    template: str
    bin_name: str
    clip_name: str
    clip_type: str
    timeline_name: str
    placed: tuple[Placed, ...]
    cleaned_up: bool

    @property
    def per_instance_text(self) -> bool:
        return bool(self.placed) and all(one.kept_its_own_text for one in self.placed)

    def render(self) -> str:
        lines = [
            f"template:      {self.template}",
            f"imported bin:  {self.bin_name}",
            f"template clip: {self.clip_name} (Type={self.clip_type or '<none>'})",
            f"timeline:      {self.timeline_name}",
        ]
        for one in self.placed:
            lines.append(
                f"  instance {one.index}: {one.name} @ {one.record_in} for {one.duration}f — "
                f"{one.tool_name} (Text+ nodes in comp: {one.tool_count}) "
                f"asked {one.asked!r}, read back {one.read_back!r}"
            )
        lines.append(f"per-instance text: {'yes' if self.per_instance_text else 'no'}")
        lines.append(
            "scratch bin and timeline: removed"
            if self.cleaned_up
            else "scratch bin and timeline: left behind — delete them by hand"
        )
        return "\n".join(lines)


@dataclass
class _Scratch:
    """Everything the probe changed in a real project, and what it has to put back."""

    pool: Any
    project: Any
    previous_folder: Any
    previous_timeline: Any
    imported: Any = None
    timeline: Any = None


def probe_template_append(
    connection: ResolveConnection,
    template: Path,
    *,
    texts: Sequence[str] = DEFAULT_TEXTS,
    duration: int = DEFAULT_DURATION,
    now: datetime | None = None,
) -> Report:
    """Walk the whole route and report what Resolve did, or raise ``ProbeFailed``.

    Mutates the open project — a scratch bin and a scratch timeline, both removed again,
    and the current folder and timeline, both restored. ``texts`` must hold at least two
    distinct strings: one title proves only that a write landed somewhere, not that it
    landed on the instance it was aimed at.
    """
    asked = list(texts)
    if len(asked) < 2 or len(set(asked)) != len(asked):
        raise ValueError("The probe needs at least two distinct texts to prove anything.")

    trail: list[str] = []

    def walked(step: str, detail: str) -> None:
        trail.append(f"{step}: {detail}")
        log.info("Text+ probe — %s: %s", step, detail)

    def failed(step: str, detail: str) -> ProbeFailed:
        return _failure(step, detail, trail)

    step = "read the template"
    if not template.is_file():
        raise failed(step, f"No template at {template}. Export one from Resolve first.")
    walked(step, str(template))

    pool = media_pool(connection)
    # Safe only because media_pool() has already refused a session with no project open.
    project = connection.handle().GetProjectManager().GetCurrentProject()
    root = pool.GetRootFolder()
    known = {str(sub.GetName() or "") for sub in root.GetSubFolderList()}
    scratch = _Scratch(pool, project, pool.GetCurrentFolder(), project.GetCurrentTimeline())
    pool.SetCurrentFolder(root)
    walked("reach the media pool", f"root {str(root.GetName() or '')!r}, {len(known)} bins")

    try:
        step = "import the template bin"
        if not pool.ImportFolderFromFile(str(template)):
            raise failed(step, f"Resolve refused {template.name}.")
        walked(step, f"ImportFolderFromFile({template.name}) answered True")

        step = "find the imported bin"
        landed = [sub for sub in root.GetSubFolderList() if str(sub.GetName() or "") not in known]
        if not landed:
            raise failed(
                step,
                f"ImportFolderFromFile answered True but no new bin appeared under "
                f"{str(root.GetName() or '')!r}. Either nothing was imported, or the bin's "
                f"name was already taken by one of: {sorted(known)}. A bin may have been "
                f"left behind — check the pool by hand.",
            )
        if len(landed) > 1:
            raise failed(step, f"One import produced several bins: {_names(landed)}.")
        scratch.imported = landed[0]
        bin_name = str(scratch.imported.GetName() or "")
        walked(step, f"bin {bin_name!r}")

        step = "find the template clip"
        clips = _clips_under(scratch.imported)
        if not clips:
            raise failed(step, f"The imported bin {bin_name!r} holds no clips.")
        if len(clips) > 1:
            raise failed(
                step,
                f"The imported bin {bin_name!r} holds more than one clip "
                f"({_names(clips)}); export a bin holding the one template.",
            )
        clip = clips[0]
        clip_name = str(clip.GetName() or "")
        clip_type = str(clip.GetClipProperty("Type") or "")
        walked(step, f"{clip_name!r} (Type={clip_type or '<none>'})")

        step = "create the scratch timeline"
        timeline_name = timestamped_name(SCRATCH_LABEL, "", SCRATCH_LABEL, now)
        scratch.timeline = pool.CreateEmptyTimeline(timeline_name)
        if scratch.timeline is None:
            raise failed(step, f"Resolve created no timeline called {timeline_name!r}.")
        walked(step, timeline_name)

        step = "target the scratch timeline"
        project.SetCurrentTimeline(scratch.timeline)
        current = project.GetCurrentTimeline()
        wanted = _method(scratch.timeline, "GetUniqueId", step, trail)()
        if current is None or _method(current, "GetUniqueId", step, trail)() != wanted:
            raise failed(
                step,
                f"Resolve would not make {timeline_name!r} current — it is still on "
                f"{'nothing' if current is None else repr(str(current.GetName() or ''))}. "
                f"Appending now would place titles on the open cut, so nothing was appended.",
            )
        walked(step, f"{timeline_name!r} is current")

        step = "append the instances"
        placements = [
            {"mediaPoolItem": clip, "startFrame": 0, "endFrame": duration - 1} for _ in asked
        ]
        returned = list(pool.AppendToTimeline(placements) or [])
        if len(returned) != len(asked):
            short = (
                " A template shorter than the span asked for is refused rather than "
                "trimmed, so try a smaller duration=."
                if len(returned) < len(asked)
                else ""
            )
            raise failed(
                step,
                f"Asked for {len(asked)} instances of {clip_name!r} at {duration} frames "
                f"each, got back {len(returned)}.{short}",
            )
        walked(step, f"{len(returned)} instances of {clip_name!r} at {duration}f each")

        step = "find the placed instances"
        items = _placed_on(scratch.timeline)
        if len(items) != len(asked):
            raise failed(
                step,
                f"AppendToTimeline answered {len(returned)} instances but {timeline_name!r} "
                f"holds {len(items)} on its first video track.",
            )
        _check_each_landed_apart(items, trail)
        walked(step, f"{len(items)} on the first video track of {timeline_name!r}")

        step = "open the Fusion comp"
        nodes = [_text_plus_node(item, position, trail) for position, item in enumerate(items, 1)]
        walked(step, f"a Text+ node in each of {len(nodes)} comps")

        step = "write the text"
        for node, text in zip(nodes, asked, strict=True):
            node.tool.SetInput(STYLED_TEXT, text)
        walked(step, ", ".join(repr(text) for text in asked))

        # Every write first, then every read, and every read off the timeline rather than
        # off the handles written through: a shared comp only shows up once a later write
        # has had the chance to overwrite an earlier instance's title.
        step = "read the text back"
        placed = _read_each_back(_placed_on(scratch.timeline), nodes, asked, trail)
        _check_each_kept_its_own(placed, trail)
        walked(step, "each instance kept the text it was given")

        report = Report(
            template=template.name,
            bin_name=bin_name,
            clip_name=clip_name,
            clip_type=clip_type,
            timeline_name=timeline_name,
            placed=tuple(placed),
            cleaned_up=False,
        )
    finally:
        tidy = _clean_up(scratch)

    return replace(report, cleaned_up=tidy)


def _method(obj: Any, name: str, step: str, trail: Sequence[str]) -> Any:
    """A getter this Resolve build does not have, named rather than left to blow up.

    fusionscript answers *every* attribute name: a method it does not know comes back as
    ``None``, not as a missing attribute. Verified live on Studio 21.0.3.7, where
    ``hasattr(item, "GetTakeCount")`` is ``True`` and the attribute is ``None`` — so the
    usual guard passes and the call dies with ``NoneType is not callable``, which names
    neither the method nor the build. The Fusion surface is the part of the API most
    likely to differ between builds, so it is read through here.
    """
    getter = getattr(obj, name, None)
    if getter is None:
        raise _failure(step, f"This Resolve build has no {name}.", trail)
    return getter


def _failure(step: str, detail: str, trail: Sequence[str]) -> ProbeFailed:
    log.warning("Text+ probe failed at %s: %s", step, detail)
    return ProbeFailed(step, detail, trail)


def _names(objects: Sequence[Any]) -> str:
    return ", ".join(sorted(repr(str(one.GetName() or "")) for one in objects))


def _clips_under(folder: Any) -> list[Any]:
    """Every clip in the imported bin, however deeply the GUI nested it."""
    found = list(folder.GetClipList() or [])
    for sub in folder.GetSubFolderList() or []:
        found.extend(_clips_under(sub))
    return found


def _placed_on(timeline: Any) -> list[Any]:
    """What the timeline itself holds — the only witness that a title was placed."""
    return list(timeline.GetItemListInTrack(VIDEO, 1) or [])


def _text_plus_node(item: Any, position: int, trail: Sequence[str]) -> TextPlusNode:
    step = "open the Fusion comp"
    if not int(_method(item, "GetFusionCompCount", step, trail)() or 0):
        raise _failure(
            step,
            f"Instance {position} has no Fusion comp — the .drb round trip did not carry "
            f"the template's comp onto the placed clip.",
            trail,
        )
    comp = _method(item, "GetFusionCompByIndex", step, trail)(1)
    if comp is None:
        raise _failure(
            step,
            f"Instance {position} counts a Fusion comp but hands out none at index 1.",
            trail,
        )
    tool_list = _method(comp, "GetToolList", "find the Text+ node", trail)
    matching = dict(tool_list(False, TEXT_PLUS) or {})
    if not matching:
        held = dict(tool_list(False, "") or {})
        raise _failure(
            "find the Text+ node",
            f"Instance {position}'s comp holds no {TEXT_PLUS} node; it holds "
            f"{_tool_names(held) or '<nothing>'}.",
            trail,
        )
    first = matching[min(matching)]
    return TextPlusNode(str(first.GetAttrs("TOOLS_Name") or ""), first, len(matching))


def _tool_names(tools: dict[int, Any]) -> str:
    return ", ".join(repr(str(tool.GetAttrs("TOOLS_Name") or "")) for tool in tools.values())


def _read_each_back(
    items: Sequence[Any],
    nodes: Sequence[TextPlusNode],
    asked: Sequence[str],
    trail: Sequence[str],
) -> list[Placed]:
    if not len(items) == len(nodes) == len(asked):
        raise _failure(
            "read the text back",
            f"{len(asked)} titles were asked for and {len(nodes)} written, but the timeline "
            f"holds {len(items)} to read back — something moved them between the passes.",
            trail,
        )
    placed: list[Placed] = []
    for position, (item, node, text) in enumerate(zip(items, nodes, asked, strict=True), start=1):
        fresh = _text_plus_node(item, position, trail)
        read_back = fresh.tool.GetInput(STYLED_TEXT)
        placed.append(
            Placed(
                index=position,
                name=str(item.GetName() or ""),
                record_in=int(item.GetStart()),
                duration=int(item.GetDuration()),
                tool_name=node.name,
                tool_count=node.of_how_many,
                asked=text,
                read_back=None if read_back is None else str(read_back),
            )
        )
    return placed


def _check_each_landed_apart(items: Sequence[Any], trail: Sequence[str]) -> None:
    """Two instances stacked on one frame would read back as one title placed twice."""
    starts = [int(item.GetStart()) for item in items]
    if len(set(starts)) != len(starts):
        raise _failure(
            "find the placed instances",
            f"The instances did not land apart — record starts {starts}.",
            trail,
        )


def _check_each_kept_its_own(placed: Sequence[Placed], trail: Sequence[str]) -> None:
    strayed = [one for one in placed if not one.kept_its_own_text]
    if not strayed:
        return
    first = strayed[0]
    others = {one.asked for one in placed if one is not first}
    bled = first.read_back in others
    raise _failure(
        "read the text back",
        f"Instance {first.index} was given {first.asked!r} and reads back "
        f"{first.read_back!r}. "
        + (
            "That is another instance's text, so the placed instances share one comp and "
            "per-instance titling is not available on this route."
            if bled
            else "The write did not stick."
        ),
        trail,
    )


def _any_timeline_but(project: Any, scratch: Any) -> Any:
    """Some cut for Resolve to sit on that is not the one about to be deleted."""
    try:
        avoid = scratch.GetUniqueId()
        for index in range(1, int(project.GetTimelineCount() or 0) + 1):
            candidate = project.GetTimelineByIndex(index)
            if candidate is not None and candidate.GetUniqueId() != avoid:
                return candidate
    except Exception:  # noqa: BLE001 - a departed Resolve must not mask the finding
        log.exception("Text+ probe could not look for a timeline to fall back to")
    log.warning("Text+ probe has no timeline to leave Resolve on; the scratch cut may survive")
    return None


def _clean_up(scratch: _Scratch) -> bool:
    """Put the project back. Never raises — see the module docstring.

    Resolve is moved off the scratch timeline before it is deleted, because it will not
    delete the cut it is sitting on. A session that had no timeline open when the probe
    started has nothing to go back to, so any other cut in the project will do; a project
    whose only timeline is the scratch one leaves it behind, and says so in the report.
    """
    pool, project = scratch.pool, scratch.project
    was_in = scratch.previous_folder
    scratch_timeline, scratch_bin = scratch.timeline, scratch.imported
    was_on = scratch.previous_timeline
    if was_on is None and scratch_timeline is not None:
        was_on = _any_timeline_but(project, scratch_timeline)

    steps: list[tuple[str, Callable[[], Any]]] = []
    if was_on is not None:
        steps.append(("restore the current timeline", lambda: project.SetCurrentTimeline(was_on)))
    if scratch_timeline is not None:
        steps.append(
            ("delete the scratch timeline", lambda: pool.DeleteTimelines([scratch_timeline]))
        )
    if scratch_bin is not None:
        steps.append(("delete the scratch bin", lambda: pool.DeleteFolders([scratch_bin])))
    if was_in is not None:
        steps.append(("restore the current folder", lambda: pool.SetCurrentFolder(was_in)))

    tidy = True
    for what, undo in steps:
        try:
            done = undo()
        except Exception:  # noqa: BLE001 - a departed Resolve must not mask the finding
            log.exception("Text+ probe could not %s", what)
            tidy = False
            continue
        if not done:
            log.warning("Text+ probe could not %s", what)
            tidy = False
    return tidy
