"""Where a cut sits in the form: the join onto the bar map (#180).

``bars.py`` writes the bar map — one record per bar, each with its place in the four-bar
group. This is the other half: reading a cut against it, so a shot has a bar of the form under
it rather than only a pulse. The two are apart because they answer to different things — the
map is a measurement of the music, this is a measurement of an edit against that measurement,
and the second one exists precisely for the material where the first one had to be run.

*Why a second bar column at all.* Every beat in the grid already carries a bar number, but only
the one the beat model committed to; on material where it commits to nothing that column is a
meter of one, and the whole reading collapses. The bar map is the second opinion that recovers
a real bar line, so a report can carry both and say which is which — ``bar``/``in_bar`` from
the grid, ``map_bar``/``in_group``/``bar_offset`` from here.

*Nearest, not containing.* The bar line is read the way the beat is: nearest, with a signed
offset, negative early. A cut twenty milliseconds *before* a downbeat is a cut on the one, and
a rule that filed it under the bar it technically falls inside would misfile the commonest
placement in this material every time.

The interface mirrors ``subject``: ``reading`` for the columns one cut carries, ``summary`` for
the list-free blocks taken over them. Neither touches a file or a Resolve handle — bar records
in, dicts out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .beats import nearest
from .stats import histogram, measured, offsets, rounded

COLUMNS = ("map_bar", "in_group", "bar_offset")
"""The columns a record carries from this join, unmeasured as ``None`` on all three."""


def times(bars: Sequence[Mapping[str, Any]] | None) -> list[float]:
    """The bar lines as seconds, in the order the map wrote them — what ``reading`` searches."""
    return [float(row["t"]) for row in (bars or ())]


def reading(
    bars: Sequence[Mapping[str, Any]] | None,
    lines: Sequence[float],
    seconds: float,
) -> dict[str, Any]:
    """One cut against the bar map: which bar of the form, where in the group, how far off.

    All three columns are ``None`` together when no map was named — a cut with no bar map is a
    cut nobody asked the question of, and a zero offset would read as a cut landing dead on a
    line that was never measured.

    ``lines`` is ``times(bars)``, hoisted by the caller because a measurement runs this once per
    cut and rebuilding the list each time would be the same list several thousand times over.
    """
    found = None if not lines else nearest(lines, seconds)
    line = None if found is None else (bars or ())[found]
    if line is None:
        return dict.fromkeys(COLUMNS)
    return {
        "map_bar": line.get("bar"),
        "in_group": line.get("in_group"),
        "bar_offset": rounded(seconds - float(line["t"])),
    }


def summary(
    rows: Sequence[Mapping[str, Any]], cuts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The two list-free blocks: where in the group the cuts land, and how far off the line.

    Deliberately not gated on ``in_grid``. The beat gate refuses beats the *grid* describes
    badly, and a bar map exists precisely for the grids that get refused wholesale — gating
    this on the grid's verdict would empty the one reading that still had something to say.

    ``rows`` is every record and ``cuts`` the ones cut to music, and the two are different
    questions: whether the column was measured at all is asked of the whole file, while an
    opening is not a cut to the music and would drag a meaningless offset into the statistics.
    """
    return {
        "bar_groups": (
            histogram(row["in_group"] for row in cuts) if measured("in_group", rows) else None
        ),
        "bar_offsets": (
            offsets([row["bar_offset"] for row in cuts]) if measured("bar_offset", rows) else None
        ),
    }
