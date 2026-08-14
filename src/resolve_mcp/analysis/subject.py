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

SHARE_PRECISION = 3
"""Decimals a share is reported to. Not seconds — a fraction of a cut, rounded on its own."""

# --- the sidecar's vocabulary: what a camera can be framed on ---------------------------------
#
# ENSEMBLE is the whole band rather than one of its players. FOLLOWS_FRONT is a camera pointed
# at whoever is out front rather than at a fixed player: its shots are on the soloist by
# construction — that is the camera's job — and the solo map is what says who that was. A rig
# with one operated camera and one locked one has exactly one of these, and reading it as a
# player named "soloist" would look for a stem nobody separated.

ENSEMBLE = "ensemble"
FOLLOWS_FRONT = "soloist"

# --- what kind of subject that is -------------------------------------------------------------
#
# One of the band, the band itself (spelled ENSEMBLE, as the subject is), or neither — an
# audience camera, a room shot.

PLAYER = "player"
OTHER = "other"

# --- the lines a shot's screen time is counted on ----------------------------------------------
#
# On the player out front, on the band, on a player who was not soloing, on something that is
# neither (an audience camera, a room shot), on nobody the sidecar has named, or on nothing at
# all. Three of these names were chosen carefully.
# ON_OTHER is not ``other``: the residual stem is *called* ``other``, so a line by that name
# sitting beside a solo map whose front reads ``other`` would be two things spelled the same.
# ON_ELSEWHERE exists because a room shot is not a player: folded into ON_OTHER it would read
# as a cut watching the wrong musician, which is a different fact about a cut than a cut
# watching the room, and the aggregate is where that difference would otherwise be lost.
# ON_SOLOIST *is* spelled like FOLLOWS_FRONT, and that one is a real coincidence rather than an
# accident: a shot from the camera that follows the front is on the soloist by definition, so
# the subject and the line it lands on are the same word about the same fact.
# BLACK is kept apart from UNLABELLED for the reason the angle shares keep them apart: how much
# of a cut is empty is a fact about the edit, and how much of it the sidecar has not named is a
# fact about the sidecar. Added together neither is readable.

ON_SOLOIST = "soloist"
ON_ENSEMBLE = "ensemble"
ON_OTHER = "other_player"
ON_ELSEWHERE = "elsewhere"
UNLABELLED = "unlabelled"
BLACK = "black"

FRONT_MATCH = "front_match"
FOLLOW_CAMERA = "follow_camera"
"""How a shot came to be on the soloist: the subject matched the front the solo map measured,
or the sidecar says this camera follows the front and the shot was taken at its word."""

ORDER = (ON_SOLOIST, ON_ENSEMBLE, ON_OTHER, ON_ELSEWHERE, UNLABELLED, BLACK)
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

    Where that rule is wrong it is wrong in one direction, and the sidecar is what fixes it: a
    player who never took the front all night is on no roster either, and reads as neither.
    Naming that camera's ``voice`` — the stem the solo map would have called them — is the
    escape hatch, and a solo map that covers the whole night is what makes it unnecessary.
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
    known: frozenset[str],
    start: float,
    end: float,
    spans: Sequence[Window],
    black: bool = False,
) -> dict[str, Any]:
    """One shot against the solo windows: what it is on, where its screen time went, and the
    one-word verdict.

    The kind is worked out here rather than passed in, because it is a fact about the subject
    and the solo map's roster and about nothing else — the caller that had to compute it first
    could only ever have computed it this way.

    ``on_soloist`` is the reading a critic quotes and the seconds are what it was taken from:
    the verdict is whichever line holds the most of the shot, and a shot split evenly takes
    the one it opened on — the tie is broken by time rather than by name, so renaming a
    player cannot move a verdict.

    ``black`` is a stretch nothing covers, which is not a shot with no label on it: it is
    counted on its own line and its verdict is nothing rather than false.
    """
    subject_kind = kind(subject, known)
    seconds = _split(subject, subject_kind, start, end, spans, black)
    verdict = None if not seconds else _verdict(seconds)
    on_soloist = None if verdict in (None, UNLABELLED, BLACK) else verdict == ON_SOLOIST
    return {
        "subject_kind": subject_kind,
        "on_soloist": on_soloist,
        # How the shot got onto that line, because the two ways are not equally well known: a
        # subject that *matches* the front was joined against the solo map, while a camera
        # whose whole job is the front was taken at the sidecar's word. Both are real readings
        # and only one of them is a measurement — a rig with a follow camera can otherwise post
        # a high share on the soloist without anything ever having been measured.
        "on_soloist_by": _by(subject, subject_kind, on_soloist),
        "on_soloist_seconds": seconds or None,
    }


