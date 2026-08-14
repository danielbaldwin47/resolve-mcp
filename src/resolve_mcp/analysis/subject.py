"""Which player a shot is framed on, crossed with who is out front.

The core concert question, and the one a blind viewer-judge kept asking for: not "how long
is the shot" but "is it on the person playing". Two documents already answer half of it each
— the angle sidecar says what a camera is framed on, the solo changes say who was out front
— and neither is useful alone. This joins them.

*Nothing here decides.* A cut that spends its solos on the drummer is reported as a cut that
spends its solos on the drummer; whether that is wrong is the style profile's business (#21).

Two rules keep the numbers honest.

*Seconds, not verdicts at the cut.* A shot that opens on a drum solo and runs four seconds
past the horn's entrance is two facts, and reading the front at the cut alone would record
only the first. Every reading here is screen time inside solo windows, split where the front
changes, so "62% of the solo-window screen time is on the soloist" is arithmetic over the
records rather than an impression.

*Unattributable screen time is counted apart.* A shot from a camera the sidecar has not
labelled is not a shot away from the soloist. Folded into the denominator it reads as a cut
ignoring the player; dropped silently it reads as a cut with nothing to answer for. It gets
its own line, so a fraction is always read next to how much of the cut it could not see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ..timing import SECONDS_PRECISION

ENSEMBLE = "ensemble"
"""The subject that is the whole band rather than one of its players."""

FOLLOWS_FRONT = "soloist"
"""The subject of a camera pointed at whoever is out front, rather than at a fixed player.

