"""The readings taken over a column of records, shared by everything that takes one.

Small on purpose. Three of these — how far off a column of offsets is, how its values
distribute, whether it was measured at all — are asked by the cut report and by every join
that contributes a block to it, and a second copy of "early is negative" or of "a column
nobody named reads as nothing, not zero" is a second rule waiting to disagree with the first.

Nothing here knows what a cut is. A sequence of numbers in, a dict out.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from ..timing import SECONDS_PRECISION


def rounded(seconds: float) -> float:
    """Seconds at the precision every time in a report is written to."""
    return round(seconds, SECONDS_PRECISION)


def measured(field: str, rows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a column was measured at all — an input nobody named reads as nothing, not zero."""
    return any(row[field] is not None for row in rows)


def offsets(found: Sequence[float | None]) -> dict[str, Any] | None:
    """How far off the cuts are, and which way — early and late counted apart."""
    values = [value for value in found if value is not None]
    if not values:
        return None
    sizes = [abs(value) for value in values]
    return {
        "measured": len(values),
        "mean_abs": rounded(statistics.fmean(sizes)),
        "median_abs": rounded(statistics.median(sizes)),
        "max_abs": rounded(max(sizes)),
        "early": sum(1 for value in values if value < 0),
        "late": sum(1 for value in values if value > 0),
        "on": sum(1 for value in values if value == 0),
    }


def histogram(values: Any) -> dict[str, int]:
    """Counts keyed by value as a string — JSON has no integer keys to speak of."""
    counted = Counter(value for value in values if value is not None)
    return {str(key): count for key, count in sorted(counted.items(), key=lambda one: str(one[0]))}
