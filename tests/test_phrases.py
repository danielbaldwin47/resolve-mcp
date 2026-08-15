"""Phrase boundaries: the rule layer, the reading seam, the job, the cache (#143).

The rule layer is where the decisions are, so most of this file is plain functions over notes
and a beat grid — no audio, no job, no threads. Notes are written out by hand here rather than
read off a stem, because "a phrase ended at 4.0" has to be a fact about the input before it
can be a fact about the output; the reader that turns audio into notes is checked in
``test_melody.py``, and the job on top of both is checked here because that is where the
caching lives.

One seam is deliberately *not* here: whether these boundaries are the ones a director would
name. That needs the #46 tune and its annotated cut points, and it is an evaluation on real
media, not a unit test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import beats as beats_module
from resolve_mcp.analysis import melody, music, phrases
from resolve_mcp.analysis.beats import BeatGrid, Detector
from resolve_mcp.analysis.melody import Note
from resolve_mcp.audio.stems import DRUM_PASS, DRUM_STEMS, FOUR_STEMS, MIX_PASS
from resolve_mcp.config import get_config
from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.jobs.runner import JobOutput, wait_for
from resolve_mcp.jobs.store import JobRecord
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_clicks, write_hits, write_tones

TEMPO = 120.0
BEAT = 60.0 / TEMPO
"""Half a second. Every time in this file is a whole number of these unless it says otherwise."""


@pytest.fixture
def master(tmp_path: Path) -> Path:
    """The mix the stems were separated from — what the beat grid is read off."""
    return write_clicks(tmp_path / "media" / "concert.wav", beats_per_minute=TEMPO, seconds=16.0)


@pytest.fixture
def separation(tmp_path: Path) -> Path:
    """A separation directory as ``separate_stems`` leaves it: two passes, one parent."""
    directory = tmp_path / "stems" / "concert-abc123def456"
    for label in FOUR_STEMS:
        write_hits(directory / MIX_PASS / f"concert_({label.title()})_model.wav", times=(1.0,))
    for label in DRUM_STEMS:
        write_hits(directory / DRUM_PASS / f"concert_({label.title()})_model.wav", times=())
    return directory


@pytest.fixture
def stems(separation: Path) -> dict[str, Path]:
    """The four-stem mapping, the shape the separation job reports alongside the directory."""
    return {
        label: separation / MIX_PASS / f"concert_({label.title()})_model.wav"
        for label in FOUR_STEMS
    }


# --- a grid and a line to hang the rules on -----------------------------------------


def _grid(bars: int = 8, tempo: float = TEMPO, meter: int = 4) -> tuple[dict[str, Any], ...]:
    """``beats.rows`` for a steady grid — the same shape the beats half writes."""
    step = 60.0 / tempo
    beats = tuple(round(index * step, 6) for index in range(bars * meter))
    downbeats = tuple(beats[index] for index in range(0, len(beats), meter))
    return beats_module.rows(BeatGrid(beats=beats, downbeats=downbeats))


def _line(
    starts: Sequence[float],
    held: float = 0.4,
    hz: float = 440.0,
) -> list[Note]:
    """Even notes of one length at one pitch — the line to depart from."""
    return [Note(seconds=one, end=one + held, hz=hz, strength=0.5) for one in starts]


def _running(count: int, first: float = 0.0, held: float = 0.4) -> list[Note]:
    """``count`` notes a beat apart, none of them ending anything."""
    return _line([first + index * BEAT for index in range(count)], held=held)


# --- the rule layer: what nominates a boundary --------------------------------------


def test_a_line_that_never_stops_has_no_phrase_boundary_but_its_last_note() -> None:
    """Notes end-to-end at one pitch and one length: the only ending is the player stopping."""
    detection = phrases.boundaries(_running(16), _grid())

    assert detection.considered == 1
    assert len(detection.boundaries) == 1
    assert detection.boundaries[0].measured == pytest.approx(15 * BEAT + 0.4)


def test_a_rest_of_a_beat_ends_a_phrase() -> None:
    played = _running(8) + _running(8, first=8 * BEAT + BEAT)

    detection = phrases.boundaries(played, _grid())

    assert [one.measured for one in detection.boundaries][0] == pytest.approx(7 * BEAT + 0.4)


def test_a_gap_shorter_than_the_nomination_floor_is_not_an_ending() -> None:
    """Notes are not butt-joined in real playing, and every tongued note must not be a phrase."""
    small = phrases.REST_BEATS * BEAT / 2.0
    played = _running(8) + _running(8, first=8 * BEAT + small)

    detection = phrases.boundaries(played, _grid())

    assert detection.considered == 1


def test_a_held_note_ends_a_phrase_at_the_default_floor_with_no_rest_at_all() -> None:
    """The player leans on the last note and the next phrase comes straight in over the top.

    At the **default** floor, deliberately: the three cues stand in for each other, so a cue
    that only survives with the floor turned off is a cue this detector does not really have.
    """
    played = _running(6)
    played[5] = played[5]._replace(end=played[5].seconds + 0.4 * phrases.LONG_MULTIPLE)
    played += _running(6, first=played[5].end)

    detection = phrases.boundaries(played, _grid())

    assert any(one.measured == pytest.approx(played[5].end) for one in detection.boundaries)


def _leaping(semitones: float) -> list[Note]:
    """Six notes, then six more a given distance away, with no rest and no held note between."""
    return _running(6) + _line(
        [6 * BEAT + index * BEAT for index in range(6)],
        hz=440.0 * 2 ** (semitones / 12.0),
    )


def test_a_leap_of_a_fifth_ends_a_phrase_at_the_default_floor_on_its_own() -> None:
    """The contour reset with no rest and no held note: the new phrase starts somewhere else."""
    detection = phrases.boundaries(_leaping(phrases.RESET_SEMITONES), _grid())

    assert any(one.measured == pytest.approx(5 * BEAT + 0.4) for one in detection.boundaries)


def test_a_bare_fifth_reads_weaker_than_an_octave() -> None:
    """The cue nominates at a fifth and only saturates at an octave, so it stays filterable."""
    fifth = phrases.boundaries(_leaping(phrases.RESET_SEMITONES), _grid(), 0.0).boundaries[0]
    octave = phrases.boundaries(
        _leaping(phrases.RESET_FULL_SEMITONES), _grid(), 0.0
    ).boundaries[0]

    assert fifth.factors["contour"] < 1.0
    assert octave.factors["contour"] == 1.0
    assert fifth.confidence < octave.confidence


@pytest.mark.parametrize("cue", phrases.CUES)
def test_one_saturated_cue_clears_the_floor_however_weak_the_placement(cue: str) -> None:
    """Stated directly, because a weighted sum here is what made two of the three cues dead.

    ``grid`` is pinned at zero — weaker than any real placement, since the grid never scores
    below ``beats.MID_BAR`` — so this is the cue carrying the reading with no help at all.
    """
    alone = {name: (1.0 if name == cue else 0.0) for name in phrases.CUES} | {"grid": 0.0}

    assert phrases.scored(alone) > phrases.DEFAULT_MINIMUM_CONFIDENCE


def test_cues_that_agree_read_stronger_than_one_cue_on_its_own() -> None:
    lonely = {"rest": 1.0, "held": 0.0, "contour": 0.0, "grid": 0.0}
    corroborated = {"rest": 1.0, "held": 1.0, "contour": 1.0, "grid": 0.0}

    assert phrases.scored(corroborated) > phrases.scored(lonely)


def test_a_leap_is_not_read_across_a_note_with_no_pitch() -> None:
    """An unpitched event has no interval to leap by, and inventing one would nominate noise."""
    played = _running(4) + [Note(seconds=4 * BEAT, end=4 * BEAT + 0.4, hz=0.0, strength=0.5)]

    detection = phrases.boundaries(played, _grid())

    assert detection.boundaries[-1].interval is None


def test_two_endings_closer_than_half_a_bar_are_reported_once() -> None:
    """One ending heard twice — a breath inside a phrase is not the end of another one."""
    #  Endings at 1.9, 2.7 and 3.4. The first two are 0.8s apart, under half a bar at 120.
    played = _running(4) + _line([2.3, 3.0])

    detection = phrases.boundaries(played, _grid(), minimum_confidence=0.0)

    assert detection.dropped == 1
    spacing = [
        later.measured - earlier.measured
        for earlier, later in zip(
            detection.boundaries, detection.boundaries[1:], strict=False
        )
    ]
    assert all(one >= phrases.MINIMUM_PHRASE_BEATS * BEAT for one in spacing)


def test_the_default_floor_keeps_a_marginal_ending_out_of_the_record() -> None:
    """A rest at the nomination threshold: weighed, counted, and not written.

    The floor is asserted at its shipped value rather than at 1.0. A floor of 1.0 rejects
    everything by construction and so says nothing about the detector anyone will actually
    run — which is how two dead cues survived the first pass.
    """
    #  The fourth note ends at 1.85 and the line resumes at 2.10 — a rest of exactly REST_BEATS.
    played = _line([0.0, 0.5, 1.0, 1.5], held=0.35) + _line([2.10, 2.60], held=0.35)

    generous = phrases.boundaries(played, _grid(), minimum_confidence=0.0)
    default = phrases.boundaries(played, _grid())

    assert any(one.measured == pytest.approx(1.85) for one in generous.boundaries)
    assert not any(one.measured == pytest.approx(1.85) for one in default.boundaries)
    assert default.considered == generous.considered


def test_a_line_of_one_note_reads_nothing() -> None:
    assert phrases.boundaries(_running(1), _grid()).boundaries == ()


def test_a_grid_with_no_tempo_in_it_reads_nothing() -> None:
    """No beat to measure a rest in. Better nothing than a rest measured against zero."""
    flat = beats_module.rows(BeatGrid(beats=(0.0, 0.0, 0.0), downbeats=(0.0,)))

    assert phrases.boundaries(_running(8), flat).boundaries == ()


def test_notes_with_no_length_read_nothing() -> None:
    """A median note length of zero is no baseline: every note would be infinitely long."""
    instant = [
        Note(seconds=index * BEAT, end=index * BEAT, hz=440.0, strength=0.5)
        for index in range(8)
    ]

    assert phrases.boundaries(instant, _grid()).boundaries == ()


# --- the rule layer: what a boundary says about itself -------------------------------


def test_a_boundary_is_called_on_the_first_beat_inside_the_rest() -> None:
    """The cut goes where nothing is playing, not on the frame the last note stopped."""
    played = _running(8, held=0.3) + _running(8, first=8 * BEAT + BEAT)
    #  the eighth note stops at 3.8s, and the rest runs to 4.5s: beat 4.0 is inside it

    (first, *_) = phrases.boundaries(played, _grid()).boundaries

    assert first.measured == pytest.approx(3.8)
    assert first.seconds == pytest.approx(4.0)
    assert first.snapped is True


def test_a_boundary_names_the_bar_and_beat_it_is_called_on() -> None:
    played = _running(8, held=0.3) + _running(8, first=8 * BEAT + BEAT)

    (first, *_) = phrases.boundaries(played, _grid()).boundaries

    assert (first.bar, first.in_bar, first.downbeat) == (3, 1, True)


def test_an_ending_far_from_any_beat_is_reported_where_it_was_heard_and_says_so() -> None:
    """Past the snap tolerance the measured time stands — the same rule solo changes use."""
    played = _running(4, held=0.3)
    beyond = _grid()[-1]["t"] + phrases.SNAP_BEATS * BEAT * 4
    played.append(Note(seconds=beyond, end=beyond + 0.3, hz=440.0, strength=0.5))

    last = phrases.boundaries(played, _grid(), minimum_confidence=0.0).boundaries[-1]

    assert last.snapped is False
    assert last.seconds == pytest.approx(last.measured)


def test_a_boundary_counts_the_notes_in_the_phrase_it_closes() -> None:
    played = _running(8) + _running(8, first=8 * BEAT + BEAT)

    first, second = phrases.boundaries(played, _grid()).boundaries

    assert (first.notes, second.notes) == (8, 8)


def test_the_ending_at_the_top_of_a_four_bar_phrase_outscores_the_one_mid_bar() -> None:
    """The grid factor, which is the same rule ``fills`` scores its resolution point with."""
    on_the_phrase = phrases.boundaries(
        _running(8, held=0.3) + _running(4, first=8 * BEAT + BEAT), _grid(), 0.0
    ).boundaries[0]
    mid_bar = phrases.boundaries(
        _running(9, held=0.3) + _running(4, first=9 * BEAT + BEAT), _grid(), 0.0
    ).boundaries[0]

    assert on_the_phrase.factors["grid"] > mid_bar.factors["grid"]
    assert on_the_phrase.confidence > mid_bar.confidence


def test_a_longer_rest_reads_as_a_stronger_ending() -> None:
    short = phrases.boundaries(
        _running(8) + _running(4, first=8 * BEAT + BEAT), _grid()
    ).boundaries[0]
    long = phrases.boundaries(
        _running(8) + _running(4, first=8 * BEAT + 3 * BEAT), _grid()
    ).boundaries[0]

    assert long.factors["rest"] > short.factors["rest"]


def test_every_factor_and_the_confidence_stay_inside_zero_to_one() -> None:
    played = _running(4)
    played[3] = played[3]._replace(end=played[3].seconds + 20.0)
    played += _running(4, first=played[3].end + 8.0, held=0.4)

    for one in phrases.boundaries(played, _grid(), minimum_confidence=0.0).boundaries:
        assert all(0.0 <= value <= 1.0 for value in one.factors.values())
        assert 0.0 <= one.confidence <= 1.0


# --- the records and the gist --------------------------------------------------------


def test_a_record_carries_both_placements_so_a_cut_can_use_either() -> None:
    played = _running(8, held=0.3) + _running(8, first=8 * BEAT + BEAT)

    (record, *_) = phrases.rows(phrases.boundaries(played, _grid()))

    assert record["t"] == pytest.approx(4.0)
    assert record["measured_t"] == pytest.approx(3.8)
    assert record["resumes_t"] == pytest.approx(4.5)


def test_the_gist_reports_the_median_phrase_length() -> None:
    played = _running(8) + _running(8, first=8 * BEAT + BEAT)
    detection = phrases.boundaries(played, _grid())

    summary = phrases.gist(detection, phrases.DEFAULT_MINIMUM_CONFIDENCE, "other", len(played))

    assert summary["count"] == 2
    assert summary["median_phrase_seconds"] == pytest.approx(4.5)
    assert summary["stem"] == "other"


def test_the_gist_accounts_for_every_ending_it_weighed() -> None:
    """Kept, too weak and too soon must add up to considered, or a number is being lost."""
    rest = phrases.REST_BEATS * BEAT
    played = _running(4) + _line([4 * BEAT - BEAT / 4 + rest, 12 * BEAT])
    detection = phrases.boundaries(played, _grid(), minimum_confidence=0.5)

    summary = phrases.gist(detection, 0.5, "other", len(played))

    assert (
        summary["count"] + summary["below_confidence"] + summary["too_soon_dropped"]
        == summary["considered"]
    )


def test_an_empty_detection_still_answers_every_gist_key() -> None:
    summary = phrases.gist(phrases.boundaries((), _grid()), 0.35, "other", 0)

    assert summary["count"] == 0
    assert summary["mean_confidence"] is None
    assert summary["median_phrase_seconds"] is None
    assert summary["strongest"] is None


# --- the job: what lands on disk, what comes back inline ----------------------------


def _read(played: Sequence[Note]) -> melody.Reader:
    """A reader standing in for the soloist: two phrases with a bar's rest between them."""

    def reader(stem: Path) -> tuple[Note, ...]:
        return tuple(played)

    return reader


