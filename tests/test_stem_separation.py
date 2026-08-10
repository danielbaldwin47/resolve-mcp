"""Stem separation: two passes always, a third over ``other`` on request.

The separator is a subprocess, so the seam is the same one ffmpeg uses: the call is a
parameter, and every decision around it — the commands, which file each later pass reads,
where the stems land, what a refusal looks like, what a rerun costs, and which of the two
keys the opt-in flag belongs to — is verified with the CLI substituted. What no seam here
can answer is whether audio-separator on the 4080 Super produces those models' stems at all,
or that ``17_HP-Wind_Inst-UVR`` labels its halves the way ``WIND_STEMS`` spells them; that is
the live tier, and it is why the fake writes real WAVs under the real naming convention
rather than empty files.
"""

from __future__ import annotations

import locale
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.audio import separator
from resolve_mcp.audio.acquire import audio_source
from resolve_mcp.audio.stems import (
    DRUM_PASS,
    DRUM_STEMS,
    FOUR_STEMS,
    KIND,
    MIX_PASS,
    OTHER_PASS,
    PASS_TWO_CEILING,
    SEPARATED,
    SEPARATION,
    WIND_KEYS,
    WIND_STEMS,
    acquired,
    multi_pass,
    separate_stems,
    separation_params,
)
from resolve_mcp.config import Config, get_config, set_config
from resolve_mcp.errors import InvalidRequestError, StemSeparationError
from resolve_mcp.ffmpeg import Completed as FfmpegCompleted
from resolve_mcp.ffmpeg import Runner as FfmpegRunner
from resolve_mcp.jobs import cache
from resolve_mcp.jobs.runner import Progress, wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import stems as stems_tool

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeSeparator,
    FakeTimeline,
    media_pool,
    studio,
    with_a_mix,
    write_wav,
)

FIXTURE_SECONDS = 2.0
SIX_DRUM_STEMS = ("kick", "snare", "toms", "hh", "ride", "crash")


@pytest.fixture
def fixture_audio(tmp_path: Path) -> Path:
    """Two seconds of tone standing in for a concert."""
    return write_wav(tmp_path / "media" / "drums.wav", seconds=FIXTURE_SECONDS)


@pytest.fixture
def separating() -> FakeSeparator:
    """A separator that produces four stems on the first pass and six drums on the second."""
    return FakeSeparator(FOUR_STEMS, SIX_DRUM_STEMS)


@pytest.fixture
def splitting() -> FakeSeparator:
    """The same, plus the third pass's two halves — for the runs that ask for the wind split.

    Kept apart from ``separating`` rather than folded into it because the fake hands out its
    passes by call number: a two-pass run against a three-pass fake would be given the wind
    labels on its next first pass.
    """
    return FakeSeparator(FOUR_STEMS, SIX_DRUM_STEMS, WIND_STEMS)


# --- the two passes ----------------------------------------------------------------------


