"""Inside a placed title: the Text+ node, its text, and the opacity spline that fades it.

This is the only part of the server that reaches into a Fusion comp, and it is the part
the scripting API documents least. Four things here are decisions rather than API calls:

* **Every attribute is checked for being callable, never for existing.** fusionscript
  answers *every* attribute name — a method a build does not have comes back as ``None``,
  not as a missing attribute (#41, verified live on Studio 21.0.3.7). ``hasattr`` is no
  guard at all, so each Fusion-side method is fetched and checked before it is used, and
  a build that lacks one is named rather than left to die on ``NoneType is not callable``.
* **The fade is written where the API allows one at all.** Resolve exposes no clip-level
  fade handles and no keyframe API (#5): ``TimelineItem.SetProperty`` reaches a *static*
  opacity and nothing else. The only scriptable fade is inside the comp — a BezierSpline
  on the Text+ node's ``Opacity1``, keyframed by index assignment, which is the pattern
  shipping Text+ automation uses.
* **A fade that cannot be written does not sink the title.** The text is the title; the
  fade is how it arrives. So a comp with no Text+ node raises — the title would be blank
  and wrong — while an opacity input that will not animate comes back as a fade marked
  unverified, with the reason, and the placed title stays. The report says which per
  event, because on a machine nobody is watching that distinction is the whole diagnosis.
* **The keyframes are read back at their own times.** Writing them proves the calls were
  accepted; reading ``GetInput`` at each keyframe time is the only evidence the spline is
  connected and animating. A build whose ``GetInput`` takes no time argument cannot answer
  that, which is reported as unverified rather than treated as a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..errors import TitleTemplateError
from ..logging_config import get_logger

log = get_logger("fusion")

Comp = Any
Item = Any
Tool = Any

TEXT_PLUS: Final = "TextPlus"
"""The node type a Text+ title is built on — what ``GetToolList`` filters by."""

STYLED_TEXT: Final = "StyledText"
"""The Text+ input holding the words. Multi-line via ``\\n``."""

OPACITY: Final = "Opacity1"
"""Shading element 1's opacity: the Text+ input a fade is written on (#5)."""

INPUT_ID: Final = "INPS_ID"
"""``GetInputList`` hands back Input *objects*; this attribute is the id ``GetInput`` takes.

The display name (``INPS_Name``) is not it — "Size" on screen can be ``Size`` or
``StyleSize`` underneath, and only the id round-trips through ``SetInput``.
"""

EXTERNAL: Final = "INPB_External"
"""Whether an input is a control at all, as against Fusion's own internal plumbing.

A live Text+ on Studio 21.0.3.7 lists **309** inputs, of which 194 are external and the
rest are nests, separators and layout furniture that no titler would ever set.
"""

NUMBER_DEFAULT: Final = "INPN_Default"
"""A numeric input's stock value. There is no ``INPS_Default``: text declares none."""

PARAM_LIMIT: Final = 40
"""A hard cap on a params listing, so no template can flood a tool result."""

CLEAR: Final = 0.0
FULL: Final = 1.0

RENDER_START: Final = "COMPN_RenderStart"
RENDER_END: Final = "COMPN_RenderEnd"

_TOLERANCE: Final = 1e-6
"""Opacity crosses a C bridge as a float; only a real difference should read as one."""


@dataclass(frozen=True)
class TitleNode:
    """The Text+ node of one placed instance, with the comp it lives in.

    ``of_how_many`` travels with it because a template may hold several Text+ nodes and
    which one answered is exactly what an apply report cannot reconstruct afterwards.
    """

    comp: Comp
    tool: Tool
    name: str
    of_how_many: int


@dataclass(frozen=True)
class Fade:
    """What was asked of the opacity spline, and what came back when it was read."""

    frames_in: int
    frames_out: int
    keyframes: tuple[tuple[int, float], ...]
    verified: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "in": self.frames_in,
            "out": self.frames_out,
            "keyframes": [{"frame": frame, "opacity": value} for frame, value in self.keyframes],
            "verified": self.verified,
            "detail": self.detail,
        }


NO_FADE = Fade(0, 0, (), True, "no fade asked for")
"""A hard cut on and off — the file said nothing about a fade, so nothing was written."""


def baked_fade(frames_in: int, frames_out: int) -> Fade:
    """The PNG route's fade: already in the pixels, so there is nothing to write or read.

    Reported in the same shape as a written one so a caller reading the apply report does
    not have to branch on the route to find out how a title fades. ``verified`` is true
    because the ramp arrived with the card — the frames were counted against the event's
    duration before anything was placed (T11), which is the whole of the check available.
    """
    if not (frames_in or frames_out):
        return NO_FADE
    return Fade(frames_in, frames_out, (), True, "baked into the exported frames")


