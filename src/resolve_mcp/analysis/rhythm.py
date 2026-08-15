"""How varied the cutting is, and whether the music's dynamics moved it.

The failure a build cannot see from the inside is the one every critic names first: two
cameras traded back and forth on a fixed length. It is invisible in the offsets — every cut
can sit dead on its beat and still read as a metronome — so it needs a reading of its own,
taken over the shot list rather than over the music: how the lengths spread, how strictly the
angles alternate, how much of the cut sits in one length bin, how far it ramps.

``gears`` asks the other half of the same question, and is the one reading here that needs the
music. A cut can vary every length and still run at one speed all night — the intro cut at the
pace of the solo — and no reading over the shot list alone can see that, because the thing
missing is the dynamics. So the level curve is split into thirds by loudness and the rate is
reported per third. ``quiet_floor`` then reads the inside of the slow gear: hitting the quiet
rate and parking are the same number until the lengths inside a passage are looked at.

*Nothing here decides.* ``reads_metronomic``, ``one_speed`` and ``reads_locked`` are sentences
the report says out loud beside the numbers and the rules that drew them (``HEURISTIC``,
``GEAR_HEURISTIC``, ``FLOOR_HEURISTIC``), so a builder can disagree on the evidence rather than
argue with a threshold. Trading two cameras for four minutes is a real edit some music asks
for; the point is that the builder *decided* it.

The interface a caller uses is one function. ``read(rows, levels)`` takes the per-cut records
the report is built from — ``t``, ``seconds`` and ``clip`` are the only columns touched — and
the loudness curve, and returns the ``shot_rhythm`` block. No Resolve handle, no files, no job
record: the arithmetic is testable from a list of dicts written by hand.

One reader goes underneath it on purpose. ``gauntlet/recon/quiet_floor.py`` is the receipt
behind #190's corpus row, and it reads a passage at a time against sections the report knows
nothing about; it imports the private helpers rather than reimplementing them, so the receipt
cannot go on describing arithmetic the server has changed. They stay private because that is
what they are — a receipt is not a second caller to keep an interface stable for, and it lives
in this repo where a rename reaches it.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import Any

from .stats import rounded

BLACK = "black"
"""What a stretch nothing covers is called in the sequence of angles.

Spelled the same as the report's own black line and named here rather than imported, for the
reason ``subject.py`` names its own: this module reads a column, and a shared constant would
tie its vocabulary to a caller it otherwise knows nothing about.
"""

RHYTHM_BINS: tuple[tuple[str, float], ...] = (
    ("<2", 2.0),
    ("2-4", 4.0),
    ("4-8", 8.0),
    ("8-15", 15.0),
    ("15-30", 30.0),
    (">30", float("inf")),
)
"""The corpus shot-length bins, as label and upper edge, in seconds.

Half-open on the upper edge: a shot of exactly 4 s counts in ``4-8``, and the last bin takes
everything from 30 s up. The labels are the ones the corpus and the style profiles already
speak, so a histogram from this report drops straight into a comparison against them; the
edges are that vocabulary's, not a threshold this file tuned.
"""

ALTERNATION_MIN = 2
"""How many cuts an A/B run needs before it is alternation rather than a cut.

Two shots that differ are just a cut — the pattern only exists once the cut *returns*, so a
run is counted from three shots (A B A) up. Without this floor every two-shot timeline reads
as perfectly alternating, which is arithmetic rather than a fact about the edit.
"""

RAMP_MIN = 4
"""How many cuts of one direction make a ramp rather than a coincidence.

Three shots that happen to shorten are two cuts, and two cuts in a row go one way in any cut
list long enough — the shape only exists once it keeps going. Counted from five shots (four
cuts) up, which is where a run stops being what randomness hands you.
"""

ALTERNATION_FLOOR = 0.8
ONE_BIN_FLOOR = 0.6
CV_FLOOR = 0.35
RAMP_FLOOR = 0.6
"""The four numbers ``reads_metronomic`` is drawn at — see ``HEURISTIC``."""

HEURISTIC = (
    "reads_metronomic is true when the longest strict A/B alternation run covers more than "
    f"{ALTERNATION_FLOOR} of the cuts and the lengths are mechanical with it: either one bin "
    f"holds more than {ONE_BIN_FLOOR} of the shots, or the coefficient of variation of shot "
    f"lengths is under {CV_FLOOR}, or the longest run of shots that only shorten or only "
    f"lengthen covers more than {RAMP_FLOOR} of the cuts. It is a warning to look at, not a "
    "verdict: a passage that genuinely wants a two-camera ping-pong scores the same as a cut "
    "nobody varied, and only the director can tell them apart."
)
"""The heuristic in words, carried in the report so nobody has to read this file to check it."""

GEAR_WINDOW_SECONDS = 1.0
"""How coarse the loudness curve the gearing is read against is, in seconds per window.