def _detector_for(rows: Sequence[Mapping[str, Any]]) -> Detector:
    beats = tuple(row["t"] for row in rows)
    downbeats = tuple(row["t"] for row in rows if row["downbeat"])

    def detector(path: Path) -> BeatGrid:
        return BeatGrid(beats=beats, downbeats=downbeats)

    return detector


def _finished(record: JobRecord) -> dict[str, Any]:
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def _two_phrases() -> list[Note]:
    return _running(8, held=0.3) + _running(8, first=8 * BEAT + BEAT)


def _ran(
    stems: Mapping[str, Path] | str | Path,
    audio: Path,
    rows: Sequence[Mapping[str, Any]],
    **asked: Any,
) -> dict[str, Any]:
    """Run the job to completion with the grid and the line injected."""
    started = phrases.detect_phrases(
        stems,
        audio,
        reader=_read(_two_phrases()),
        detector=_detector_for(rows),
        **asked,
    )
    return _finished(wait_for(started["job_id"]))


def test_the_job_writes_one_record_per_boundary_and_returns_the_gist(
    master: Path,
    stems: dict[str, Path],
) -> None:
    result = _ran(stems, master, _grid())

    written = Path(result["path"])
    assert written.parent == get_config().analysis_dir
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["kind"] == phrases.PHRASES
    assert len(document[phrases.PHRASES]) == result["count"] == 2
    assert result["stem"] == phrases.DEFAULT_STEM