@dataclass(frozen=True)
class Params:
    """The exposed inputs of one placed title's Text+ node, and whether they could be read.

    Reported the same way a :class:`Fade` is: a build that will not enumerate its inputs
    yields empty ``values`` with the reason in ``detail`` rather than an error, because the
    listing is a convenience — every input can still be *written* by id without it.
    """

    values: dict[str, Any]
    read: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"values": dict(self.values), "read": self.read, "detail": self.detail}


def _callable(obj: Any, name: str) -> Any | None:
    """The named method, or ``None`` when this build does not really have it."""
    found = getattr(obj, name, None)
    return found if callable(found) else None


def _required(obj: Any, name: str, where: str) -> Any:
    found = _callable(obj, name)
    if found is None:
        raise TitleTemplateError(
            cause=f"This Resolve build has no {name}, so {where} cannot be titled.",
            fix="Check the Resolve version — the Fusion comp getters are the part of the "
            "scripting API that differs most between builds.",
            detail={"method": name, "where": where},
        )
    return found


def title_node(item: Item, where: str) -> TitleNode:
    """The Text+ node of a placed template instance. Raises if there is not exactly one."""
    if not int(_required(item, "GetFusionCompCount", where)() or 0):
        raise TitleTemplateError(
            cause=f"The clip placed for {where} carries no Fusion comp, so it is not a "
            f"Text+ title template.",
            detail={"where": where},
        )
    comp = _required(item, "GetFusionCompByIndex", where)(1)
    if comp is None:
        raise TitleTemplateError(
            cause=f"The clip placed for {where} counts a Fusion comp but hands out none.",
            detail={"where": where},
        )
    tools = dict(_required(comp, "GetToolList", where)(False, TEXT_PLUS) or {})
    if not tools:
        held = dict(_required(comp, "GetToolList", where)(False, "") or {})
        raise TitleTemplateError(
            cause=f"The comp placed for {where} holds no {TEXT_PLUS} node; it holds "
            f"{_names(held) or '<nothing>'}.",
            # A Fusion *macro* is the other thing a title template can be, and its editable
            # text is an exported input on the macro rather than a Text+ node — reachable
            # by a different route than this one, so it is named rather than guessed at.
            fix=f"This route titles a plain {TEXT_PLUS} template. If the clip is a Fusion "
            f"macro, its text is an exported input rather than a {TEXT_PLUS} node and this "
            f"tool cannot reach it — author a plain {TEXT_PLUS} title in the GUI and export "
            f"its bin as a .drb instead.",
            detail={"where": where, "tools": sorted(_names_of(held))},
        )
    first = tools[min(tools)]
    return TitleNode(comp, first, str(first.GetAttrs("TOOLS_Name") or ""), len(tools))


def _names_of(tools: dict[int, Tool]) -> list[str]:
    return [str(tool.GetAttrs("TOOLS_Name") or "") for tool in tools.values()]


def _names(tools: dict[int, Tool]) -> str:
    return ", ".join(repr(name) for name in _names_of(tools))


def set_text(node: TitleNode, text: str) -> None:
    """Write one instance's words. ``SetInput`` reports nothing — read it back to know."""
    node.tool.SetInput(STYLED_TEXT, text)


def read_text(node: TitleNode) -> str | None:
    """What the node says its text is now — the only evidence a write landed."""
    value = node.tool.GetInput(STYLED_TEXT)
    return None if value is None else str(value)


def set_input(node: TitleNode, key: str, value: Any) -> None:
    """Write any one exposed input by its id. Reports nothing, like ``set_text``."""
    node.tool.SetInput(key, value)


def read_input(node: TitleNode, key: str) -> Any:
    """The current value of one exposed input, or ``None`` for one this node has not got."""
    return node.tool.GetInput(key)


