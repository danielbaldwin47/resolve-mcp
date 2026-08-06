"""Where the front of the band changes hands, from stem energy and residual timbre.

No model says who is soloing — no separator ships a horn stem or a piano stem (#22). What
exists is four stems and the arithmetic over them, so that is what is under test: which stem
is out front over its own norm, when that changes, when the residual stem's brightness steps
(one horn out, piano in, both inside ``other``), and where those change points land once
they are snapped to the nearest downbeat.

The fixtures are curves rather than audio: the reading of a WAV is ``energy``'s business and
is tested there. What can be got wrong here is the rules.
"""

from __future__ import annotations

from resolve_mcp.analysis import solos

HOP = 1.0


def _voice(name: str, *sections: tuple[float, float]) -> solos.Voice:
    """A stem's loudness curve from ``(lufs, seconds)`` sections laid end to end."""
    lufs: list[float] = []
    for level, seconds in sections:
        lufs.extend([level] * int(round(seconds / HOP)))
    starts = tuple(round(index * HOP, 6) for index in range(len(lufs)))
    return solos.Voice(name=name, seconds=starts, lufs=tuple(lufs))


def _runs(*voices: solos.Voice, minimum_seconds: float = 4.0) -> tuple[solos.Run, ...]:
    return solos.runs(voices, margin_db=3.0, minimum_seconds=minimum_seconds)


# --- who is out front -------------------------------------------------------------------


def test_the_stem_that_lifts_over_its_own_quiet_baseline_leads() -> None:
    """Absolute level says drums — they are always loudest. What leads is what lifts.

    A stem that never varies is never taking a solo, however hot it sits in the mix; the
    horn that comes up twelve dB over its own quiet baseline is.
    """
    horn = _voice("other", (-30.0, 10.0), (-18.0, 10.0))
    drums = _voice("drums", (-12.0, 10.0), (-12.0, 10.0))

    found = _runs(horn, drums)

    assert [one.name for one in found] == ["other"]
    assert found[0].start == 10.0


def test_a_handover_is_one_run_each_side_of_it() -> None:
    vocals = _voice("vocals", (-18.0, 12.0), (-30.0, 12.0))
    other = _voice("other", (-30.0, 12.0), (-18.0, 12.0))

    found = _runs(vocals, other)

    assert [(one.name, one.start, one.end) for one in found] == [
        ("vocals", 0.0, 12.0),
        ("other", 12.0, 24.0),
    ]


def test_two_stems_within_the_margin_do_not_hand_over() -> None:
    """Head in, horns and piano together. Neither is soloing, and nothing changed."""
    other = _voice("other", (-20.0, 10.0), (-19.0, 10.0))
    vocals = _voice("vocals", (-20.0, 10.0), (-20.0, 10.0))

    found = _runs(other, vocals, minimum_seconds=4.0)

    assert solos.changes(found, ()) == ()


def test_a_blip_shorter_than_the_minimum_is_not_a_solo() -> None:
    """Four bars of a horn stab inside a piano solo is not the horn taking the tune."""
    piano = _voice("other", (-18.0, 20.0), (-30.0, 3.0), (-18.0, 20.0))
    horn = _voice("vocals", (-30.0, 20.0), (-18.0, 3.0), (-30.0, 20.0))

    found = _runs(piano, horn, minimum_seconds=8.0)

    assert [one.name for one in found] == ["other"]
    assert found[0].end == 43.0


def test_stems_of_unequal_length_measure_over_what_they_share() -> None:
    """A stem a window short must not shorten the answer or raise."""
    long = _voice("other", (-18.0, 12.0), (-30.0, 12.0))
    entering = _voice("vocals", (-30.0, 12.0), (-18.0, 12.0))
    short = solos.Voice(entering.name, entering.seconds[:-2], entering.lufs[:-2])

    found = _runs(long, short)

    assert [one.name for one in found] == ["other", "vocals"]
    assert found[-1].end == 22.0


# --- the residual stem's timbre ---------------------------------------------------------


def test_a_step_in_brightness_is_a_change_inside_the_residual_stem() -> None:
    """Tenor out, piano in: same stem, same energy, a different instrument."""
    seconds = tuple(float(index) for index in range(40))
    hertz = tuple(600.0 if one < 20 else 1_500.0 for one in range(40))

    found = solos.steps(seconds, hertz, semitones=4.0, minimum_seconds=8.0)

    assert len(found) == 1
    assert found[0].seconds == 20.0
    assert found[0].after > found[0].before


def test_brightness_drifting_slowly_is_not_a_change() -> None:
    seconds = tuple(float(index) for index in range(40))
    hertz = tuple(600.0 + 4.0 * index for index in range(40))

    assert solos.steps(seconds, hertz, semitones=4.0, minimum_seconds=8.0) == ()