def test_the_document_holds_one_boundary_per_line(
    master: Path,
    stems: dict[str, Path],
) -> None:
    result = _ran(stems, master, _grid())

    lines = Path(result["path"]).read_text(encoding="utf-8").splitlines()
    records = [line for line in lines if line.strip().startswith('{"t"')]
    assert len(records) == 2


def test_the_directory_a_separation_reports_is_accepted(
    master: Path,
    separation: Path,
) -> None:
    """The job hands back the parent of both passes, so the parent is what this takes."""
    assert _ran(separation, master, _grid())["count"] == 2


def test_the_mix_pass_on_its_own_is_accepted_too(master: Path, separation: Path) -> None:
    assert _ran(separation / MIX_PASS, master, _grid())["count"] == 2


def test_another_stem_can_be_named(master: Path, separation: Path) -> None:
    """A vocal-led band's line is in vocals, and a director with a horn stem should say so."""
    assert _ran(separation, master, _grid(), stem="vocals")["stem"] == "vocals"


def test_the_worker_reads_the_real_stem_when_no_reader_is_injected(
    master: Path,
    tmp_path: Path,
) -> None:
    """The one end-to-end pass: audio in, boundaries out, on a line whose phrasing was written.

    Two phrases of four crotchets with a full bar of rest between them. The boundary is
    asserted to within 0.3s — the tolerance the ticket names — because the reader is allowed
    half a window and the grid is allowed to pull the placement to the nearest beat.
    """
    stem = write_tones(
        tmp_path / "stems" / "mix" / "concert_(Other)_model.wav",
        notes=[(index * BEAT, 0.35, 440.0) for index in range(4)]
        + [(8 * BEAT + index * BEAT, 0.35, 660.0) for index in range(4)],
        seconds=16.0,
        sample_rate=16_000,
    )

    started = phrases.detect_phrases(
        {phrases.DEFAULT_STEM: stem},
        master,
        detector=_detector_for(_grid()),
    )
    result = _finished(wait_for(started["job_id"]))

    written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    ends = [record["measured_t"] for record in written[phrases.PHRASES]]
    assert any(end == pytest.approx(3 * BEAT + 0.35, abs=0.3) for end in ends)