def read_params(node: TitleNode) -> Params:
    """The inputs this template *sets*, by id, so the agent can see what makes it itself.

    Enumerating everything is not the answer, and the live listing is why: a plain Text+
    reports 309 inputs, 194 of them external and nearly all sitting at their stock value.
    A tool result carrying all 194 tells the reader nothing and costs them the context to
    read it. What identifies a title template is the handful its author moved — on the
    stock Text+ that is exactly ``Font``, ``Style``, ``Size``, the two justifications and
    ``Wrap`` — so an input is listed when its value differs from the default this build
    declares for it, or when it is a string with anything in it.

    That is a listing rule, never a permission: ``edit_title`` will write *any* id, listed
    or not, and proves the write by reading it back. ``detail`` says how many were passed
    over so the reader knows the listing is a summary, and :func:`editable_ids` gives the
    full set — which is what a refused write reports, so an id that this rule passed over
    is always reachable at the moment someone needs it.

    Never raises: a build that does not enumerate its inputs still takes every write by id,
    so an unusable listing is reported and the edit route stays open. Only scalars are kept
    — image, mask and gradient inputs answer with objects that mean nothing here — and
    ``StyledText`` is left out because the words have their own field everywhere else, and
    two places to write one value is how they drift apart.
    """
    lister = _callable(node.tool, "GetInputList")
    if lister is None:
        return Params({}, False, f"this build's {TEXT_PLUS} node has no GetInputList")
    try:
        listed = dict(lister() or {})
    except (TypeError, ValueError) as exc:
        return Params({}, False, f"GetInputList would not answer: {exc}")

    editable = 0
    values: dict[str, Any] = {}
    for entry in listed.values():
        attrs = _attrs_of(entry)
        if attrs is None or not attrs.get(EXTERNAL):
            continue
        found = attrs.get(INPUT_ID)
        if found is None or str(found) == STYLED_TEXT:
            continue
        value = read_input(node, str(found))
        if not isinstance(value, str | int | float):
            continue
        editable += 1
        if _is_set(value, attrs):
            values[str(found)] = value

    shown = dict(sorted(values.items())[:PARAM_LIMIT])
    detail = (
        f"{len(values)} input(s) this template sets, of {editable} editable "
        f"of {len(listed)} listed"
    )
    if len(shown) < len(values):
        detail += f"; showing the first {len(shown)} by id"
    return Params(shown, True, detail)


def editable_ids(node: TitleNode) -> list[str]:
    """Every input id this node will take a write on, in full and unfiltered.

    :func:`read_params` reports what the template *sets*, which is the useful summary and
    is deliberately a fraction of what exists. This is the other half of that bargain: an
    id the summary passed over is still writable, so a write refused for an unknown id
    reports this list and the caller can see what they should have asked for. Kept off the
    happy path on purpose — nearly two hundred ids on a stock Text+ is a diagnosis, not a
    listing.
    """
    lister = _callable(node.tool, "GetInputList")
    if lister is None:
        return []
    try:
        listed = dict(lister() or {})
    except (TypeError, ValueError):
        return []
    found = set()
    for entry in listed.values():
        attrs = _attrs_of(entry)
        if attrs is None or not attrs.get(EXTERNAL):
            continue
        key = attrs.get(INPUT_ID)
        if key is not None:
            found.add(str(key))
    return sorted(found)


def _attrs_of(entry: Any) -> dict[str, Any] | None:
    attrs = _callable(entry, "GetAttrs")
    if attrs is None:
        return None
    reported = attrs()
    return reported if isinstance(reported, dict) else None


def _is_set(value: Any, attrs: dict[str, Any]) -> bool:
    """Whether this input carries a choice rather than the value it shipped with.

    A number whose build declares no default is passed over rather than guessed at: it
    would otherwise list every slider on the node, which is the outcome this rule exists
    to avoid.
    """
    if isinstance(value, str):
        return bool(value)
    default = attrs.get(NUMBER_DEFAULT)
    if isinstance(value, int | float) and isinstance(default, int | float):
        return abs(float(value) - float(default)) > _TOLERANCE
    return False


def same_value(written: Any, read: Any) -> bool:
    """Whether an input reads back as what was written, allowing for the C bridge.

    A number written from Python comes back as a float, so an exact comparison would call
    a landed write a failure; anything else has to match outright, because a *different*
    value read back is the shared-comp symptom this check exists to catch.
    """
    if written == read:
        return True
    if isinstance(written, bool) or isinstance(read, bool):
        return False
    if isinstance(written, int | float) and isinstance(read, int | float):
        return abs(float(read) - float(written)) <= _TOLERANCE
    return False