def _by(subject: Subject | None, subject_kind: str | None, on_soloist: bool | None) -> str | None:
    """``follow_camera`` where the sidecar asserted it, ``front_match`` where the join found it."""
    if not on_soloist or subject is None or subject_kind != PLAYER:
        return None
    return FOLLOW_CAMERA if subject.voice == FOLLOWS_FRONT else FRONT_MATCH


def _verdict(seconds: Mapping[str, float]) -> str:
    """The line a shot is called by: whichever holds most of it, ties to the one it opened on.

    The dict is built in the order the windows were played, so ``max`` returning the first of
    equal values *is* the tie-break — it does not need one of its own.
    """
    return max(seconds, key=lambda line: float(seconds[line]))


def _split(
    subject: Subject | None,
    subject_kind: str | None,
    start: float,
    end: float,
    spans: Sequence[Window],
    black: bool,
) -> dict[str, float]:
    """How many seconds of this shot fall on each line, in the order they were played."""
    seconds: dict[str, float] = {}
    for span in spans:
        if span.front is None:
            continue
        overlap = min(end, span.end) - max(start, span.start)
        if overlap <= 0:
            continue
        line = _line(subject, subject_kind, span.front, black)
        seconds[line] = round(seconds.get(line, 0.0) + overlap, SECONDS_PRECISION)
    return seconds


def _line(subject: Subject | None, subject_kind: str | None, front: str, black: bool) -> str:
    if black:
        return BLACK
    if subject is None or subject_kind is None:
        return UNLABELLED
    if subject_kind == ENSEMBLE:
        return ON_ENSEMBLE
    if subject_kind != PLAYER:
        return ON_ELSEWHERE
    if subject.voice == FOLLOWS_FRONT or subject.voice == front:
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
    asserted = 0.0
    for row in rows:
        split = row.get("on_soloist_seconds")
        if not isinstance(split, Mapping) or not split:
            continue
        for line, held in split.items():
            seconds[str(line)] = round(seconds.get(str(line), 0.0) + float(held), SECONDS_PRECISION)
        if row.get("on_soloist_by") == FOLLOW_CAMERA:
            asserted = round(asserted + float(split.get(ON_SOLOIST, 0.0)), SECONDS_PRECISION)
        verdict = _verdict({str(line): float(held) for line, held in split.items()})
        shots[verdict] = shots.get(verdict, 0) + 1
    if not seconds:
        return None
    apart = (UNLABELLED, BLACK)
    labelled = round(
        sum(held for line, held in seconds.items() if line not in apart), SECONDS_PRECISION
    )
    return {
        "solo_window_seconds": round(sum(seconds.values()), SECONDS_PRECISION),
        "labelled_seconds": labelled,
        "unlabelled_seconds": seconds.get(UNLABELLED, 0.0),
        "black_seconds": seconds.get(BLACK, 0.0),
        "seconds": {
            line: seconds[line] for line in ORDER if line in seconds and line not in apart
        },
        "fraction_on_soloist": _share(seconds.get(ON_SOLOIST, 0.0), labelled),
        "fraction_on_ensemble": _share(seconds.get(ON_ENSEMBLE, 0.0), labelled),
        "fraction_on_other_player": _share(seconds.get(ON_OTHER, 0.0), labelled),
        "fraction_elsewhere": _share(seconds.get(ON_ELSEWHERE, 0.0), labelled),
        # How much of the soloist line was asserted by a follow camera's label rather than
        # joined against the solo map. Zero is the usual answer and the reason to print it:
        # a share that is mostly this is a share of the sidecar's word, not of the music.
        "soloist_seconds_by_follow_camera": asserted,
        "shots": {line: shots[line] for line in ORDER if line in shots},
    }


def _share(held: float, total: float) -> float | None:
    return None if total <= 0 else round(held / total, SHARE_PRECISION)