A second is a bar at slow rock tempo and half of one at anything faster: coarse enough that
a snare hit does not move a window into the loud third, fine enough that the quiet verse and
the last chorus land in different ones. Nothing here is a loudness measurement anybody
publishes — the only question asked of it is which windows are louder than which.
"""

QUIET, MID, LOUD = "quiet", "mid", "loud"
"""The three gears, named where the report names them."""

SUB_TWO_SECONDS = RHYTHM_BINS[0][1]
"""What counts as a short shot: the corpus's own ``<2`` edge, not a second threshold."""

RATE_RATIO_FLOOR = 1.3
GEAR_CV_FLOOR = 0.65
"""The two numbers ``one_speed`` is drawn at — see ``GEAR_HEURISTIC``."""

GEAR_HEURISTIC = (
    "one_speed is true when the loud third of the music is cut less than "
    f"{RATE_RATIO_FLOOR}x as fast as the quiet third (rate_ratio) and the coefficient of "
    f"variation of shot lengths is under {GEAR_CV_FLOOR}. Terciles are thirds of the span by "
    "level, not by time: the 1 s RMS windows inside the cut are ranked and split three ways, "
    "and each shot is counted in the window its first frame lands in — a shot the curve does "
    "not reach is counted in outside_shots and in no tercile. It is a warning to look "
    "at, not a verdict — a ballad cut at one speed throughout is a real edit, and a passage "
    "whose loudness never moves has no gears to change. What it catches is the build that cut "
    "the guitar solo at the pace it cut the intro."
)
"""The gearing heuristic in words, carried in the report beside the numbers it was drawn from."""

QUIET_SMOOTHING_WINDOWS = 15
"""How many gear windows the level curve is smoothed over before quiet passages are found.

The terciles above label one window at a time, and at that resolution a live room's level
crosses the quiet edge and back inside a single bar — a snare hit, a shout, a chord. Labels
that flicker are exactly what a *rate* wants, since the rate is over the music each label
holds however scattered it is, but a passage is not scattered: it is a stretch you sit in.
A centred moving median over fifteen windows is the coarsest reading that still separates
this corpus's sections, which run from thirty seconds to a minute and a half.
"""

QUIET_FLOOR_SECONDS = 20.0
"""How long a quiet stretch must run before its shot lengths are read as a passage.

Shorter than this is a pocket, and a pocket holds two or three shots — too few for a spread
to mean anything, and short enough that holding through it is a gesture rather than a stall.
"""

ORPHAN_FRACTION = 0.5
"""How much shorter than the passage's median a shot must be to count as an orphan.

Half is the line because it puts a flash on the other side of the passage's own scale rather
than of a fixed threshold: in a floor of ten-second holds a four-second shot is a shorter
shot, and a two-second one is a different device.
"""

FLOOR_CV_FLOOR = 0.65
"""The number ``reads_locked`` is drawn at — see ``FLOOR_HEURISTIC``.

The same value ``GEAR_CV_FLOOR`` holds, and written out rather than aliased to it: it is the
same question asked of a smaller span — are these lengths varied enough to read as cutting —
but it is not the same knob, and retuning what a whole cut is judged at should not silently
retune what a passage inside one is. Measured on the one full song in the corpus, the
director's own quiet passage runs a spread of 0.78 across 157 s and ours 0.56; the floor sits
between them, on a gap of one song, which is all it is worth.
"""