def test_progress_only_climbs_and_the_document_is_the_job_artifact(
    master: Path,
    stems: dict[str, Path],
) -> None:
    steps: list[tuple[float, str]] = []

    output: JobOutput = phrases.detect(
        master,
        stems[phrases.DEFAULT_STEM],
        {"stem": phrases.DEFAULT_STEM, "minimum_confidence": phrases.DEFAULT_MINIMUM_CONFIDENCE},
        lambda fraction, step: steps.append((fraction, step)),
        reader=_read(_two_phrases()),
        detector=_detector_for(_grid()),
    )

    fractions = [fraction for fraction, _ in steps]
    assert fractions == sorted(fractions)
    assert [step for _, step in steps if "phrase" in step]
    assert output.artifacts == (Path(output.result["path"]),)


# --- the cache ----------------------------------------------------------------------


def _must_not_run() -> melody.Reader:
    def reader(stem: Path) -> tuple[Note, ...]:
        raise AssertionError("the cached result should have answered this")

    return reader


def test_asking_twice_answers_from_cache_without_reading_the_stem_again(
    master: Path,
    stems: dict[str, Path],
) -> None:
    first = _ran(stems, master, _grid())

    again = phrases.detect_phrases(stems, master, reader=_must_not_run())

    assert again["cached"] is True
    assert again["result"] == first