def write_fade(node: TitleNode, *, duration: int, fade_in: int, fade_out: int) -> Fade:
    """Keyframe ``Opacity1`` up over ``fade_in`` and down over ``fade_out``.

    Never raises: a fade this build will not write is reported, and the title it belongs
    to stays placed and correctly worded. ``duration`` is the placed instance's length in
    frames, used only when the comp will not say what its own render range is.
    """
    if not fade_in and not fade_out:
        return NO_FADE

    spline = _callable(node.comp, "BezierSpline")
    if spline is None:
        return _unwritten(fade_in, fade_out, "this Resolve build has no comp.BezierSpline")

    start, end = _render_range(node.comp, duration)
    keyframes = _keyframes(start, end, fade_in, fade_out)
    setattr(node.tool, OPACITY, spline())
    animated = getattr(node.tool, OPACITY, None)
    if animated is None:
        return _unwritten(fade_in, fade_out, f"the {TEXT_PLUS} node has no {OPACITY} input")
    try:
        for frame, value in keyframes:
            animated[frame] = value
    except TypeError as exc:
        # An Input that will not take a keyframe by index assignment: the write is refused
        # by the bridge itself, which is a build difference rather than a bug here.
        return _unwritten(fade_in, fade_out, f"{OPACITY} would not take a keyframe: {exc}")

    verified, detail = _read_back(node.tool, keyframes)
    return Fade(fade_in, fade_out, keyframes, verified, detail)


def _unwritten(fade_in: int, fade_out: int, why: str) -> Fade:
    log.warning("No opacity fade written: %s", why)
    return Fade(fade_in, fade_out, (), False, why)


def _render_range(comp: Comp, duration: int) -> tuple[int, int]:
    """The comp's own first and last frame, or the instance's own length as a fallback.

    A Fusion comp on a timeline clip is rendered over its own range, which is what a
    keyframe time is counted in. The fallback is what an edit-page comp normally holds
    anyway, and it is logged, because a wrong range puts a fade outside the title.
    """
    attrs = _callable(comp, "GetAttrs")
    reported = attrs() if attrs is not None else None
    if isinstance(reported, dict):
        start, end = _as_frame(reported.get(RENDER_START)), _as_frame(reported.get(RENDER_END))
        if start is not None and end is not None and end > start:
            return (start, end)
    log.info("Comp reported no usable render range; fading over the instance's %d frames", duration)
    return (0, max(duration - 1, 0))


def _as_frame(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _keyframes(start: int, end: int, fade_in: int, fade_out: int) -> tuple[tuple[int, float], ...]:
    """Up from nothing over ``fade_in``, held, down to nothing over ``fade_out``.

    ``end`` is the comp's last frame, so the ramps are inclusive of both ends. Both ends
    always carry a keyframe, even the end that is not fading: what a spline does *outside*
    its keyframes is an extrapolation setting nobody here has set, so a title asked to
    fade out only would otherwise rely on Fusion holding full opacity backwards from the
    first keyframe. Anchoring both ends says it instead of assuming it.

    When the two ramps meet — the file asked for a fade the whole length of the title —
    the held section collapses to one frame at full rather than inverting the keyframes.
    A title that fades both ways always keeps both of its clear ends: an in-ramp long
    enough to reach the last frame is pulled back one, because a ramp that landed *on*
    the last frame would take the frame the out-ramp has to end clear on, and the title
    would finish at full opacity while the report still said it faded out.
    """
    up_at = start + fade_in
    if fade_in and fade_out:
        up_at = min(up_at, end - 1)
    down_at = max(end - fade_out, up_at)
    # Keyed by frame, because the ramps can collapse onto one another and a repeated
    # frame written twice is a keyframe whose value depends on write order.
    keys = {start: CLEAR if fade_in else FULL, end: CLEAR if fade_out else FULL}
    if fade_in:
        keys[up_at] = FULL
    if fade_out:
        keys[down_at] = FULL
    return tuple(sorted(keys.items()))


def _read_back(tool: Tool, keyframes: tuple[tuple[int, float], ...]) -> tuple[bool, str]:
    """Read the animated input at each keyframe time; say plainly if it will not answer."""
    for frame, value in keyframes:
        try:
            reported = tool.GetInput(OPACITY, frame)
        except TypeError:
            return (False, f"this build's GetInput does not read {OPACITY} at a time")
        read = _as_opacity(reported)
        if read is None:
            return (False, f"{OPACITY} read back as {reported!r} at frame {frame}")
        if abs(read - value) > _TOLERANCE:
            return (False, f"{OPACITY} reads {read} at frame {frame}, not {value}")
    return (True, "read back at every keyframe")


def _as_opacity(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CLEAR",
    "EXTERNAL",
    "FULL",
    "INPUT_ID",
    "NUMBER_DEFAULT",
    "OPACITY",
    "PARAM_LIMIT",
    "STYLED_TEXT",
    "TEXT_PLUS",
    "Fade",
    "Params",
    "TitleNode",
    "baked_fade",
    "editable_ids",
    "read_input",
    "read_params",
    "read_text",
    "same_value",
    "set_input",
    "set_text",
    "title_node",
    "write_fade",
]