FLOOR_HEURISTIC = (
    "quiet_floor reads the passages a build holds the slow gear through: the 1 s levels are "
    f"smoothed over {QUIET_SMOOTHING_WINDOWS} windows, the quiet third of the smoothed curve "
    f"is taken, and its contiguous stretches of at least {QUIET_FLOOR_SECONDS} s are the "
    "passages. reads_locked is true for a passage whose spread survives neither its orphans "
    f"nor the floor: cv_less_orphans under {FLOOR_CV_FLOOR}. An orphan is a shot under "
    f"{ORPHAN_FRACTION}x the passage median with both neighbours inside the passage at or "
    "above it — a lone flash, as against a burst, which is short shots side by side and stays "
    "in the spread. The reading exists because cv alone is carried by whatever is most "
    "extreme: five long holds and one flash score a spread the holds do not have. A passage "
    "no shot starts inside is held through by one, which reads locked on its own — "
    "held_through_seconds is that shot's length, and there is no cut in the passage to take a "
    "spread over. No passage "
    "long enough to read is an empty runs list, which is not the same as passing. It is a "
    "warning to look at, not a verdict — a passage held on a picture that develops is a real "
    "edit, and this measures lengths, not what is inside the frame."
)
"""The floor heuristic in words, carried in the report beside the numbers it was drawn from."""


