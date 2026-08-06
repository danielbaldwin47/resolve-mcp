"""The current-timeline getter sweep (#84, the outstanding half of #68).

One question, asked of every getter rather than of the two that were caught: **does this
getter answer differently when its timeline is not the project's current one?** Resolve
answers some of them from editor state, and for any other timeline returns the falsy value
of their own type — no exception, no ``None`` — which is indistinguishable from a genuinely
empty selector or a muted track. ADR 0004 records what this found.

So this is a probe, not a wrapper: it lives in ``tests/`` because its output is a finding
for a ticket, not a tool for an agent. It needs real Resolve — the fakes hand back the same
object whether or not a timeline is current, so there is nothing for it to measure there.

Three things here are decisions rather than API calls:

* **Every getter is swept, not a chosen few.** The two the ticket opened on were found by
  accident, a week apart, each after it had cost someone a wrong conclusion. A list of
  suspects assembled by reasoning would have missed ``GetIsTrackLocked``, which this found.
* **A falsy true value proves nothing, and is reported as such.** ``GetTakesCount`` reading
  ``0 -> 0`` says only that the clip has no takes. Those keys come back under ``vacuous``
  rather than being counted as safe, and the fixture is the thing to fix: give the getter a
  non-falsy true value — a real selector, a locked track, a source offset, a marker — and
  ask again. Every getter this repo reads was driven out of ``vacuous`` this way before
  ADR 0004 called the other ninety proven.
* **The target is read three times, not two.** The third read, back on the current
  timeline, catches a probe that disturbs what it measures — and did: switching timelines
  moves the viewer playhead, which is why ``inspect_timeline`` does not switch by default.

Run it against a project holding at least two timelines:

    uv run python -m tests.currency_probe [target-timeline-name]

It exits non-zero when anything drifted, and is read-only apart from the switch, which it
puts back.
"""

from __future__ import annotations

import sys
from typing import Any, NamedTuple

from resolve_mcp.resolve.connection import get_connection

SKIP = frozenset(
    {
        # Megabytes of base64, and nothing reads it.
        "GetCurrentClipThumbnailImage",
        # The whole settings dict; its own question, and it drowns the diff.
        "GetSetting",
        # Deliberately unstable per proxy — comparing it would flag every key.
        "GetUniqueId",
    }
)
KINDS = ("video", "audio", "subtitle")
MAX_ITEMS = 4
FALSY = frozenset({"False", "0", "0.0", "None", "", "<list len=0>", "<tuple len=0>"})

Timeline = Any
Project = Any


class Sweep(NamedTuple):
    """What three reads of one timeline established about its getters."""

    target: str
    other: str
    drift: dict[str, tuple[Any, Any]]
    """Currency-sensitive: read one way while current, another way while not."""
    unstable: dict[str, tuple[Any, Any]]
    """Changed between the first and third read — the probe disturbed it, or Resolve did."""
    proven: list[str]
    """Non-falsy while current and unchanged while not: safe, not merely untested."""
    vacuous: list[str]
    """Already falsy while current, so a lie would be invisible. Unproven, not safe."""


def render(value: Any) -> Any:
    """A small, stable, comparable rendering of whatever a getter handed back."""
    if isinstance(value, bool | int | float | str) or value is None:
        text = str(value)
        return text if len(text) <= 120 else text[:120] + "..."
    if isinstance(value, list | tuple):
        return f"<{type(value).__name__} len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={sorted(value)[:8]}>"
    return f"<{type(value).__name__}>"


def zero_arg_getters(obj: Any) -> dict[str, Any]:
    """Every ``Get``/``Is`` name that answers without arguments.

    A getter that needs arguments raises ``TypeError`` and is covered explicitly by
    :func:`sweep`; one that raises anything else has said something worth recording, so the
    exception is kept as the reading rather than dropped.
    """
    out: dict[str, Any] = {}
    for name in sorted(dir(obj)):
        if name in SKIP or not (name.startswith("Get") or name.startswith("Is")):
            continue
        try:
            out[name] = render(getattr(obj, name)())
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001 - a raising getter is itself a datum
            out[name] = f"<raised {type(exc).__name__}: {exc}>"
    return out