def test_refresh_redoes_the_work_the_cache_would_have_answered(
    master: Path,
    stems: dict[str, Path],
) -> None:
    _ran(stems, master, _grid())

    started = phrases.detect_phrases(
        stems,
        master,
        reader=_read(_two_phrases()),
        detector=_detector_for(_grid()),
        refresh=True,
    )
    record = wait_for(started["job_id"])

    assert record.cached is False
    assert _finished(record)["count"] == 2


def test_a_different_stem_is_a_different_key(master: Path, stems: dict[str, Path]) -> None:
    _ran(stems, master, _grid())

    started = phrases.detect_phrases(
        stems,
        master,
        stem="vocals",
        reader=_read(_two_phrases()),
        detector=_detector_for(_grid()),
    )

    assert started.get("cached") is not True


def test_the_beat_grid_is_the_one_analyze_music_already_paid_for(
    master: Path,
    stems: dict[str, Path],
) -> None:
    """The two jobs share the beats cache entry, so the model runs once per master."""
    rows = _grid()
    wait_for(music.analyze_music(master, energy=False, detector=_detector_for(rows))["job_id"])

    def detector_that_must_not_run(path: Path) -> BeatGrid:
        raise AssertionError("beats were already detected for this audio")

    started = phrases.detect_phrases(
        stems,
        master,
        reader=_read(_two_phrases()),
        detector=detector_that_must_not_run,
    )

    assert _finished(wait_for(started["job_id"]))["count"] == 2