def read(
    rows: Sequence[Mapping[str, Any]],
    levels: Sequence[tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """The ``shot_rhythm`` block: how varied the cutting is, in the shapes a metronome shows in.

    ``rows`` are the per-cut records as the report writes them, openings included — where the
    screen time went is the question, and the shot that starts the film is screen time like any
    other. Three columns are read: ``seconds``, ``clip`` (``None`` for black), and ``t`` for the
    gearing. ``levels`` is the loudness curve as ``(window start, RMS in dBFS)`` pairs; without
    one, ``gears`` is ``None`` rather than a gearing nobody measured.

    The ramp is the same mechanism wearing a disguise. A two-camera trade whose lengths walk
    steadily from ten seconds down to three varies every one of them, so the bin and the spread
    both call it varied — and a panel called it a mechanical metronome anyway, because a ladder
    is as countable a pattern as a fixed length (P3·R3: strict two-framing, 9.9 s to 2.9 s
    without a step back up). The run of one-way lengths is what catches it.
    """
    lengths = [float(row["seconds"]) for row in rows]
    # The angle on screen is what alternation is about, so black counts as one source rather
    # than as a hole: cutting camera, black, camera, black is a pattern, not an absence.
    sources = [BLACK if row["clip"] is None else str(row["clip"]) for row in rows]
    histogram = _bins(lengths)
    alternation = _alternation(sources)
    uniformity = _uniformity(lengths, histogram)
    ramp = _ramp(lengths)
    return {
        "shots": len(rows),
        "lengths": {
            "histogram": histogram,
            # max/min over the shots as written; ``shot_seconds`` holds the two numbers it is
            # taken from. None when the shortest shot rounds to zero and the ratio would not
            # divide.
            "spread_ratio": _ratio(lengths),
            "mean": rounded(statistics.fmean(lengths)) if lengths else None,
            "median": rounded(statistics.median(lengths)) if lengths else None,
        },
        "alternation": alternation,
        "uniformity": uniformity,
        "ramp": ramp,
        "reads_metronomic": _metronomic(alternation, uniformity, ramp),
        "gears": _gears(rows, levels, uniformity),
        "heuristic": HEURISTIC,
    }


def _bins(lengths: Sequence[float]) -> dict[str, int]:
    """The corpus histogram, every bin present — a zero is a reading, not a missing key."""
    counted = dict.fromkeys((label for label, _ in RHYTHM_BINS), 0)
    for length in lengths:
        for label, edge in RHYTHM_BINS:
            if length < edge:
                counted[label] += 1
                break
    return counted


def _ratio(lengths: Sequence[float]) -> float | None:
    if not lengths or min(lengths) <= 0:
        return None
    return rounded(max(lengths) / min(lengths))


def _alternation(sources: Sequence[str]) -> dict[str, Any]:
    """The longest strict A/B run in the sequence of angles, counted in cuts.

    Strict means each shot returns to the one before last and differs from the one before it:
    A B A B. A third angle ends the run, and so does the same angle twice — both are variety,
    which is the thing being looked for. The run is measured in cuts rather than shots so its
    fraction is of the same denominator the report counts cuts in.
    """
    cuts = max(len(sources) - 1, 0)
    longest = 0
    run = 0
    for index in range(1, len(sources)):
        if sources[index] == sources[index - 1]:
            run = 0
            continue
        returns = index >= 2 and sources[index] == sources[index - 2]
        run = run + 1 if returns and run else 1
        longest = max(longest, run)
    counted = longest if longest >= ALTERNATION_MIN else 0
    return {
        "cuts": cuts,
        "longest_run": counted,
        "fraction": rounded(counted / cuts) if cuts else 0.0,
    }


def _uniformity(lengths: Sequence[float], histogram: Mapping[str, int]) -> dict[str, Any]:
    """How much of the cut is one length: the fullest bin's share, and the spread around it.

    Two readings because they miss different cuts. The bin catches shots clustered at one
    length even when the numbers wobble inside it; the coefficient of variation catches a cut
    whose lengths drift slowly across a boundary and so land in two bins while never varying.
    """
    if not lengths:
        return {"bin": None, "one_bin": None, "cv": None}
    fullest = max(RHYTHM_BINS, key=lambda one: histogram[one[0]])[0]
    return {
        "bin": fullest,
        "one_bin": rounded(histogram[fullest] / len(lengths)),
        "cv": _cv(lengths),
    }


def _ramp(lengths: Sequence[float]) -> dict[str, Any]:
    """The longest run of shots that only shorten, or only lengthen, counted in cuts.

    A tightening ladder is a pattern the bin count and the spread are both blind to: every
    length differs, so no bin holds the cut and the coefficient of variation reads as variety,
    while what the audience sees is one rule applied over and over. Direction rather than
    slope, because the shape is the *monotony*, not the rate — a ladder that steps 10, 8, 7,
    3 is as mechanical as one that halves each time, and a single step back up ends both.

    Equal neighbours end a run rather than continue one: a stretch of identical lengths is
    already what ``_uniformity`` reads, and letting it feed this one would count the same cut
    twice. Measured in cuts, like ``_alternation``, so the two fractions share a denominator.
    """
    cuts = max(len(lengths) - 1, 0)
    longest = 0
    rising = falling = 0
    for index in range(1, len(lengths)):
        rising = rising + 1 if lengths[index] > lengths[index - 1] else 0
        falling = falling + 1 if lengths[index] < lengths[index - 1] else 0
        longest = max(longest, rising, falling)
    counted = longest if longest >= RAMP_MIN else 0
    return {
        "cuts": cuts,
        "longest_run": counted,
        "fraction": rounded(counted / cuts) if cuts else 0.0,
    }


def _metronomic(
    alternation: Mapping[str, Any], uniformity: Mapping[str, Any], ramp: Mapping[str, Any]
) -> bool:
    """``HEURISTIC``, applied. A reading it cannot take is not a reading against the cut."""
    one_bin = uniformity["one_bin"]
    cv = uniformity["cv"]
    uniform = (one_bin is not None and one_bin > ONE_BIN_FLOOR) or (
        cv is not None and cv < CV_FLOOR
    )
    ramped = float(ramp["fraction"]) > RAMP_FLOOR
    return float(alternation["fraction"]) > ALTERNATION_FLOOR and (uniform or ramped)


def _gears(
    rows: Sequence[Mapping[str, Any]],
    levels: Sequence[tuple[float, float]] | None,
    uniformity: Mapping[str, Any],
) -> dict[str, Any] | None:
    """How the cutting rate changes with the music's loudness — the one-speed read.

    ``None`` when there is no level curve to read, or none of it covers the cut: not looking
    and finding no gearing are different answers, and only one of them is about the edit.

    The span the terciles are taken over is the cut's own, not the mix's. A four-minute song
    inside a two-hour concert is quiet *in that concert* from end to end, and thirds taken
    over the whole mix would drop every shot in it into one gear and report nothing.

    Where the cut runs past the curve — a tail after the analysed mix ends, a cold open ahead
    of its start — those shots are counted in ``outside_shots`` and left out of the terciles
    and out of the sub-2 s numbers with them. Every rate here divides by the seconds of music
    its tercile holds, so a shot placed where no window exists is a numerator with no
    denominator, and the one it borrows is the wrong one.
    """
    if not rows or not levels:
        return None
    span_start = min(float(row["t"]) for row in rows)
    span_end = max(float(row["t"]) + float(row["seconds"]) for row in rows)
    windows = [
        (start, level)
        for start, level in levels
        if start < span_end and start + GEAR_WINDOW_SECONDS > span_start
    ]
    if not windows:
        return None

    placed = _terciles(windows)
    starts = [start for start, _ in windows]
    held: dict[str, list[Mapping[str, Any]]] = {QUIET: [], MID: [], LOUD: []}
    outside = 0
    for row in rows:
        window = _window_at(starts, float(row["t"]))
        if window is None:
            outside += 1
            continue
        held[placed[window]].append(row)

    terciles = {
        gear: _gear(
            held[gear],
            [level for index, (_, level) in enumerate(windows) if placed[index] == gear],
        )
        for gear in (QUIET, MID, LOUD)
    }
    ratio = _rate_ratio(terciles[LOUD]["cuts_per_minute"], terciles[QUIET]["cuts_per_minute"])
    # The short shots are the ones a fast passage is made of, so where they sit is the
    # gearing read in its bluntest form: a build that saves them for the loud third has
    # changed gear even if the averages move less than the ratio floor.
    counted = [row for gear in (QUIET, MID, LOUD) for row in held[gear]]
    short = sum(1 for row in counted if float(row["seconds"]) < SUB_TWO_SECONDS)
    in_loud = sum(1 for row in held[LOUD] if float(row["seconds"]) < SUB_TWO_SECONDS)
    return {
        "window_seconds": GEAR_WINDOW_SECONDS,
        "terciles": terciles,
        "outside_shots": outside,
        "rate_ratio": ratio,
        "sub2s_count": short,
        "sub2s_in_loud": in_loud,
        "sub2s_loud_fraction": rounded(in_loud / short) if short else None,
        "one_speed": _one_speed(ratio, uniformity["cv"]),
        "quiet_floor": _quiet_floor(rows, windows),
        "heuristic": GEAR_HEURISTIC,
    }


def _quiet_floor(
    rows: Sequence[Mapping[str, Any]], windows: Sequence[tuple[float, float]]
) -> dict[str, Any]:
    """Whether the passages held in the slow gear breathe, or only hold.

    The gear ratios say a quiet section was cut slower; they say nothing about what happens
    inside it. A build can hit the quiet rate exactly and still park: five holds of much the
    same length in a row is the rate the table asked for and a passage nobody is watching by
    the end of. This is the reading of the inside — how much the lengths move, and how much of
    that movement one shot is holding up.

    The passages are found on a smoothed curve rather than on the tercile labels above,
    because those labels are per window and a live room crosses the quiet edge and back inside
    a bar. Smoothing is what makes a *passage* out of a curve; the rates keep the unsmoothed
    labels, since a rate does not care whether the music it holds was contiguous.
    """
    runs = _quiet_runs(windows)
    passages = [_passage(rows, start, end) for start, end in runs]
    return {
        "smoothing_windows": QUIET_SMOOTHING_WINDOWS,
        "runs": passages,
        "reads_locked": any(passage["reads_locked"] for passage in passages),
        "heuristic": FLOOR_HEURISTIC,
    }


def _quiet_runs(windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """The stretches of music long enough, and quiet enough, to be a passage.

    Quiet is the bottom third of the *smoothed* curve — the same split ``_terciles`` takes,
    run over a curve the outliers have been taken out of rather than the raw one, which is the
    whole difference between a passage and a window. Runs shorter than ``QUIET_FLOOR_SECONDS``
    are dropped rather than measured: they hold two or three shots, and a spread over three
    shots is a number the report cannot mean anything by.
    """
    if not windows:
        return []
    levels = _smoothed([level for _, level in windows])
    smoothed = [(start, level) for (start, _), level in zip(windows, levels, strict=True)]
    quiet = [label == QUIET for label in _terciles(smoothed)]

    runs: list[tuple[float, float]] = []
    opened: int | None = None
    for index, is_quiet in enumerate([*quiet, False]):
        if is_quiet and opened is None:
            opened = index
        elif not is_quiet and opened is not None:
            runs.append((windows[opened][0], windows[index - 1][0] + GEAR_WINDOW_SECONDS))
            opened = None
    return [(start, end) for start, end in runs if end - start >= QUIET_FLOOR_SECONDS]


def _smoothed(levels: Sequence[float]) -> list[float]:
    """A centred moving median over ``QUIET_SMOOTHING_WINDOWS`` windows.

    Median rather than mean, because the thing being smoothed out is exactly the outlier — one
    crash in a quiet passage pulls a mean over the edge and leaves the median where the music
    is. At the ends the window shrinks rather than padding: a curve that starts quiet should
    read quiet from its first window, not ease in from a value nobody measured.
    """
    half = QUIET_SMOOTHING_WINDOWS // 2
    return [
        statistics.median(levels[max(0, index - half) : min(len(levels), index + half + 1)])
        for index in range(len(levels))
    ]


def _passage(rows: Sequence[Mapping[str, Any]], start: float, end: float) -> dict[str, Any]:
    """One quiet passage: how it was cut, and whether the spread is real or carried.

    ``cv_less_orphans`` is the reading the flag is drawn on. A coefficient of variation is
    dominated by whatever is furthest from the mean, so one 2.5 s flash among five holds of
    ten to twenty seconds reports a spread that no part of the passage a viewer sits through
    actually has. Dropping the orphans and asking again is the difference between a floor that
    breathes and a floor with a hole punched in it.

    A passage with no shot starting inside it is not an absent reading, it is the stillest one
    there is: a single hold runs the whole way through and there is no cut in it to measure.
    That is reported as ``held_through_seconds`` and reads locked on its own, because a
    spread taken over the no lengths inside would otherwise let the most parked passage
    possible past the flag.
    """
    lengths = [float(row["seconds"]) for row in rows if start <= float(row["t"]) < end]
    held_through = _held_through(rows, start, end) if not lengths else None
    orphans = _orphans(lengths)
    kept = [length for index, length in enumerate(lengths) if index not in orphans]
    cv = _cv(lengths)
    less = _cv(kept)
    seconds = end - start
    return {
        "from": rounded(start),
        "to": rounded(end),
        "seconds": rounded(seconds),
        "shots": len(lengths),
        "cuts_per_minute": rounded(len(lengths) / (seconds / 60.0)) if seconds > 0 else None,
        "median_seconds": rounded(statistics.median(lengths)) if lengths else None,
        "cv": cv,
        "orphans": len(orphans),
        "orphan_seconds": [rounded(lengths[index]) for index in orphans],
        "cv_less_orphans": less,
        "held_through_seconds": held_through,
        "reads_locked": held_through is not None or (less is not None and less < FLOOR_CV_FLOOR),
    }


def _held_through(rows: Sequence[Mapping[str, Any]], start: float, end: float) -> float | None:
    """The length of the shot that runs the whole passage without a cut in it, if there is one.

    Not every passage with nothing starting inside it is held through: a cut that ends before
    the passage does — or one the level curve outruns — has no film there at all, and a
    stretch with no picture in it is not an edit anybody made. The shot has to cover the
    passage end to end for the reading to be about a decision.
    """
    for row in rows:
        opens = float(row["t"])
        if opens <= start and opens + float(row["seconds"]) >= end:
            return rounded(float(row["seconds"]))
    return None


def _orphans(lengths: Sequence[float]) -> set[int]:
    """Which shots are lone flashes: short against the passage, with nothing short beside them.

    Neighbours are taken inside the passage only, so its first and last shot are judged against
    the one neighbour they have — a passage that opens on a flash is still opening on one.

    Two short shots side by side are not orphans, either of them. That is a burst, which is a
    gesture a quiet passage is allowed to make and which the spread should keep, and the whole
    point of the distinction is that a burst is something a viewer reads as cutting while a
    single flash between long holds reads as a mistake or a stinger.
    """
    if len(lengths) < 2:
        return set()
    median = statistics.median(lengths)
    found: set[int] = set()
    for index, length in enumerate(lengths):
        if length >= ORPHAN_FRACTION * median:
            continue
        beside = [lengths[near] for near in (index - 1, index + 1) if 0 <= near < len(lengths)]
        if all(other >= median for other in beside):
            found.add(index)
    return found


def _cv(lengths: Sequence[float]) -> float | None:
    """Coefficient of variation, or ``None`` where there is nothing to take it over.

    One shot is a spread of zero rather than no reading: a passage crossed by a single hold is
    the most locked a passage can be, and answering ``None`` there would let the stillest case
    of all fall through the flag. Nothing at all — a long shot that started before the passage
    and covers it whole — is the reading that cannot be taken, because no length inside it is a
    decision made about it.
    """
    if not lengths:
        return None
    mean = statistics.fmean(lengths)
    return rounded(statistics.pstdev(lengths) / mean) if mean > 0 else None


def _terciles(windows: Sequence[tuple[float, float]]) -> list[str]:
    """Which third of the loudness each window sits in, by rank rather than by level range.

    Ranking, because the levels themselves are not a scale anything can be split evenly on:
    a mix mastered hot spends most of its windows inside four decibels, and thirds of *that*
    range would put the whole concert in one gear. Thirds of the windows always split the
    span three ways, so the rate in each is a rate over comparable amounts of music.

    Ties break by time, which matters only on a curve flat enough that the split is arbitrary
    — and there the ratio it produces lands near one, which is exactly what a passage whose
    loudness never moves should read as.
    """
    order = sorted(range(len(windows)), key=lambda index: (windows[index][1], windows[index][0]))
    lower, upper = len(order) // 3, (2 * len(order)) // 3
    placed = [MID] * len(windows)
    for rank, index in enumerate(order):
        placed[index] = QUIET if rank < lower else LOUD if rank >= upper else MID
    return placed


def _window_at(starts: Sequence[float], seconds: float) -> int | None:
    """The window a shot's first frame lands in, or ``None`` when the curve does not reach it.

    A shot is counted whole in the gear it was cut *into*: that is the decision the editor
    made at that frame. Splitting a long shot across two gears would credit the loud third
    with screen time nobody cut there.

    Past the ends the answer is nothing rather than the nearest window. The rates are cuts
    over the *music* each tercile holds, and a curve that stops before the cut does — or
    starts after it, on a cold open — holds no music where those shots sit; clamping them
    into the first or last window would add cuts to a numerator whose denominator never grew,
    and a cut running a minute past the analysed mix would read as a gear change nobody made.
    """
    index = bisect_right(starts, seconds) - 1
    if index < 0 or seconds >= starts[-1] + GEAR_WINDOW_SECONDS:
        return None
    return index


def _gear(shots: Sequence[Mapping[str, Any]], levels: Sequence[float]) -> dict[str, Any]:
    """One tercile: how much music it holds, how many shots were cut in it, and how fast.

    ``seconds`` is counted in whole windows rather than in the shots' own lengths, because
    the denominator has to be the music: a gear where one long shot runs over a minute of
    loud music has a real cutting rate, and dividing by that shot's length would report the
    rate of the shot instead.
    """
    lengths = [float(shot["seconds"]) for shot in shots]
    seconds = len(levels) * GEAR_WINDOW_SECONDS
    return {
        "seconds": rounded(seconds),
        "shots": len(shots),
        "cuts_per_minute": rounded(len(shots) / (seconds / 60.0)) if seconds > 0 else None,
        "median_seconds": rounded(statistics.median(lengths)) if lengths else None,
        "level_dbfs": rounded(statistics.median(levels)) if levels else None,
    }


def _rate_ratio(loud: Any, quiet: Any) -> float | None:
    """How much faster the loud third is cut than the quiet one.

    ``None`` when nothing was cut in the quiet third: a ratio against zero is a number the
    report cannot mean anything by, and inventing one there would read as a verdict.
    """
    if not isinstance(loud, int | float) or not isinstance(quiet, int | float) or not quiet:
        return None
    return rounded(float(loud) / float(quiet))


def _one_speed(ratio: float | None, cv: Any) -> bool:
    """``GEAR_HEURISTIC``, applied. A reading it cannot take is not a reading against the cut."""
    if ratio is None or not isinstance(cv, int | float):
        return False
    return ratio < RATE_RATIO_FLOOR and float(cv) < GEAR_CV_FLOOR
