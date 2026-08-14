"""Which player a shot is framed on, crossed with who is out front.

Pure arithmetic over two documents the agent already owns — the angle sidecar's labels and
the solo changes another job wrote — so every case here is a fake-tier one. The interesting
failures are all about the join: a shot that outlives the solo it opened in, a subject the
solo map never names, and the difference between "nobody was measured" and "nobody was out
front".
"""

from __future__ import annotations

from typing import Any

import pytest

from resolve_mcp.analysis import subject as subject_module
from resolve_mcp.analysis.subject import Subject

SOLOS: tuple[dict[str, Any], ...] = (
    {"change": 1, "t": 0.0, "from": "bass", "to": "drums"},
    {"change": 2, "t": 10.0, "from": "drums", "to": "wind"},
)
"""Bass out front until the top, drums to 10 s, wind after — the shape solos.py writes."""


def _windows() -> tuple[subject_module.Window, ...]:
    return subject_module.windows(SOLOS)


# --- reading the sidecar ----------------------------------------------------------------------


def test_a_subject_is_read_off_the_entry_that_names_one() -> None:
    assert subject_module.subject_of({"role": "drums-tight", "subject": "drums"}) == Subject(
        "drums", "drums"
    )


def test_a_role_in_the_corpus_form_yields_its_subject() -> None:
    assert subject_module.subject_of({"role": "ensemble-wide"}) == Subject("ensemble", "ensemble")


def test_a_bare_role_names_a_character_and_yields_no_subject() -> None:
    """``wide`` says how the camera frames, not what it is framed on — inventing one would lie."""
    assert subject_module.subject_of("wide") is None
    assert subject_module.subject_of({"role": "wide"}) is None


def test_a_bare_role_in_the_corpus_form_still_yields_its_subject() -> None:
    assert subject_module.subject_of("drums-tight") == Subject("drums", "drums")


def test_a_voice_key_is_what_the_solo_map_calls_this_subject() -> None:
    """The sidecar names the player; the solo map names stems. The escape hatch joins them."""
    entry = {"role": "sax-tight", "subject": "mike", "voice": "wind"}
    assert subject_module.subject_of(entry) == Subject("mike", "wind")


def test_an_entry_with_neither_subject_nor_usable_role_is_dropped() -> None:
    assert subject_module.subject_of({"note": "the room"}) is None
    assert subject_module.subject_of(None) is None


# --- what kind of subject it is ---------------------------------------------------------------


def test_the_ensemble_is_its_own_kind() -> None:
    assert subject_module.kind(Subject("ensemble", "ensemble"), frozenset()) == "ensemble"


def test_a_camera_on_whoever_is_out_front_is_a_player() -> None:
    """One player at a time — which one is the solo map's answer, not the sidecar's."""
    assert subject_module.kind(Subject("soloist", "soloist"), frozenset()) == "player"


def test_a_subject_the_solo_map_names_is_a_player() -> None:
    assert subject_module.kind(Subject("drums", "drums"), subject_module.voices(SOLOS)) == "player"


def test_a_subject_the_solo_map_never_names_reads_as_other() -> None:
    """An audience camera is neither a player nor the band, and counting it as one would skew."""
    assert subject_module.kind(Subject("audience", "audience"), subject_module.voices(SOLOS)) == (
        "other"
    )


def test_without_a_solo_map_there_is_no_roster_to_contradict_a_subject() -> None:
    assert subject_module.kind(Subject("piano", "piano"), frozenset()) == "player"


def test_no_subject_is_no_kind() -> None:
    assert subject_module.kind(None, subject_module.voices(SOLOS)) is None


# --- the solo windows -------------------------------------------------------------------------


def test_the_front_before_the_first_change_is_who_it_was_taken_over_from() -> None:
    first = _windows()[0]
    assert first.front == "bass"
    assert first.end == 0.0


def test_each_change_opens_a_window_that_runs_to_the_next_one() -> None:
    middle = _windows()[1]
    assert (middle.start, middle.end, middle.front) == (0.0, 10.0, "drums")


