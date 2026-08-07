"""The Fusion page: comps, tools and the animation splines on their inputs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .core import AnswersNone

if TYPE_CHECKING:
    from .connection import FakeResolve


class FakeSpline:
    """An animation modifier on one input: keyframes written by index assignment.

    That is the whole Fusion keyframe API from Python — ``tool.Opacity1[frame] = value``
    once a spline is connected. ``takes_keyframes=False`` models the bridge refusing the
    assignment, which is a ``TypeError`` rather than a return value.
    """

    def __init__(self, takes_keyframes: bool = True) -> None:
        self.keyframes: dict[float, float] = {}
        self.takes_keyframes = takes_keyframes

    def __setitem__(self, frame: Any, value: Any) -> None:
        if not self.takes_keyframes:
            raise TypeError("'FakeSpline' object does not support item assignment")
        self.keyframes[float(frame)] = float(value)

    def __getitem__(self, frame: Any) -> float | None:
        return self.keyframes.get(float(frame))

    def at(self, frame: Any) -> float | None:
        """What the animated input reads at a time — a keyframe or nothing between them."""
        return self.keyframes.get(float(frame))


class FakeFusionInput:
    """One entry of ``tool.GetInputList()``: an Input *object*, not an id and not a value.

    Modelled off the real thing, read live on Studio 21.0.3.7: a stock Text+ lists **309**
    inputs, and the three attributes that make that number usable are all here.

    The display name is deliberately not the id — Fusion's "Size" on screen is ``Size`` or
    ``StyleSize`` underneath, and only the id round-trips through ``GetInput``/``SetInput``.
    A fake whose two names agreed would let a wrapper reading ``INPS_Name`` pass here and
    then write to an input that does not exist on a real Text+.

    ``INPB_External`` is false for Fusion's own nests, separators and layout furniture —
    115 of that 309 — and ``INPN_Default`` is the stock value a *number* shipped with.
    There is deliberately no ``INPS_Default``: text inputs declare none live, so a fake
    that invented one would hide the branch that has to cope without it.
    """

    def __init__(
        self,
        input_id: str,
        name: str | None = None,
        owner: FakeResolve | None = None,
        external: bool = True,
        default: Any = None,
    ):
        self.input_id = input_id
        self.display_name = name if name is not None else input_id.upper()
        self.external = external
        self.default = default
        self._owner = owner

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def GetAttrs(self, key: str | None = None) -> Any:  # noqa: N802
        self._check()
        attrs: dict[str, Any] = {
            "INPS_ID": self.input_id,
            "INPS_Name": self.display_name,
            "INPB_External": self.external,
        }
        if isinstance(self.default, int | float) and not isinstance(self.default, bool):
            attrs["INPN_Default"] = self.default
        return attrs if key is None else attrs.get(key)


class FakeFusionTool(AnswersNone):
    """One node inside a Fusion comp; for titling only the Text+ node matters.

    ``SetInput`` returns ``None`` in the real API — it reports nothing, so the only way to
    know a write landed is to read it back, which is what the probe does.

    Attribute access is the other half of the Fusion API and behaves nothing like Python's:
    an input is *connected* to a modifier by assigning to an attribute named after it
    (``tool.Opacity1 = comp.BezierSpline()``), and every attribute name answers — an input
    this node does not have reads back as ``None`` rather than raising. Both are modelled,
    because a wrapper that guarded with ``hasattr`` would pass here and fail live.

    ``animatable=False`` models a node that has no such input at all: the assignment is
    accepted, nothing is connected, and the attribute still reads back as ``None``.
    ``reads_at_a_time=False`` models a build whose ``GetInput`` takes no time argument, so
    an animated value cannot be read back at a keyframe.

    ``refuses`` is the write that lies: ``SetInput`` returns ``None`` whether it wrote or
    not, so a named input here takes the call, keeps its old value and says nothing —
    which is what a locked track and a read-only input both look like from Python.
    ``missing`` hides a *method* the way fusionscript does, answering ``None`` rather than
    raising, so a build with no ``GetInputList`` is modelled as ``missing={"GetInputList"}``.

    ``defaults`` is the stock value each *number* shipped with and ``internal`` is the set
    that is not a control at all — both are what a real listing is filtered by, and a fake
    without them would make a 194-input dump look like a reasonable answer.
    """

    def __init__(
        self,
        tool_id: str = "TextPlus",
        name: str = "Template Text",
        inputs: dict[str, Any] | None = None,
        owner: FakeResolve | None = None,
        animatable: bool = True,
        reads_at_a_time: bool = True,
        refuses: frozenset[str] | set[str] | None = None,
        missing: frozenset[str] | set[str] | None = None,
        defaults: dict[str, Any] | None = None,
        internal: frozenset[str] | set[str] | None = None,
    ) -> None:
        # Every field is set while the node is still being built, so nothing here can be
        # mistaken for an input connection — and no list of field names has to be kept in
        # step with this signature for that to hold.
        object.__setattr__(self, "_built", False)
        object.__setattr__(self, "_missing", set(missing or ()))
        self.tool_id = tool_id
        self.name = name
        self.inputs: dict[str, Any] = dict(inputs or {"StyledText": "TEMPLATE"})
        self.animated: dict[str, FakeSpline] = {}
        self.animatable = animatable
        self.reads_at_a_time = reads_at_a_time
        self.refuses = set(refuses or ())
        self.defaults: dict[str, Any] = dict(defaults or {})
        self.internal = set(internal or ())
        self._owner = owner
        object.__setattr__(self, "_built", True)

    def __setattr__(self, key: str, value: Any) -> None:
        if key.startswith("_") or not self._built:
            object.__setattr__(self, key, value)
            return
        self._check()
        if not self.animatable:
            return  # accepted and dropped, exactly as a node without the input answers
        self.animated[key] = value

    def __getattr__(self, key: str) -> Any:
        # Only reached when normal lookup failed, which for fusionscript means "an input
        # or a method this build does not have" — and that answers None, never raises.
        if key.startswith("_"):
            raise AttributeError(key)
        return self.__dict__.get("animated", {}).get(key)

    def copy(self) -> FakeFusionTool:
        """A fresh instance's node: the template's inputs, none of its animation."""
        return FakeFusionTool(
            self.tool_id,
            self.name,
            dict(self.inputs),
            self._owner,
            self.animatable,
            self.reads_at_a_time,
            set(self.refuses),
            set(self._missing),
            dict(self.defaults),
            set(self.internal),
        )

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def GetAttrs(self, key: str | None = None) -> Any:  # noqa: N802
        """Fusion answers a whole dict, or one key. ``TOOLS_RegID`` is the node's type."""
        self._check()
        attrs = {"TOOLS_RegID": self.tool_id, "TOOLS_Name": self.name}
        return attrs if key is None else attrs.get(key)

    def SetInput(self, key: str, value: Any) -> None:  # noqa: N802
        """A write lands only on an input this node actually has.

        A Fusion node's inputs are fixed by its type: ``SetInput`` with an id the node
        does not carry is ignored, and ``GetInput`` then answers ``None``. Nothing in the
        return value says so — it is ``None`` either way — so a fake that grew a new input
        on demand would let a typo'd id read back as a clean write and pass.
        """
        self._check()
        if key in self.refuses or key not in self.inputs:
            return  # taken and dropped: the API reports nothing either way
        self.inputs[key] = value

    def GetInputList(self) -> dict[int, FakeFusionInput]:  # noqa: N802
        """A *one-based dict of Input objects*, the way GetToolList answers with tools."""
        self._check()
        return {
            index: FakeFusionInput(
                key,
                owner=self._owner,
                external=key not in self.internal,
                default=self.defaults.get(key),
            )
            for index, key in enumerate(self.inputs, start=1)
        }

    def GetInput(self, key: str, time: Any = None) -> Any:  # noqa: N802
        """The input's value, at a time when one is asked for and the build answers for it."""
        self._check()
        if time is None:
            return self.inputs.get(key)
        if not self.reads_at_a_time:
            raise TypeError("GetInput() takes 2 positional arguments but 3 were given")
        spline = self.animated.get(key)
        return spline.at(time) if spline is not None else self.inputs.get(key)