def test_one_step_is_reported_once_rather_than_at_every_window_near_it() -> None:
    seconds = tuple(float(index) for index in range(60))
    hertz = tuple(600.0 if one < 30 else 1_800.0 for one in range(60))

    assert len(solos.steps(seconds, hertz, semitones=4.0, minimum_seconds=8.0)) == 1


# --- change points, snapped to downbeats ------------------------------------------------


def test_a_change_point_snaps_to_the_nearest_downbeat() -> None:
    changes = solos.changes(_runs(_voice("other", (-18.0, 12.0), (-30.0, 12.0)),
                                  _voice("vocals", (-30.0, 12.0), (-18.0, 12.0))), ())

    snapped = solos.snapped(changes, downbeats=(9.6, 11.6, 13.6), tolerance=2.0)

    assert snapped[0].measured == 12.0
    assert snapped[0].seconds == 11.6
    assert snapped[0].downbeat is True


def test_a_change_point_with_no_downbeat_near_it_keeps_its_measured_time() -> None:
    """A snap that reaches half a chorus away is a worse answer than not snapping."""
    changes = solos.changes(_runs(_voice("other", (-18.0, 12.0), (-30.0, 12.0)),
                                  _voice("vocals", (-30.0, 12.0), (-18.0, 12.0))), ())

    snapped = solos.snapped(changes, downbeats=(0.0, 40.0), tolerance=2.0)

    assert snapped[0].seconds == 12.0
    assert snapped[0].downbeat is False


def test_with_no_grid_at_all_nothing_snaps() -> None:
    changes = solos.changes(_runs(_voice("other", (-18.0, 12.0), (-30.0, 12.0)),
                                  _voice("vocals", (-30.0, 12.0), (-18.0, 12.0))), ())

    snapped = solos.snapped(changes, downbeats=(), tolerance=2.0)

    assert snapped[0].seconds == 12.0
    assert snapped[0].downbeat is False


def test_a_timbre_step_at_the_same_moment_as_a_handover_is_one_change() -> None:
    """The horn leaving ``other`` and the vocal taking the front is one event, reported once."""
    runs = _runs(
        _voice("other", (-18.0, 12.0), (-30.0, 12.0)),
        _voice("vocals", (-30.0, 12.0), (-18.0, 12.0)),
    )
    seconds = tuple(float(index) for index in range(24))
    hertz = tuple(600.0 if one < 12 else 1_800.0 for one in range(24))

    found = solos.changes(runs, solos.steps(seconds, hertz, semitones=4.0, minimum_seconds=6.0))

    assert [one.signal for one in found] == [solos.LEAD]


def test_two_timbre_steps_close_together_are_both_reported() -> None:
    """A timbre step is suppressed by a lead change, never by another timbre step.

    ``steps`` has already decided how close two handovers inside the residual may be; a
    second rule here would silently drop the piano coming in after the tenor went out.
    """
    stepped = (solos.Step(20.0, 600.0, 1500.0), solos.Step(22.0, 1500.0, 600.0))

    found = solos.changes((), stepped, together_seconds=4.0)

    assert [one.seconds for one in found] == [20.0, 22.0]


def test_change_records_name_what_left_and_what_took_over() -> None:
    runs = _runs(
        _voice("other", (-18.0, 12.0), (-30.0, 12.0)),
        _voice("vocals", (-30.0, 12.0), (-18.0, 12.0)),
    )
    rows = solos.numbered(solos.snapped(solos.changes(runs, ()), (11.6,), tolerance=2.0))

    assert rows[0]["change"] == 1
    assert rows[0]["from"] == "other"
    assert rows[0]["to"] == "vocals"
    assert rows[0]["signal"] == solos.LEAD
    assert rows[0]["t"] == 11.6
    assert rows[0]["measured_t"] == 12.0
    assert rows[0]["downbeat"] is True


def test_the_gist_says_how_long_each_stem_led_and_how_many_changes_snapped() -> None:
    runs = _runs(
        _voice("other", (-18.0, 12.0), (-30.0, 12.0)),
        _voice("vocals", (-30.0, 12.0), (-18.0, 12.0)),
    )
    changes = solos.snapped(solos.changes(runs, ()), (11.6,), tolerance=2.0)

    summary = solos.gist(runs, changes)

    assert summary["count"] == 1
    assert summary["snapped"] == 1
    assert summary["longest_lead"]["stem"] in {"other", "vocals"}
    assert summary["longest_lead"]["seconds"] == 12.0
    assert not any(isinstance(value, list) for value in summary.values())
