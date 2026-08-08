"""Driving the render queue: what counts as finished, and what gets left behind.

The polling loop takes its clock and its sleep as parameters, so every wait here is
instant and the timeout case is a test rather than an hour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.errors import RenderQueueError
from resolve_mcp.resolve import render

from .fakes import FakeProject, write_wav


def test_a_completed_render_hands_back_the_file_it_wrote(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    expecting = tmp_path / "mix.wav"
    write_wav(expecting, seconds=0.1)
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    assert render.render(project, job_id, expecting, sleep=_no_sleep) == expecting
    assert project.render_format == ("wav", "lpcm")


def test_the_queue_is_left_as_it_was_found(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    expecting = write_wav(tmp_path / "mix.wav", seconds=0.1)
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    render.render(project, job_id, expecting, sleep=_no_sleep)

    assert project.render_queue == []


def test_a_failed_job_is_a_failure_even_though_every_call_returned_true(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.render_statuses = ["Rendering", "Failed"]
    expecting = write_wav(tmp_path / "mix.wav", seconds=0.1)
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(project, job_id, expecting, sleep=_no_sleep)

    assert "Failed" in raised.value.cause
    assert project.render_queue == []


def test_a_render_that_writes_nothing_is_not_a_success(tmp_path: Path) -> None:
    """Resolve reports Complete for renders that land nothing — an unwritable target does."""
    project = FakeProject("sunset-set")
    project.render_writes_the_file = False
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(project, job_id, tmp_path / "mix.wav", sleep=_no_sleep)

    assert "wrote nothing" in raised.value.cause


def test_a_queue_that_never_finishes_times_out_pointing_at_the_gui(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.render_statuses = ["Rendering"]
    clock = iter([0.0, 0.0, 10_000.0])
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(
            project,
            job_id,
            tmp_path / "mix.wav",
            timeout=60.0,
            now=lambda: next(clock),
            sleep=_no_sleep,
        )

    assert "still Rendering" in raised.value.cause
    assert "Deliver" in raised.value.fix
    assert project.render_queue == []


def test_a_job_that_never_leaves_the_queue_fails_at_the_start_deadline(tmp_path: Path) -> None:
    """#92, live on 21.0.3.7: a wedged render engine holds every job it is given at
    "Ready for background render" at 0% forever, and deleting the queue does not clear it —
    only restarting Resolve does. Waiting the render timeout out on that spends an hour on a
    state that is knowable in seconds, so a job that never starts is refused on its own,
    much shorter deadline. The clock here holds three readings: exhausting it would raise
    StopIteration, so a loop that waited the hour out could not pass this.
    """
    project = FakeProject("sunset-set")
    project.render_statuses = ["Ready for background render"]
    clock = iter([0.0, 0.0, 61.0])
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(
            project,
            job_id,
            tmp_path / "mix.wav",
            now=lambda: next(clock),
            sleep=_no_sleep,
        )

    assert "never started" in raised.value.cause
    assert "Ready for background render" in raised.value.cause
    assert "Restart Resolve" in raised.value.fix
    assert raised.value.detail["start_timeout_seconds"] == render.START_TIMEOUT
    assert project.render_queue == []


def test_a_job_that_does_start_keeps_the_full_render_timeout(tmp_path: Path) -> None:
    """The start deadline is about starting, not about finishing: once the job leaves the
    queue it gets the whole hour, because a genuinely long render is not a wedge."""
    project = FakeProject("sunset-set")
    project.render_statuses = ["Ready for background render", "Rendering"]
    clock = iter([0.0, 0.0, 100.0, 5_000.0])
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(
            project,
            job_id,
            tmp_path / "mix.wav",
            now=lambda: next(clock),
            sleep=_no_sleep,
        )

    assert "still Rendering" in raised.value.cause
    assert "never started" not in raised.value.cause


def test_a_status_that_does_not_read_is_never_refused_as_wedged(tmp_path: Path) -> None:
    """Only a reading that succeeded may refuse. An unreported status is how this API says
    "not on this build" and how a dying handle reads, and a running render behind one would
    be thrown away by a start deadline that treated it as queued — the false refusal is the
    worse trade, because the wedge at least announces itself by never finishing.
    """
    project = FakeProject("sunset-set")
    project.render_statuses = [""]
    clock = iter([0.0, 0.0, 100.0, 5_000.0])
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(
            project,
            job_id,
            tmp_path / "mix.wav",
            now=lambda: next(clock),
            sleep=_no_sleep,
        )

    assert "still unreported" in raised.value.cause
    assert "never started" not in raised.value.cause


def test_a_render_timeout_that_lands_first_still_gives_the_wedge_advice(tmp_path: Path) -> None:
    """The two deadlines are both the caller's to set, so the render timeout can land on a
    job that never started. What it says then has to follow the job, not the deadline."""
    project = FakeProject("sunset-set")
    project.render_statuses = ["Ready for background render"]
    clock = iter([0.0, 0.0, 61.0])
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(
            project,
            job_id,
            tmp_path / "mix.wav",
            timeout=30.0,
            start_timeout=600.0,
            now=lambda: next(clock),
            sleep=_no_sleep,
        )

    assert "still Ready for background render" in raised.value.cause
    assert raised.value.fix == render.WEDGED_FIX
    assert raised.value.detail["started"] is False


def test_progress_comes_from_the_jobs_own_percentage(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.render_statuses = ["Rendering", "Rendering", "Complete"]
    expecting = write_wav(tmp_path / "mix.wav", seconds=0.1)
    seen: list[float] = []
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    render.render(
        project,
        job_id,
        expecting,
        progress=lambda fraction, step: seen.append(fraction),
        sleep=_no_sleep,
    )

    assert seen == [0.0, 0.1, 1.0]


@pytest.mark.parametrize(
    ("refusal", "expected"),
    [
        ("accepts_format", "would not render"),
        ("accepts_settings", "refused the render settings"),
        ("accepts_job", "would not add the job"),
    ],
)
def test_every_way_the_queue_can_refuse_a_job_says_which(
    tmp_path: Path,
    refusal: str,
    expected: str,
) -> None:
    project = FakeProject("sunset-set")
    setattr(project, refusal, False)

    with pytest.raises(RenderQueueError) as raised:
        render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    assert expected in raised.value.cause


def test_a_named_preset_is_the_route_taken_and_the_pair_is_never_asked_for(
    tmp_path: Path,
) -> None:
    """Studio 21.0.3.7 refuses every ("wav", …) pair, so the preset goes first (#131).

    Asking for the pair first cost one guaranteed-failing call on every export there; the
    call record is what holds the order, since either route leaves the same format behind.
    The settings are applied after the preset, so the caller's target directory survives it.
    """
    project = FakeProject("sunset-set")
    project.accepts_format = False

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == ["Audio Only"]
    assert project.format_calls == []
    assert project.render_settings["TargetDir"] == str(tmp_path)


def test_a_build_without_the_preset_still_renders_through_the_pair(tmp_path: Path) -> None:
    """The pair is kept as the fallback for builds where it works — an install missing
    the stock preset (renamed, localised, deleted) is one of them."""
    project = FakeProject("sunset-set")
    del project.render_presets["Audio Only"]

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == []
    assert project.format_calls == [("wav", "lpcm")]
    assert project.render_format == ("wav", "lpcm")


def test_a_preset_that_will_not_load_falls_back_to_the_pair(tmp_path: Path) -> None:
    """A preset Resolve lists and then refuses is the other build the pair is kept for."""
    project = FakeProject("sunset-set")
    project.accepts_preset = False

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == []
    assert project.format_calls == [("wav", "lpcm")]
    assert project.render_format == ("wav", "lpcm")


def test_a_preset_that_renders_something_else_does_not_beat_the_pair(tmp_path: Path) -> None:
    """A customised stock preset would otherwise silently win over an explicit request, and
    the job would land a file under a name saying it is something it is not."""
    project = FakeProject("sunset-set")
    project.render_presets["Audio Only"] = ("mp4", "AAC")

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == ["Audio Only"]
    assert project.render_format == ("wav", "lpcm")


def test_a_preset_the_build_will_not_report_a_format_for_keeps_the_render(
    tmp_path: Path,
) -> None:
    """21.0.3.7 answers "unknown" after ``Audio Only`` loads and renders a WAV anyway (live).

    Only a reading that succeeded may demote the preset: treating an unreadable format as
    disagreement would reject the one route that works on the supported build.
    """
    project = FakeProject("sunset-set")
    project.render_presets["Audio Only"] = ("unknown", "")
    project.accepts_format = False

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == ["Audio Only"]
    assert project.format_calls == []


def test_a_preset_with_no_pair_behind_it_fails_rather_than_queueing_the_wrong_format(
    tmp_path: Path,
) -> None:
    """Nothing queues on a format nobody selected: the job would render whatever the
    project was last set to and land a file the caller never asked for."""
    project = FakeProject("sunset-set")
    del project.render_presets["Audio Only"]

    with pytest.raises(RenderQueueError) as raised:
        render.submit(project, {"TargetDir": str(tmp_path)}, preset="Audio Only")

    assert "Audio Only" in raised.value.cause
    assert project.render_jobs == []


def test_neither_route_to_the_format_working_says_both(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.accepts_format = False
    del project.render_presets["Audio Only"]

    with pytest.raises(RenderQueueError) as raised:
        render.submit(
            project,
            {"TargetDir": str(tmp_path)},
            ("wav", "lpcm"),
            preset="Audio Only",
        )

    assert "would not render" in raised.value.cause
    assert "Audio Only" in raised.value.cause
    assert raised.value.detail["preset"] == "Audio Only"
    # What presets do exist is the one fact that identifies a renamed or localised install.
    assert "H.265 Master" in raised.value.detail["available"]


def test_a_queue_that_will_not_start_takes_its_job_back_off(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.starts_rendering = False
    job_id = render.submit(project, {"TargetDir": str(tmp_path)}, ("wav", "lpcm"))

    with pytest.raises(RenderQueueError) as raised:
        render.render(project, job_id, tmp_path / "mix.wav", sleep=_no_sleep)

    assert "would not start" in raised.value.cause
    assert project.render_queue == []


def _no_sleep(seconds: float) -> None:
    """The loop between polls, with the waiting taken out."""