Its shots are on the soloist by construction — that is the camera's job — and the solo map
is what says who that was. A rig with one operated camera and one locked one has exactly one
of these, and reading it as a player named "soloist" would look for a stem nobody separated.
"""

PLAYER = "player"
OTHER = "other"
"""What a subject can be: one of the band, the band itself, or neither (audience, room)."""

ON_SOLOIST = "soloist"
ON_ENSEMBLE = "ensemble"
ON_OTHER = "other"
UNLABELLED = "unlabelled"
"""Where a shot's screen time is counted: on the player out front, on the band, on somebody
else, or nowhere anyone can attribute."""

ORDER = (ON_SOLOIST, ON_ENSEMBLE, ON_OTHER, UNLABELLED)
"""The order the readings are reported in, so two runs of the same cut compare line by line."""


class Subject(NamedTuple):
    """What a camera is framed on, and what the solo map calls that.

    Two fields because the two documents need not share a vocabulary: a sidecar may name the
    player ("mike") where the solo map names the stem he is heard on ("wind"). Where the
    sidecar says nothing else, the two are the same string.
    """

    name: str
    voice: str


class Window(NamedTuple):
    """A stretch of the concert with one voice out front. ``front`` unknown is not a window
    anything can be measured against — it is the part of the concert the solo map does not
    describe."""

    start: float
    end: float
    front: str | None


def subject_of(entry: Any) -> Subject | None:
    """What one angle-sidecar entry says its camera is framed on, or nothing.

    Read from ``subject`` where the entry names one. Otherwise from ``role``, which the
    corpus writes as ``<subject>-<character>`` ("drums-tight", "ensemble-wide") — its head is
    a subject. A one-word role is a *character* ("wide", "moving"): it says how the camera
    frames, not what it is framed on, and turning that into a subject would invent a fact the
    sidecar did not state.

    ``voice`` is the escape hatch for the sidecar whose subjects are people rather than
    stems: it is what the solo map calls this subject, and the join uses it.
    """
    if isinstance(entry, str):
        return _named(_head(entry), None)
    if not isinstance(entry, Mapping):
        return None
    named = entry.get("subject")
    role = entry.get("role")
    name = named if isinstance(named, str) else _head(role) if isinstance(role, str) else None
    voice = entry.get("voice")
    return _named(name, voice if isinstance(voice, str) else None)


def _named(name: str | None, voice: str | None) -> Subject | None:
    if name is None or not name.strip():
        return None
    return Subject(name, (voice or name))


def _head(role: str) -> str | None:
    """The subject half of a ``<subject>-<character>`` role, or nothing for a bare one."""
    subject, dash, character = role.partition("-")
    return subject if dash and subject and character else None


def voices(rows: Sequence[Mapping[str, Any]] | None) -> frozenset[str]:
    """Every voice the solo map names — the only roster of players that was measured."""
    if not rows:
        return frozenset()
    found = {
        str(row[key]) for row in rows for key in ("from", "to") if isinstance(row.get(key), str)
    }
    return frozenset(found)


def kind(subject: Subject | None, known: frozenset[str]) -> str | None:
    """Whether this subject is a player, the ensemble, or neither.

    The roster is the solo map's own, not a list this file keeps: a subject it never names is
    an audience camera or a room shot as far as anything measurable goes, and counting that
    as a player away from the front would make a cut look like it was watching the wrong
    person. With no solo map there is no roster to contradict the sidecar, so a named subject
    is taken at its word.
    """
    if subject is None:
        return None
    if subject.name == ENSEMBLE:
        return ENSEMBLE
    if subject.voice == FOLLOWS_FRONT:
        return PLAYER
    if known and subject.voice not in known:
        return OTHER
    return PLAYER


def windows(rows: Sequence[Mapping[str, Any]] | None) -> tuple[Window, ...]:
    """The solo changes read as stretches: who was out front, from when to when.

    Before the first change there is still someone out front — the change records who it was
    taken over *from* — and after the last one the front holds to the end of the concert. The
    ends are unbounded on purpose: this measures shots, and a shot past the last change is
    still on whoever the map left out front.
    """
    if not rows:
        return ()
    ordered = sorted(rows, key=lambda row: float(row["t"]))
    first = ordered[0].get("from")
    held: str | None = str(first) if isinstance(first, str) else None
    built: list[Window] = []
    start = float("-inf")
    for row in ordered:
        moment = float(row["t"])
        built.append(Window(start, moment, held))
        entered = row.get("to")
        held = str(entered) if isinstance(entered, str) else held
        start = moment
    built.append(Window(start, float("inf"), held))
    return tuple(built)


def reading(
    subject: Subject | None,
    subject_kind: str | None,
    start: float,
    end: float,
    spans: Sequence[Window],
) -> dict[str, Any]:
    """One shot against the solo windows: where its screen time went, and the one-word verdict.

    ``on_soloist`` is the reading a critic quotes and the seconds are what it was taken from:
    the verdict is whichever line holds the most of the shot, and a shot split evenly takes
    the one it opened on — the tie is broken by time rather than by name, so renaming a
    player cannot move a verdict.
    """
    seconds = _split(subject, subject_kind, start, end, spans)
    if not seconds:
        return {"on_soloist": None, "on_soloist_seconds": None}
    verdict = max(seconds, key=lambda line: seconds[line])
    return {
        "on_soloist": None if verdict == UNLABELLED else verdict == ON_SOLOIST,
        "on_soloist_seconds": seconds,
    }


def _split(
    subject: Subject | None,
    subject_kind: str | None,
    start: float,
    end: float,
    spans: Sequence[Window],
) -> dict[str, float]:
    """How many seconds of this shot fall on each line, in the order they were played."""
    seconds: dict[str, float] = {}
    for span in spans:
        if span.front is None:
            continue
        overlap = min(end, span.end) - max(start, span.start)
        if overlap <= 0:
            continue
        line = _line(subject, subject_kind, span.front)
        seconds[line] = round(seconds.get(line, 0.0) + overlap, SECONDS_PRECISION)
    return seconds


def _line(subject: Subject | None, subject_kind: str | None, front: str) -> str:
    if subject is None or subject_kind is None:
        return UNLABELLED
    if subject_kind == ENSEMBLE:
        return ON_ENSEMBLE
    if subject_kind == PLAYER and (subject.voice == FOLLOWS_FRONT or subject.voice == front):
        return ON_SOLOIST
    return ON_OTHER


def summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The list-free reading: what share of the measured screen time is on the player playing.

    ``None`` when nothing was measured at all — no solo map, or no angle labels — because a
    cut that was never asked the question and a cut that never went near the soloist are
    opposite facts and a zero would read as the second.
    """
    seconds: dict[str, float] = {}
    shots: dict[str, int] = {}
    for row in rows:
        split = row.get("on_soloist_seconds")
        if not isinstance(split, Mapping) or not split:
            continue
        for line, held in split.items():
            seconds[str(line)] = round(seconds.get(str(line), 0.0) + float(held), SECONDS_PRECISION)
        verdict = max(split, key=lambda line: float(split[line]))
        shots[str(verdict)] = shots.get(str(verdict), 0) + 1
    if not seconds:
        return None
    labelled = round(sum(held for line, held in seconds.items() if line != UNLABELLED), 3)
    return {
        "solo_window_seconds": round(sum(seconds.values()), SECONDS_PRECISION),
        "labelled_seconds": labelled,
        "unlabelled_seconds": seconds.get(UNLABELLED, 0.0),
        "seconds": {
            line: seconds[line] for line in ORDER if line in seconds and line != UNLABELLED
        },
        "fraction_on_soloist": _share(seconds.get(ON_SOLOIST, 0.0), labelled),
        "fraction_on_ensemble": _share(seconds.get(ON_ENSEMBLE, 0.0), labelled),
        "fraction_on_other": _share(seconds.get(ON_OTHER, 0.0), labelled),
        "shots": {line: shots[line] for line in ORDER if line in shots},
    }


def _share(held: float, total: float) -> float | None:
    return None if total <= 0 else round(held / total, 3)