def test_the_last_change_holds_to_the_end_of_the_concert() -> None:
    last = _windows()[-1]
    assert last.front == "wind"
    assert last.end == float("inf")


def test_a_first_change_that_took_over_from_nobody_leaves_the_front_unknown() -> None:
    rows = [{"change": 1, "t": 4.0, "from": None, "to": "drums"}]
    assert subject_module.windows(rows)[0].front is None


def test_no_solo_map_is_no_windows() -> None:
    assert subject_module.windows(None) == ()
    assert subject_module.windows(()) == ()


# --- the join ---------------------------------------------------------------------------------


def _read(name: str | None, start: float, end: float, **rest: Any) -> dict[str, Any]:
    """One shot, named by its subject — the roster is the fixture solo map's own."""
    subject = None if name is None else Subject(name, name)
    spans = rest.pop("spans", None)
    return subject_module.reading(
        subject,
        subject_module.voices(SOLOS),
        start,
        end,
        _windows() if spans is None else spans,
        **rest,
    )


def test_a_shot_on_the_player_out_front_is_on_the_soloist() -> None:
    reading = _read("drums", 2.0, 6.0)
    assert reading["subject_kind"] == "player"
    assert reading["on_soloist"] is True
    assert reading["on_soloist_seconds"] == {"soloist": 4.0}


def test_a_shot_on_a_player_who_is_not_soloing_is_on_a_non_soloing_player() -> None:
    reading = _read("drums", 12.0, 15.0)
    assert reading["on_soloist"] is False
    assert reading["on_soloist_seconds"] == {"other_player": 3.0}


def test_a_shot_on_the_ensemble_is_counted_apart_from_both() -> None:
    reading = _read("ensemble", 2.0, 6.0)
    assert reading["subject_kind"] == "ensemble"
    assert reading["on_soloist"] is False
    assert reading["on_soloist_seconds"] == {"ensemble": 4.0}


def test_a_camera_that_follows_the_front_is_on_the_soloist_whoever_that_is() -> None:
    assert _read("soloist", 12.0, 15.0)["on_soloist"] is True


def test_the_two_ways_onto_the_soloist_line_are_told_apart() -> None:
    """One was joined against the solo map; the other is the sidecar's word about a camera."""
    assert _read("drums", 2.0, 6.0)["on_soloist_by"] == "front_match"
    assert _read("soloist", 12.0, 15.0)["on_soloist_by"] == "follow_camera"
    assert _read("ensemble", 2.0, 6.0)["on_soloist_by"] is None


def test_a_shot_that_outlives_its_solo_is_split_across_the_change() -> None:
    """The whole point of measuring seconds rather than reading the front at the cut."""
    reading = _read("drums", 8.0, 14.0)
    assert reading["on_soloist_seconds"] == {"soloist": 2.0, "other_player": 4.0}
    assert reading["on_soloist"] is False


def test_a_shot_split_evenly_takes_the_verdict_it_opened_on() -> None:
    reading = _read("drums", 8.0, 12.0)
    assert reading["on_soloist_seconds"] == {"soloist": 2.0, "other_player": 2.0}
    assert reading["on_soloist"] is True


def test_a_shot_with_no_subject_label_is_screen_time_nobody_can_attribute() -> None:
    reading = _read(None, 2.0, 6.0)
    assert reading["subject_kind"] is None
    assert reading["on_soloist"] is None
    assert reading["on_soloist_seconds"] == {"unlabelled": 4.0}


def test_a_subject_that_is_neither_player_nor_band_is_counted_as_neither() -> None:
    """An audience camera on the ``other_player`` line would read as watching the wrong
    musician, which is a different fact about a cut than watching the room."""
    reading = _read("audience", 2.0, 6.0)
    assert reading["subject_kind"] == "other"
    assert reading["on_soloist"] is False
    assert reading["on_soloist_seconds"] == {"elsewhere": 4.0}


