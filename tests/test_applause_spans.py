"""Applause probability to tune boundaries: the arithmetic between the model and the file.

PANNs is behind a tagger (ADR 0002) and what it hears is its own business. What is checked
here is everything after it: which runs of high probability are a burst of applause rather
than a snare roll, which gaps between bursts are a tune rather than stage banter, and what
the boundary times are. A jazz set is the case that matters — a tune ends, the room claps,
the next one starts — so the fixtures are shaped like one.
"""

from __future__ import annotations

from resolve_mcp.analysis import applause

STEP = 0.5
"""Seconds per frame in these fixtures. The real tagger's frames are shorter; nothing here
depends on that beyond the arithmetic of turning frame indices back into seconds."""


def _curve(*probability: float) -> applause.Curve:
    seconds = tuple(round(index * STEP, 6) for index in range(len(probability)))
    return applause.Curve(seconds=seconds, probability=tuple(probability))


def _concert(*sections: tuple[float, float]) -> applause.Curve:
    """A curve built from ``(probability, seconds)`` sections laid end to end."""
    probability: list[float] = []
    for level, seconds in sections:
        probability.extend([level] * int(round(seconds / STEP)))
    return _curve(*probability)


def _spans(
    curve: applause.Curve,
    threshold: float = 0.5,
    minimum_seconds: float = 1.0,
    gap_seconds: float = 1.0,
) -> tuple[applause.Span, ...]:
    return applause.spans(curve, threshold, minimum_seconds, gap_seconds)


# --- which runs of probability are applause -------------------------------------------


def test_a_run_above_the_threshold_is_a_span() -> None:
    found = _spans(_concert((0.0, 4.0), (0.9, 3.0), (0.0, 4.0)))

    assert len(found) == 1
    assert found[0].start == 4.0
    assert found[0].end == 7.0
    assert found[0].seconds == 3.0
    assert found[0].peak == 0.9


def test_a_run_shorter_than_the_minimum_is_not_applause() -> None:
    """A hand-clap in the tune, or a cymbal the model likes the look of, is not a boundary."""
    found = _spans(_concert((0.0, 4.0), (0.9, 0.5), (0.0, 4.0)), minimum_seconds=2.0)

    assert found == ()


def test_a_dip_inside_one_burst_does_not_split_it() -> None:
    """Applause has holes in it. Two spans a beat apart are one burst, not two boundaries."""
    found = _spans(
        _concert((0.0, 4.0), (0.9, 2.0), (0.1, 0.5), (0.8, 2.0), (0.0, 4.0)),
        gap_seconds=1.0,
    )

    assert len(found) == 1
    assert found[0].start == 4.0
    assert found[0].end == 8.5


def test_two_bursts_further_apart_than_the_gap_stay_two() -> None:
    found = _spans(_concert((0.0, 2.0), (0.9, 2.0), (0.0, 3.0), (0.9, 2.0)), gap_seconds=1.0)

    assert [(one.start, one.end) for one in found] == [(2.0, 4.0), (7.0, 9.0)]


def test_applause_running_to_the_end_of_the_file_closes_at_the_end() -> None:
    found = _spans(_concert((0.0, 2.0), (0.9, 2.0)))

    assert found[-1].end == 4.0


def test_an_empty_curve_finds_nothing() -> None:
    assert applause.spans(applause.Curve((), ())) == ()


# --- which gaps between applause are tunes ---------------------------------------------


def test_the_music_between_two_bursts_is_a_tune() -> None:
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 30.0), (0.9, 4.0))
    found = applause.tunes(_spans(curve), duration_seconds=68.0, minimum_seconds=10.0)

    assert [(one.start, one.end) for one in found] == [(0.0, 30.0), (34.0, 64.0)]
    assert found[0].applause_before is None
    assert found[0].applause_after == 4.0
    assert found[1].applause_before == 4.0


def test_the_last_tune_runs_to_the_end_of_the_file() -> None:
    curve = _concert((0.0, 20.0), (0.9, 4.0), (0.0, 20.0))
    found = applause.tunes(_spans(curve), duration_seconds=44.0, minimum_seconds=10.0)

    assert found[-1].end == 44.0
    assert found[-1].applause_after is None