# --- what the starter refuses -------------------------------------------------------


def test_a_missing_master_is_refused_before_a_job_exists(
    tmp_path: Path,
    stems: dict[str, Path],
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases(stems, tmp_path / "nothing.wav")

    assert "nothing.wav" in raised.value.cause


def test_a_stem_that_is_not_in_the_separation_is_refused(
    master: Path,
    separation: Path,
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases(separation, master, stem="saxophone")

    assert raised.value.detail["wanted"] == "saxophone"
    assert sorted(FOUR_STEMS) == raised.value.detail["found"]


def test_the_drum_pass_is_not_mistaken_for_the_melodic_stems(
    master: Path,
    separation: Path,
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases(separation / DRUM_PASS, master)

    assert raised.value.detail["found"] == sorted(DRUM_STEMS)


def test_a_directory_that_is_not_there_is_refused(tmp_path: Path, master: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases(tmp_path / "gone", master)

    assert "gone" in raised.value.cause


def test_a_named_stem_that_is_not_on_disk_is_refused(tmp_path: Path, master: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases({"other": tmp_path / "gone.wav"}, master)

    assert "gone.wav" in raised.value.cause


@pytest.mark.parametrize("floor", [-0.1, 1.1])
def test_a_confidence_floor_outside_zero_to_one_is_refused(
    master: Path,
    stems: dict[str, Path],
    floor: float,
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        phrases.detect_phrases(stems, master, minimum_confidence=floor)

    assert raised.value.detail["minimum_confidence"] == floor


# --- the tool seam -------------------------------------------------------------------


def test_the_tool_returns_a_job_to_poll_inside_an_ok_envelope(
    master: Path,
    separation: Path,
) -> None:
    envelope = analysis_tools.detect_phrases(str(separation), str(master))

    assert envelope["ok"] is True
    assert "job_id" in envelope["job"]


def test_the_tool_reports_a_bad_request_as_a_structured_error(tmp_path: Path) -> None:
    envelope = analysis_tools.detect_phrases(str(tmp_path), str(tmp_path / "nothing.wav"))

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"


def test_the_tool_is_registered_so_the_server_picks_it_up() -> None:
    """``build_server`` iterates each module's ``TOOLS``; nothing else registers a tool."""
    assert analysis_tools.detect_phrases in analysis_tools.TOOLS