class FakeFusionComp(AnswersNone):
    """A timeline item's Fusion composition.

    ``GetToolList`` is filtered by node type in the real API and returns a *one-based dict*
    rather than a list — a comp with no matching node answers an empty dict, not ``None``.

    ``render_range`` is what ``GetAttrs`` reports as the comp's own frame range, which is
    the clock a keyframe time is counted in; ``None`` models a comp that will not say, so
    the caller has to fall back to the placed instance's length. ``missing`` hides a method
    the way fusionscript does — answering ``None`` rather than raising.
    """

    def __init__(
        self,
        tools: Sequence[FakeFusionTool] | None = None,
        owner: FakeResolve | None = None,
        render_range: tuple[int, int] | None = (0, 119),
        takes_keyframes: bool = True,
        missing: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.tools: list[FakeFusionTool] = list(tools if tools is not None else [FakeFusionTool()])
        self.render_range = render_range
        self.takes_keyframes = takes_keyframes
        self._missing = set(missing or ())
        self._owner = owner

    def copy(self) -> FakeFusionComp:
        """What placing a template instance does: the new instance gets its own comp."""
        return FakeFusionComp(
            [tool.copy() for tool in self.tools],
            self._owner,
            self.render_range,
            self.takes_keyframes,
            set(self._missing),
        )

    def adopt(self, owner: FakeResolve) -> None:
        self._owner = owner
        for tool in self.tools:
            tool.adopt(owner)

    def _check(self) -> None:
        if self._owner is not None:
            self._owner._check()

    def GetAttrs(self, key: str | None = None) -> Any:  # noqa: N802
        """The comp's own attributes; the render range is the one titling reads."""
        self._check()
        if self.render_range is None:
            return {}
        start, end = self.render_range
        attrs = {"COMPN_RenderStart": start, "COMPN_RenderEnd": end}
        return attrs if key is None else attrs.get(key)

    def BezierSpline(self) -> FakeSpline:  # noqa: N802
        """Fusion's constructor-style tool creation: ``comp.BezierSpline()`` makes one."""
        self._check()
        return FakeSpline(self.takes_keyframes)

    def GetToolList(  # noqa: N802
        self,
        selected_only: bool = False,
        tool_type: str = "",
    ) -> dict[int, FakeFusionTool]:
        self._check()
        matching = [tool for tool in self.tools if not tool_type or tool.tool_id == tool_type]
        return {index: tool for index, tool in enumerate(matching, start=1)}