def test_a_stretch_with_nobody_measured_out_front_is_left_out_of_the_seconds() -> None:
    """A window whose front is unknown is not solo-window screen time — it is no measurement."""
    rows = [{"change": 1, "t": 4.0, "from": None, "to": "drums"}]
    reading = _read("drums", 0.0, 6.0, spans=subject_module.windows(rows))
    assert reading["on_soloist_seconds"] == {"soloist": 2.0}


def test_a_stretch_nothing_covers_is_counted_as_black_rather_than_as_unlabelled() -> None:
    """How much of a cut is empty is a fact about the edit, not about the sidecar."""
    reading = _read(None, 2.0, 6.0, black=True)
    assert reading["on_soloist"] is None
    assert reading["on_soloist_seconds"] == {"black": 4.0}


def test_a_shot_measured_against_no_solo_map_at_all_says_nothing() -> None:
    reading = _read("drums", 0.0, 6.0, spans=())
    assert reading["on_soloist"] is None
    assert reading["on_soloist_seconds"] is None


def test_a_shot_of_no_length_is_no_measurement() -> None:
    assert _read("drums", 4.0, 4.0)["on_soloist_seconds"] is None


# --- the reading a critic quotes ---------------------------------------------------------------


def _rows() -> list[dict[str, Any]]:
    return [
        {"subject": name, **_read(name, start, end)}
        for name, start, end in (
            ("drums", 0.0, 6.0),
            ("ensemble", 6.0, 10.0),
            ("drums", 10.0, 12.0),
            (None, 12.0, 14.0),
        )
    ]


def test_the_summary_says_what_share_of_the_measured_screen_time_is_on_the_soloist() -> None:
    found = subject_module.summary(_rows())
    assert found is not None
    assert found["seconds"] == {"soloist": 6.0, "ensemble": 4.0, "other_player": 2.0}
    assert found["fraction_on_soloist"] == 0.5
    assert found["fraction_on_ensemble"] == pytest.approx(0.333)
    assert found["fraction_on_other_player"] == pytest.approx(0.167)


def test_the_summary_counts_the_screen_time_no_label_reaches_apart() -> None:
    """In the denominator it would read as a cut that ignores the soloist; hidden, as none."""
    found = subject_module.summary(_rows())
    assert found is not None
    assert found["unlabelled_seconds"] == 2.0
    assert found["labelled_seconds"] == 12.0
    assert found["solo_window_seconds"] == 14.0


def test_the_summary_counts_black_apart_from_both() -> None:
    black = _read(None, 14.0, 15.0, black=True)
    found = subject_module.summary([*_rows(), {"subject": None, **black}])
    assert found is not None
    assert found["black_seconds"] == 1.0
    assert found["unlabelled_seconds"] == 2.0
    assert found["labelled_seconds"] == 12.0
    assert found["shots"]["black"] == 1


def test_the_summary_counts_shots_as_well_as_seconds() -> None:
    found = subject_module.summary(_rows())
    assert found is not None
    assert found["shots"] == {"soloist": 1, "ensemble": 1, "other_player": 1, "unlabelled": 1}


def test_the_summary_says_how_much_of_the_soloist_line_nobody_measured() -> None:
    """A rig whose camera follows the front can post a high share without a join ever running."""
    rows = [*_rows(), {"subject": "soloist", **_read("soloist", 14.0, 18.0)}]

    found = subject_module.summary(rows)

    assert found is not None
    assert found["seconds"]["soloist"] == 10.0
    assert found["soloist_seconds_by_follow_camera"] == 4.0

    joined = subject_module.summary(_rows())
    assert joined is not None
    assert joined["soloist_seconds_by_follow_camera"] == 0.0


def test_nothing_measured_is_no_reading_rather_than_a_reading_of_zero() -> None:
    assert subject_module.summary([{"subject": None, "on_soloist_seconds": None}]) is None
    assert subject_module.summary([]) is None