def test_stage_banter_between_two_bursts_is_not_a_tune() -> None:
    """Announcing the band takes twenty seconds. A tune does not."""
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 8.0), (0.9, 4.0), (0.0, 30.0))
    found = applause.tunes(_spans(curve), duration_seconds=76.0, minimum_seconds=20.0)

    assert [(one.start, one.end) for one in found] == [(0.0, 30.0), (46.0, 76.0)]


def test_a_concert_with_no_applause_at_all_is_one_tune() -> None:
    found = applause.tunes((), duration_seconds=300.0, minimum_seconds=20.0)

    assert [(one.start, one.end) for one in found] == [(0.0, 300.0)]
    assert found[0].applause_before is None and found[0].applause_after is None


# --- which calls have a pulse under them --------------------------------------------------


def _called(*bounds: tuple[float, float]) -> tuple[applause.Tune, ...]:
    """Tunes at the given boundaries, as ``tunes`` would hand them over — no density yet."""
    return tuple(applause.Tune(start, end, None, None) for start, end in bounds)


def _beats(start: float, end: float, per_second: float) -> tuple[float, ...]:
    """A steady grid over ``[start, end)`` at the given density."""
    step = 1.0 / per_second
    return tuple(round(start + index * step, 6) for index in range(int((end - start) * per_second)))


def test_the_density_of_a_call_is_the_beats_inside_it_over_its_length() -> None:
    found = applause.counted(_called((0.0, 10.0)), _beats(0.0, 10.0, 2.0))

    assert found[0].beats == 20
    assert found[0].beats_per_second == 2.0


def test_a_beat_on_the_closing_boundary_belongs_to_the_next_call() -> None:
    """The spans are half-open, so a downbeat landing on the boundary is counted once."""
    found = applause.counted(_called((0.0, 4.0), (4.0, 8.0)), (0.0, 2.0, 4.0, 6.0))

    assert [one.beats for one in found] == [2, 2]


def test_counting_leaves_the_boundaries_and_the_applause_alone() -> None:
    found = applause.counted((applause.Tune(3.0, 9.0, 2.0, 1.5),), (4.0, 5.0, 6.0))

    assert (found[0].start, found[0].end) == (3.0, 9.0)
    assert (found[0].applause_before, found[0].applause_after) == (2.0, 1.5)


def test_a_call_with_no_beats_under_it_has_a_density_of_zero() -> None:
    """Zero, not unmeasured: the grid was read and it found nothing, which is the finding."""
    found = applause.counted(_called((0.0, 100.0)), ())

    assert found[0].beats == 0
    assert found[0].beats_per_second == 0.0


def test_a_call_with_no_pulse_is_dropped_and_the_playing_one_is_kept() -> None:
    """Announcements and talking are bounded by applause and read as tunes until now."""
    counted = applause.counted(
        _called((0.0, 100.0), (110.0, 210.0)),
        _beats(0.0, 100.0, 1.8),
    )

    called = applause.sifted(counted, minimum_density=0.5)

    assert [(one.start, one.end) for one in called.kept] == [(0.0, 100.0)]
    assert [(one.start, one.end) for one in called.dropped] == [(110.0, 210.0)]


def test_a_call_sitting_exactly_on_the_floor_is_kept() -> None:
    counted = applause.counted(_called((0.0, 100.0)), _beats(0.0, 100.0, 0.5))

    assert len(applause.sifted(counted, minimum_density=0.5).kept) == 1


def test_a_floor_of_zero_keeps_every_call() -> None:
    """The escape hatch: nothing is dropped, so a caller can see the unfiltered set."""
    counted = applause.counted(_called((0.0, 100.0), (110.0, 210.0)), ())

    called = applause.sifted(counted, minimum_density=0.0)

    assert len(called.kept) == 2
    assert called.dropped == ()


def test_an_unmeasured_call_is_never_dropped() -> None:
    """No grid was read, so there is no evidence to drop it on."""
    called = applause.sifted(_called((0.0, 100.0)), minimum_density=0.5)

    assert len(called.kept) == 1
    assert called.dropped == ()