def read_everything(timeline: Timeline) -> dict[str, Any]:
    """One full reading of a timeline: its own getters, its tracks', and its shots'."""
    reading: dict[str, Any] = {}
    for key, value in zero_arg_getters(timeline).items():
        reading[f"timeline.{key}"] = value

    for kind in KINDS:
        try:
            count = int(timeline.GetTrackCount(kind) or 0)
        except Exception as exc:  # noqa: BLE001
            reading[f"timeline.GetTrackCount({kind})"] = f"<raised {type(exc).__name__}: {exc}>"
            continue
        reading[f"timeline.GetTrackCount({kind})"] = count
        for index in range(1, count + 1):
            for getter in ("GetTrackName", "GetIsTrackEnabled", "GetIsTrackLocked"):
                key = f"timeline.{getter}({kind},{index})"
                try:
                    reading[key] = render(getattr(timeline, getter)(kind, index))
                except Exception as exc:  # noqa: BLE001
                    reading[key] = f"<raised {type(exc).__name__}: {exc}>"
            try:
                items = timeline.GetItemListInTrack(kind, index) or []
            except Exception as exc:  # noqa: BLE001
                reading[f"timeline.GetItemListInTrack({kind},{index})"] = (
                    f"<raised {type(exc).__name__}: {exc}>"
                )
                continue
            reading[f"timeline.GetItemListInTrack({kind},{index})"] = len(items)
            for position, item in enumerate(items[:MAX_ITEMS]):
                for key, value in zero_arg_getters(item).items():
                    reading[f"item[{kind},{index},{position}].{key}"] = value
    return reading


def sweep(project: Project, target: Timeline, other: Timeline) -> Sweep:
    """Read ``target`` current, not current, then current again, and classify every key.

    The project's own current timeline is restored whatever happens: the probe is run on a
    machine someone is working at.
    """
    was = project.GetCurrentTimeline()
    try:
        project.SetCurrentTimeline(target)
        first = read_everything(target)
        project.SetCurrentTimeline(other)
        away = read_everything(target)
        project.SetCurrentTimeline(target)
        again = read_everything(target)
    finally:
        if was is not None:
            project.SetCurrentTimeline(was)

    drift = {key: (first[key], away[key]) for key in first if first[key] != away.get(key)}
    unstable = {key: (first[key], again[key]) for key in first if first[key] != again.get(key)}
    return Sweep(
        target=str(target.GetName()),
        other=str(other.GetName()),
        drift=drift,
        unstable=unstable,
        proven=sorted(k for k in first if k not in drift and str(first[k]) not in FALSY),
        vacuous=sorted(k for k in first if k not in drift and str(first[k]) in FALSY),
    )


def timelines_of(project: Project) -> list[Timeline]:
    held = [
        project.GetTimelineByIndex(index)
        for index in range(1, int(project.GetTimelineCount() or 0) + 1)
    ]
    return [timeline for timeline in held if timeline is not None]


def main(argv: list[str]) -> int:
    project = get_connection().handle().GetProjectManager().GetCurrentProject()
    if project is None:
        print("No project open in Resolve.")
        return 2
    held = timelines_of(project)
    names = [str(timeline.GetName()) for timeline in held]
    if len(held) < 2:
        print(f"The sweep needs two timelines; this project has {names}.")
        return 2

    wanted = argv[1] if len(argv) > 1 else None
    if wanted is None:
        target = project.GetCurrentTimeline() or held[0]
    else:
        found = [t for t in held if str(t.GetName()) == wanted]
        if not found:
            print(f"No timeline named {wanted!r}; this project has {names}.")
            return 2
        target = found[0]
    name = str(target.GetName())
    other = next(t for t in held if str(t.GetName()) != name)

    result = sweep(project, target, other)

    print(f"target={result.target!r} other={result.other!r}")
    print(f"CURRENCY-SENSITIVE (current -> non-current): {len(result.drift)}")
    for key, (current, away) in sorted(result.drift.items()):
        print(f"  {key}: {current!r} -> {away!r}")
    print(f"UNSTABLE (first read vs third, should be 0): {len(result.unstable)}")
    for key, (first, third) in sorted(result.unstable.items()):
        print(f"  {key}: {first!r} -> {third!r}")
    print(f"PROVEN CURRENCY-SAFE: {len(result.proven)}")
    print(f"VACUOUS (true value already falsy; unproven): {len(result.vacuous)}")
    for key in result.vacuous:
        print(f"  {key}")
    return 1 if result.drift else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
