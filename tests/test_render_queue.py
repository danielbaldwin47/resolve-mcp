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


def test_a_build_that_refuses_the_pair_renders_through_the_fallback_preset(
    tmp_path: Path,
) -> None:
    """Resolve 21.0.3 refuses every ("wav", …) pair, so the preset is the only way in (#32).

    The settings are applied after the preset, so the caller's target directory survives it.
    """
    project = FakeProject("sunset-set")
    project.accepts_format = False
    project.render_presets["Audio Only"] = ("wav", "lpcm")

    job_id = render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        fallback_preset="Audio Only",
    )

    assert job_id
    assert project.loaded_presets == ["Audio Only"]
    assert project.render_settings["TargetDir"] == str(tmp_path)


def test_the_fallback_preset_is_left_alone_when_the_pair_is_taken(tmp_path: Path) -> None:
    """A preset carries a whole render config — loading one a build did not need would
    overwrite settings the caller never asked to change."""
    project = FakeProject("sunset-set")
    project.render_presets["Audio Only"] = ("wav", "lpcm")

    render.submit(
        project,
        {"TargetDir": str(tmp_path)},
        ("wav", "lpcm"),
        fallback_preset="Audio Only",
    )

    assert project.loaded_presets == []
    assert project.render_format == ("wav", "lpcm")


def test_a_refused_pair_with_no_preset_to_fall_back_on_says_both(tmp_path: Path) -> None:
    project = FakeProject("sunset-set")
    project.accepts_format = False

    with pytest.raises(RenderQueueError) as raised:
        render.submit(
            project,
            {"TargetDir": str(tmp_path)},
            ("wav", "lpcm"),
            fallback_preset="Audio Only",
        )

    assert "would not render" in raised.value.cause
    assert raised.value.detail["preset"] == "Audio Only"


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