# --- what is written and what comes back ------------------------------------------------


def test_every_tune_is_one_record_with_its_own_number() -> None:
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 30.0))
    rows = applause.numbered(applause.tunes(_spans(curve), 64.0, minimum_seconds=10.0))

    assert [row["tune"] for row in rows] == [1, 2]
    assert set(rows[0]) == {
        "tune",
        "t",
        "end",
        "seconds",
        "applause_before",
        "applause_after",
        "beats",
        "beats_per_second",
    }
    assert rows[0]["t"] == 0.0
    assert rows[1]["applause_before"] == 4.0


def test_a_record_carries_the_density_it_was_kept_on() -> None:
    """What the filter measured, on the row, so a songs.json author can see the evidence."""
    rows = applause.numbered(applause.counted(_called((0.0, 10.0)), _beats(0.0, 10.0, 2.0)))

    assert rows[0]["beats"] == 20
    assert rows[0]["beats_per_second"] == 2.0


def test_a_record_written_without_a_grid_says_so_rather_than_claiming_zero() -> None:
    rows = applause.numbered(_called((0.0, 10.0)))

    assert rows[0]["beats"] is None
    assert rows[0]["beats_per_second"] is None


def test_the_gist_counts_the_tunes_and_the_applause_without_listing_them() -> None:
    """It rides back in a tool result, so it is stats — the boundaries themselves are on disk."""
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 20.0))
    found = _spans(curve)
    tunes = applause.tunes(found, 54.0, minimum_seconds=10.0)

    summary = applause.gist(curve, found, applause.Calls(tunes, ()))

    assert summary["count"] == 2
    assert summary["applause_count"] == 1
    assert summary["applause_seconds"] == 4.0
    assert summary["longest"]["seconds"] == 30.0
    assert summary["shortest"]["seconds"] == 20.0
    assert summary["peak_probability"] == 0.9
    assert not any(isinstance(value, list) for value in summary.values())


def test_the_gist_says_how_many_calls_had_no_pulse_and_how_close_the_floor_came() -> None:
    """Not the dropped calls themselves — the two shoulders, which is what says the floor held."""
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 30.0))
    spans = _spans(curve)
    counted = applause.counted(
        applause.tunes(spans, 64.0, minimum_seconds=10.0),
        _beats(0.0, 30.0, 1.8),
    )
    calls = applause.sifted(counted, minimum_density=0.5)

    summary = applause.gist(curve, spans, calls)

    assert summary["count"] == 1
    assert summary["dropped"] == 1
    assert summary["dropped_seconds"] == 30.0
    assert summary["densest_dropped"]["beats_per_second"] == 0.0
    assert summary["sparsest_kept"]["beats_per_second"] == 1.8
    assert not any(isinstance(value, list) for value in summary.values())


def test_the_gist_of_a_set_that_lost_nothing_has_no_dropped_shoulder() -> None:
    curve = _concert((0.0, 30.0), (0.9, 4.0), (0.0, 20.0))
    spans = _spans(curve)
    tunes = applause.tunes(spans, 54.0, minimum_seconds=10.0)

    summary = applause.gist(curve, spans, applause.Calls(tunes, ()))

    assert summary["dropped"] == 0
    assert summary["dropped_seconds"] == 0.0
    assert summary["densest_dropped"] is None


def test_a_dropped_call_is_recorded_with_the_measurement_that_dropped_it() -> None:
    """A filter whose rejects cannot be inspected is one nobody can check (#38)."""
    counted = applause.counted(_called((0.0, 100.0), (110.0, 210.0)), _beats(0.0, 100.0, 1.8))
    calls = applause.sifted(counted, minimum_density=0.5)

    rows = applause.dropped_calls(calls.dropped, 0.5)

    assert [(row["t"], row["end"]) for row in rows] == [(110.0, 210.0)]
    assert rows[0]["beats"] == 0
    assert rows[0]["beats_per_second"] == 0.0
    assert "0.5" in rows[0]["reason"]