def test_four_stems_land_on_disk_keyed_by_content_hash_and_params(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    stems = record.result["stems"]
    assert set(stems) == set(FOUR_STEMS)
    assert all(Path(one).exists() for one in stems.values())

    expected = cache.cache_key(
        KIND,
        [{"content_sha256": record.result["audio"]["content_sha256"]}],
        {name: record.params[name] for name in SEPARATION},
    )
    assert record.result["key"] == expected
    assert Path(record.result["directory"]).name.endswith(expected[:12])
    assert Path(record.result["directory"]).parent == get_config().stems_dir


def test_the_drum_stem_is_what_the_second_pass_decomposes(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert record.result is not None
    assert record.result["stems"]["drums"] == separating.calls[1][1]
    assert set(DRUM_STEMS) <= set(record.result["drums"])
    assert all(Path(one).exists() for one in record.result["drums"].values())


def test_the_cymbals_are_carried_out_of_the_drum_pass(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """#125: fills here are often cymbal-led, so the pass keeps the ride and crash it writes.

    ``hh`` stays out: the drum model produces it either way, and a hat playing four to the beat
    is a timekeeper the detector gains nothing from counting.
    """
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert {"ride", "crash"} <= set(DRUM_STEMS)
    assert "hh" not in DRUM_STEMS
    assert separation_params()["drum_stems"] == list(DRUM_STEMS)
    assert record.result is not None
    assert {"ride", "crash"} <= set(record.result["drums"])


def test_each_pass_runs_its_own_model(attach: Attach, separating: FakeSeparator) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    config = get_config()

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert len(separating.calls) == 2
    assert _flag(separating.calls[0], "--model_filename") == config.stem_model
    assert _flag(separating.calls[1], "--model_filename") == config.drum_model
    assert record.result is not None
    assert record.result["models"] == {"stems": config.stem_model, "drums": config.drum_model}


def test_the_stems_of_both_passes_are_kept_apart_on_disk(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """One directory per pass, or the drum decomposition overwrites the drum stem it read."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert record.result is not None
    drum_stem = Path(record.result["stems"]["drums"])
    assert drum_stem.parent != Path(record.result["drums"]["kick"]).parent
    assert drum_stem.exists()


# --- the opt-in third pass -----------------------------------------------------------------


def test_the_wind_pass_does_not_run_unless_it_is_asked_for(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """#126: on a band with no piano the split recovers nothing, so it is not free to run.

    ``other`` is already the wind candidate there, and an unconditional third pass would buy
    that nothing with minutes of compute on every job.
    """
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert record.state == "completed", record.error
    assert len(separating.calls) == 2
    assert record.result is not None
    assert OTHER_PASS not in record.result
    assert set(record.result["models"]) == {"stems", "drums"}


def test_the_other_stem_is_what_the_third_pass_splits(
    attach: Attach,
    splitting: FakeSeparator,
) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    config = get_config()

    record = wait_for(
        separate_stems(get_connection(), runner=splitting, split_wind=True)["job_id"]
    )

    assert record.state == "completed", record.error
    assert len(splitting.calls) == 3
    assert record.result is not None
    assert record.result["stems"]["other"] == splitting.calls[2][1]
    assert _flag(splitting.calls[2], "--model_filename") == config.wind_model
    assert record.result["models"]["other"] == config.wind_model
    assert set(record.result[OTHER_PASS]) == set(WIND_KEYS.values())
    assert all(Path(one).exists() for one in record.result[OTHER_PASS].values())


def test_the_third_pass_writes_beside_the_other_two_not_into_them(
    attach: Attach,
    splitting: FakeSeparator,
) -> None:
    """The same rule pass two follows: a model that relabels its input must not land on it."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(
        separate_stems(get_connection(), runner=splitting, split_wind=True)["job_id"]
    )

    assert record.result is not None
    directory = Path(record.result["directory"])
    wind = Path(record.result[OTHER_PASS]["wind"])
    assert wind.parent == directory / OTHER_PASS
    assert wind.parent not in {directory / MIX_PASS, directory / DRUM_PASS}
    assert Path(record.result["stems"]["other"]).exists()


def test_the_residual_half_is_never_offered_as_a_piano_stem(
    attach: Attach,
    splitting: FakeSeparator,
) -> None:
    """#126: ``No Woodwinds`` is piano *plus* guitar, vibes, percussion and leaked bass.

    On a bass-weak capture it is mostly the bass line, so the one name it must not carry is
    the one an agent would reach for. The tool's own docstring is where an agent learns that,
    which makes it part of the envelope rather than a comment.
    """
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(
        separate_stems(get_connection(), runner=splitting, split_wind=True)["job_id"]
    )

    assert record.result is not None
    assert "piano" not in record.result[OTHER_PASS]
    assert WIND_KEYS["no woodwinds"] == "comp"
    described = stems_tool.separate_stems.__doc__ or ""
    assert "not a piano stem" in described
    assert "bass" in described


def test_a_wind_model_that_leaves_a_half_out_fails_naming_it(attach: Attach) -> None:
    """The drum pass's shape: a model that renames its output is a named failure, not a gap."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    partial = FakeSeparator(FOUR_STEMS, SIX_DRUM_STEMS, ("woodwinds",))

    record = wait_for(separate_stems(get_connection(), runner=partial, split_wind=True)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "stem_separation_failed"
    assert "no woodwinds" in record.error["cause"]
    assert record.error["detail"]["produced"] == ["woodwinds"]


def test_the_flag_keys_the_job_while_the_model_keys_the_stems(
    attach: Attach,
    separating: FakeSeparator,
    splitting: FakeSeparator,
) -> None:
    """Both halves of where the third pass lives in the two keys.

    The flag is a job param, or the run that asks for the wind pass is handed the cached
    two-pass answer and never runs anything. It is *not* a stems-key param, or turning it on
    would separate the same audio into a second directory and orphan the first.
    """
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    two = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    three = separate_stems(get_connection(), runner=splitting, split_wind=True)

    assert three["cached"] is False
    record = wait_for(three["job_id"])
    assert record.state == "completed", record.error
    assert record.result is not None
    assert two.result is not None
    assert record.result["directory"] == two.result["directory"]
    assert separation_params()["wind_model"] == get_config().wind_model
    assert separation_params()["wind_stems"] == list(WIND_STEMS)


def test_a_two_pass_result_on_disk_does_not_read_as_complete_when_the_wind_pass_is_on(
    tmp_path: Path,
    separating: FakeSeparator,
    splitting: FakeSeparator,
) -> None:
    """A directory missing the pass this run wants is partial, and partial is redone whole."""
    audio = _acquired(tmp_path)
    multi_pass(audio, _params(), _ignored, runner=separating)

    output = multi_pass(audio, _params(), _ignored, runner=splitting, split_wind=True)

    assert output.result["reused"] is False
    assert len(splitting.calls) == 3
    assert set(output.result[OTHER_PASS]) == set(WIND_KEYS.values())


def test_a_two_pass_result_on_disk_is_complete_when_the_wind_pass_is_off(
    tmp_path: Path,
    splitting: FakeSeparator,
    separating: FakeSeparator,
) -> None:
    """The other half: a run that never wanted the third pass must not redo the first two.

    Both directions matter because the two shapes share a directory — the reuse check has to
    read completeness off what this run asked for, not off what happens to be on disk.
    """
    audio = _acquired(tmp_path)
    multi_pass(audio, _params(), _ignored, runner=splitting, split_wind=True)

    output = multi_pass(audio, _params(), _ignored, runner=separating)

    assert output.result["reused"] is True
    assert separating.calls == []
    assert OTHER_PASS not in output.result


def test_a_three_pass_result_is_reused_whole(
    tmp_path: Path,
    splitting: FakeSeparator,
) -> None:
    audio = _acquired(tmp_path)
    first = multi_pass(audio, _params(), _ignored, runner=splitting, split_wind=True)

    again = multi_pass(audio, _params(), _ignored, runner=splitting, split_wind=True)

    assert len(splitting.calls) == 3
    assert again.result["reused"] is True
    assert again.result[OTHER_PASS] == first.result[OTHER_PASS]


# --- progress ----------------------------------------------------------------------------


def test_progress_climbs_through_both_passes(tmp_path: Path) -> None:
    """A concert separation runs for minutes; a poller has to see which pass it is in."""
    steps: list[tuple[float, str]] = []
    audio = _acquired(tmp_path)
    separating = FakeSeparator(
        FOUR_STEMS,
        SIX_DRUM_STEMS,
        output=("  0%|          | 0/10", " 50%|#####     | 5/10", "100%|##########| 10/10"),
    )

    multi_pass(audio, {"scope": "clip"}, _recording(steps), runner=separating)

    assert [fraction for fraction, _ in steps] == sorted(fraction for fraction, _ in steps)
    first = [step for fraction, step in steps if "50%" in step]
    assert any("four stems" in step for step in first)
    assert any("drum" in step for _, step in steps)
    assert steps[-1][0] == pytest.approx(0.95, abs=0.05)
    assert max(fraction for fraction, step in steps if "drum" in step) == pytest.approx(SEPARATED)


def test_progress_climbs_through_three_passes_without_moving_where_the_last_one_ends(
    tmp_path: Path,
) -> None:
    """The third pass splits the back half rather than extending past it.

    Whichever pass is last has to finish where the bar's separation phase always finishes, or
    the cheaper two-pass job would sit at a number that reads as unfinished while it is done.
    """
    steps: list[tuple[float, str]] = []
    splitting = FakeSeparator(
        FOUR_STEMS,
        SIX_DRUM_STEMS,
        WIND_STEMS,
        output=("  0%|          | 0/10", " 50%|#####     | 5/10", "100%|##########| 10/10"),
    )

    multi_pass(_acquired(tmp_path), _params(), _recording(steps), split_wind=True, runner=splitting)

    assert [fraction for fraction, _ in steps] == sorted(fraction for fraction, _ in steps)
    winds = [fraction for fraction, step in steps if "wind" in step]
    assert winds
    assert min(winds) >= PASS_TWO_CEILING
    assert max(winds) == pytest.approx(SEPARATED)
    assert max(fraction for fraction, step in steps if "drum" in step) == pytest.approx(
        PASS_TWO_CEILING
    )


def test_the_model_downloads_own_bar_is_not_this_passs_progress(tmp_path: Path) -> None:
    """A first run fetches the model and prints a bar for that, ending at 100%, first.

    Reporting it would run the pass to its ceiling before the separation had started, and
    then either freeze there for the long part or visibly fall back.
    """
    steps: list[tuple[float, str]] = []
    downloading = FakeSeparator(
        FOUR_STEMS,
        SIX_DRUM_STEMS,
        output=(
            "Downloading model htdemucs_ft.yaml:  50%|#####     |",
            "Downloading model htdemucs_ft.yaml: 100%|##########|",
            " 20%|##        | 2/10",
        ),
    )

    multi_pass(_acquired(tmp_path), _params(), _recording(steps), runner=downloading)

    first = [fraction for fraction, step in steps if "four stems" in step]
    assert first == [pytest.approx(0.32)]  # 20% of the pass one slice, and nothing before it


def test_the_acquisition_is_reported_as_the_first_quarter_of_the_job(attach: Attach) -> None:
    """The export runs inside this job, so its own progress has to be visible from here."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    steps: list[tuple[float, str]] = []
    source = audio_source(get_connection())

    acquired(source, _recording(steps), poll=0.01)

    assert steps
    assert all(0.0 <= fraction <= 0.25 for fraction, _ in steps)
    assert any("export" in step or "audio" in step for _, step in steps)


# --- caching -----------------------------------------------------------------------------


def test_a_rerun_on_an_unchanged_timeline_never_separates_again(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    first = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    again = separate_stems(get_connection(), runner=separating)

    assert again["state"] == "completed"
    assert again["cached"] is True
    assert again["result"] == first.result
    assert len(separating.calls) == 2


def test_refresh_separates_again(attach: Attach, separating: FakeSeparator) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    again = wait_for(separate_stems(get_connection(), runner=separating, refresh=True)["job_id"])

    assert again.state == "completed", again.error
    assert again.cached is False
    assert len(separating.calls) == 4


def test_the_same_audio_under_a_new_fingerprint_reuses_the_stems_on_disk(
    tmp_path: Path,
    separating: FakeSeparator,
) -> None:
    """The directory is keyed by content, so an edit that did not change the mix is free."""
    audio = _acquired(tmp_path)
    multi_pass(audio, {"scope": "timeline"}, _ignored, runner=separating)

    output = multi_pass(audio, {"scope": "timeline"}, _ignored, runner=separating)

    assert len(separating.calls) == 2
    assert output.result["reused"] is True
    assert all(Path(one).exists() for one in output.result["stems"].values())


def test_where_the_audio_came_from_is_not_part_of_the_stems_key(
    tmp_path: Path,
    separating: FakeSeparator,
) -> None:
    """A renamed timeline is the same audio; separating it again would pay the GPU twice."""
    audio = _acquired(tmp_path)
    first = multi_pass(audio, _params(timeline="sunset-set v3"), _ignored, runner=separating)

    renamed = multi_pass(audio, _params(timeline="sunset-set v4"), _ignored, runner=separating)

    assert renamed.result["directory"] == first.result["directory"]
    assert renamed.result["reused"] is True
    assert len(separating.calls) == 2


def test_a_different_model_is_a_different_stems_key(
    tmp_path: Path,
    separating: FakeSeparator,
) -> None:
    """The other half of that: what the stems *are* must still move the key.

    The model comes from config on both runs, so this pins the model actually run as well
    as the key derived from it — a key that moved while the command did not would be worse
    than no key at all.
    """
    audio = _acquired(tmp_path)
    first = multi_pass(audio, separation_params(), _ignored, runner=separating)

    set_config(_configured(tmp_path, stem_model="htdemucs_6s.yaml"))
    other = multi_pass(audio, separation_params(), _ignored, runner=separating)

    assert other.result["directory"] != first.result["directory"]
    assert len(separating.calls) == 4
    assert _flag(separating.calls[0], "--model_filename") == "htdemucs_ft.yaml"
    assert _flag(separating.calls[2], "--model_filename") == "htdemucs_6s.yaml"


def test_the_stems_a_job_owns_are_what_its_cache_entry_verifies(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """Delete a stem and the next run redoes the work rather than pointing at a gone file."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    first = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])
    assert first.result is not None
    Path(first.result["drums"]["kick"]).unlink()

    again = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert again.cached is False
    assert again.state == "completed", again.error
    assert again.result is not None
    assert Path(again.result["drums"]["kick"]).exists()


# --- failures ----------------------------------------------------------------------------


def test_no_separator_on_the_machine_is_a_named_failure(attach: Attach) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    record = wait_for(separate_stems(get_connection(), runner=_absent)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "separator_unavailable"
    assert "RESOLVE_MCP_AUDIO_SEPARATOR" in record.error["fix"]


def test_the_separators_own_complaint_travels_back_with_the_failure(attach: Attach) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    refusing = FakeSeparator(FOUR_STEMS, returncode=1, output=("No such model: htdemucs_ft.yaml",))

    record = wait_for(separate_stems(get_connection(), runner=refusing)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "stem_separation_failed"
    assert "No such model" in record.error["detail"]["output"]


def test_a_model_that_leaves_a_stem_out_fails_naming_it(attach: Attach) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    partial = FakeSeparator(FOUR_STEMS, ("kick", "snare"))

    record = wait_for(separate_stems(get_connection(), runner=partial)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "stem_separation_failed"
    assert "toms" in record.error["cause"]
    assert record.error["detail"]["produced"]


def test_a_failed_export_fails_the_stem_job_with_the_exports_own_advice(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """The acquisition runs inside this job, so its failure has to arrive as this job's."""
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    project = resolve.current_project
    assert project is not None
    project.accepts_job = False
    attach(resolve)

    record = wait_for(separate_stems(get_connection(), runner=separating)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "audio_export_failed"
    assert "audio on it" in record.error["fix"]
    assert separating.calls == []


# --- scopes ------------------------------------------------------------------------------


def test_a_source_clip_is_extracted_then_separated(
    attach: Attach,
    fixture_audio: Path,
    separating: FakeSeparator,
) -> None:
    attach(studio(pool=media_pool({"": [_clip(fixture_audio)]})))

    record = wait_for(
        separate_stems(
            get_connection(),
            scope="clip",
            clip="drums.wav",
            runner=separating,
            ffmpeg_runner=_copying(),
        )["job_id"]
    )

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["audio"]["scope"] == "clip"
    assert record.result["audio"]["duration_seconds"] == pytest.approx(FIXTURE_SECONDS, abs=0.01)
    assert set(record.result["stems"]) == set(FOUR_STEMS)


def test_a_clip_scope_with_no_clip_named_is_refused_before_a_job_starts(attach: Attach) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    with pytest.raises(InvalidRequestError) as raised:
        separate_stems(get_connection(), scope="clip")

    assert "clip" in raised.value.fix


def test_an_unknown_scope_says_which_two_there_are(attach: Attach) -> None:
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    with pytest.raises(InvalidRequestError) as raised:
        separate_stems(get_connection(), scope="project")

    assert "timeline" in raised.value.fix
    assert "clip" in raised.value.fix


# --- the separator wrapper ----------------------------------------------------------------


def test_the_command_names_the_model_the_source_and_the_output_directory() -> None:
    argv = separator.command("audio-separator", "htdemucs_ft.yaml", Path("D:/a.wav"), Path("D:/o"))

    assert argv[0] == "audio-separator"
    assert argv[1] == str(Path("D:/a.wav"))
    assert _flag(argv, "--model_filename") == "htdemucs_ft.yaml"
    assert _flag(argv, "--output_dir") == str(Path("D:/o"))
    assert _flag(argv, "--output_format").lower() == "wav"


def test_a_stem_is_recognised_by_the_last_label_the_separator_parenthesises() -> None:
    """Pass two reads a file that is already labelled, so its own label is the last one."""
    assert separator.label_of(Path("mix_(Drums)_htdemucs.wav")) == "drums"
    assert separator.label_of(Path("mix_(Drums)_htdemucs_(Snare)_MDX23C.wav")) == "snare"
    assert separator.label_of(Path("no-label-at-all.wav")) is None


def test_progress_comes_off_the_percentages_the_separator_prints(tmp_path: Path) -> None:
    seen: list[float] = []
    counting = FakeSeparator(("drums",), output=("Loading model", " 50%|#####  | 5/10", "100%|"))

    separator.separate(
        write_wav(tmp_path / "mix.wav", seconds=0.2),
        tmp_path / "out",
        "htdemucs_ft.yaml",
        ("drums",),
        progress=seen.append,
        runner=counting,
    )

    assert seen == [0.5, 1.0]


def test_a_separator_that_writes_nothing_at_all_is_a_failure_not_an_empty_result(
    tmp_path: Path,
) -> None:
    silent = FakeSeparator(())

    with pytest.raises(StemSeparationError) as raised:
        separator.separate(
            write_wav(tmp_path / "mix.wav", seconds=0.2),
            tmp_path / "out",
            "htdemucs_ft.yaml",
            ("drums",),
            runner=silent,
        )

    assert "drums" in raised.value.cause


# --- reading the real process's output ------------------------------------------------------
#
# The runner parameter is what every test above substitutes, so the one thing it cannot cover
# is the decode ``_run`` itself performs. These two spawn a real child — this interpreter, so
# no binary has to exist — and hand it bytes the separator genuinely emits.
#
# Both run with the console codepage of the machine that reported #139, whatever the machine
# running the test has: ``subprocess`` reads the encoding a text-mode pipe inherits from
# ``locale.getencoding``, so pinning that is what makes a Windows-console bug go red on a
# UTF-8 CI runner rather than passing there for the wrong reason.


@pytest.fixture
def cp1252_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """A launching process whose codepage is cp1252 — a service, or a detached process."""
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")


@pytest.mark.usefixtures("cp1252_console")
def test_a_byte_the_consoles_codepage_cannot_decode_does_not_kill_the_run() -> None:
    """#139: a progress line held one 0x8f and a twenty-minute job died at 1%.

    The strict decode raised out of the read loop rather than out of the child, so nothing
    downstream could catch it — the job simply died at "separating four stems (1%)".
    """
    seen: list[str] = []

    returncode = separator._run(_emitting(b"separating four stems (1%): \x8f"), seen.append)

    assert returncode == 0
    assert seen == ["separating four stems (1%): �\n"]


@pytest.mark.usefixtures("cp1252_console")
def test_the_output_is_read_as_utf_8_whatever_the_console_is() -> None:
    """The bar audio-separator draws is UTF-8; decoded as cp1252 it comes back as mojibake."""
    seen: list[str] = []

    separator._run(_emitting("100%|██████| 4/4 café".encode()), seen.append)

    assert seen == ["100%|██████| 4/4 café\n"]


# --- helpers -------------------------------------------------------------------------------


def _emitting(payload: bytes) -> list[str]:
    """Argv for a child that writes exactly these bytes to stdout and exits cleanly."""
    source = f"import sys; sys.stdout.buffer.write({payload!r} + b'\\n')"
    return [sys.executable, "-c", source]


def _flag(argv: Sequence[str], name: str) -> str:
    return argv[list(argv).index(name) + 1]


def _ignored(fraction: float, step: str) -> None:
    """A progress callback for the tests that are not about progress."""


def _recording(steps: list[tuple[float, str]]) -> Progress:
    def report(fraction: float, step: str) -> None:
        steps.append((fraction, step))

    return report


def _params(timeline: str = "sunset-set v3") -> dict[str, Any]:
    """A job's params: where the audio came from, and what is being run on it."""
    return {"scope": "timeline", "timeline": timeline, **separation_params()}


def _configured(tmp_path: Path, stem_model: str) -> Config:
    """The same cache root the fixture set up, with a different pass-one model."""
    return Config.from_env(
        {
            "RESOLVE_MCP_CACHE": str(tmp_path / "cache"),
            "RESOLVE_MCP_STEM_MODEL": stem_model,
        }
    )


def _acquired(tmp_path: Path) -> dict[str, Any]:
    """What an acquisition job hands the worker: a WAV on disk and its content hash."""
    path = write_wav(tmp_path / "cache" / "audio" / "mix.wav", seconds=0.5)
    return {
        "path": str(path),
        "duration_seconds": 0.5,
        "content_sha256": cache.content_hash(path),
        "scope": "timeline",
    }


def _clip(source: Path) -> FakeMediaPoolItem:
    return FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={"Type": "Audio", "Audio Ch": "2"},
    )


def _copying() -> FfmpegRunner:
    def runner(argv: Sequence[str]) -> FfmpegCompleted:
        shutil.copyfile(argv[argv.index("-i") + 1], argv[-1])
        return FfmpegCompleted(0, "")

    return runner


def _absent(argv: Sequence[str], on_line: separator.Lines) -> int:
    raise FileNotFoundError(argv[0])
