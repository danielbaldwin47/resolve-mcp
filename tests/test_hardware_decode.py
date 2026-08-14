"""Hardware decode (#202): one device policy across the probe, the flags and the record.

This file is a device file in the ``test_cut_devices.py`` sense: NVDEC is one decision
spread over ``ffmpeg.py`` (the capability probe), ``video/ffmpeg.py`` (the flag shaping,
the software fallback and the report) and the three video routes that carry the report in
their results — and the failure worth testing is the disagreement, a decode that ran one
way and reported another. The G10 rule everything here serves: a CPU decode may happen,
but it may never happen silently.

The probe and the commands are argv shaping, so the whole file runs on fakes; whether
NVDEC actually decodes on the live box is ``test_live_smoke.py``'s business.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import FrameGrabError, InvalidRequestError
from resolve_mcp.ffmpeg import Completed, Runner, hwaccels, hwaccels_command
from resolve_mcp.video import ffmpeg as video_ffmpeg
from resolve_mcp.video.ffmpeg import (
    CUDA_FLAGS,
    choose_decode,
    sample_command,
    scene_command,
    still_command,
)

from .fakes import hwaccel_probe_reply, write_jpeg

CUDA_BOX = ("cuda", "d3d11va", "vulkan")
"""What the live box's ffmpeg lists (among others). Order and extras must not matter."""


def _answering(methods: Sequence[str], calls: list[list[str]] | None = None) -> Runner:
    """A runner that answers the probe with ``methods`` and succeeds at everything else."""

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, methods)
        if probed is not None:
            return probed
        if calls is not None:
            calls.append(list(argv))
        return Completed(0, "")

    return runner


# --- the probe -----------------------------------------------------------------------------


def test_the_probe_asks_the_binary_and_reads_one_method_a_line() -> None:
    assert hwaccels_command("ffmpeg") == ["ffmpeg", "-hide_banner", "-hwaccels"]
    found = hwaccels(runner=_answering(CUDA_BOX))
    assert found == frozenset(CUDA_BOX)


def test_the_probe_drops_the_header_line_the_binary_prints() -> None:
    assert "Hardware acceleration methods:" not in hwaccels(runner=_answering(("cuda",)))


def test_the_probe_runs_once_per_process_not_once_per_decode() -> None:
    probes: list[list[str]] = []

    def counting(argv: Sequence[str]) -> Completed:
        probes.append(list(argv))
        return hwaccel_probe_reply(argv, ("cuda",)) or Completed(0, "")

    assert hwaccels(runner=counting) == hwaccels(runner=counting)
    assert len(probes) == 1


def test_a_probe_the_binary_refuses_reads_as_no_hardware_support() -> None:
    def refusing(argv: Sequence[str]) -> Completed:
        return Completed(1, "Unrecognized option 'hwaccels'")

    assert hwaccels(runner=refusing) == frozenset()


# --- the choice ----------------------------------------------------------------------------


def test_auto_uses_cuda_when_the_binary_lists_it() -> None:
    choice = choose_decode(runner=_answering(CUDA_BOX))
    assert choice.flags == CUDA_FLAGS
    assert choice.device == "cuda"
    assert choice.reason is None


def test_auto_degrades_to_software_and_says_why_when_cuda_is_not_listed() -> None:
    choice = choose_decode(runner=_answering(("d3d11va", "vulkan")))
    assert choice.flags == ()
    assert choice.device == "cpu"
    assert choice.reason is not None and "d3d11va" in choice.reason


def test_off_never_probes_and_says_it_was_disabled() -> None:
    config = replace(get_config(), ffmpeg_hwaccel="off")

    def exploding(argv: Sequence[str]) -> Completed:
        raise AssertionError(f"probed with {argv}")

    choice = choose_decode(config, runner=exploding)
    assert choice.flags == ()
    assert choice.reason is not None and "off" in choice.reason


def test_forcing_cuda_never_probes_and_passes_the_flag() -> None:
    config = replace(get_config(), ffmpeg_hwaccel="cuda")

    def exploding(argv: Sequence[str]) -> Completed:
        raise AssertionError(f"probed with {argv}")

    choice = choose_decode(config, runner=exploding)
    assert choice.flags == CUDA_FLAGS
    assert choice.device == "cuda"


def test_an_unknown_mode_is_refused_naming_the_valid_ones() -> None:
    config = replace(get_config(), ffmpeg_hwaccel="nvdec")
    with pytest.raises(InvalidRequestError) as caught:
        choose_decode(config, runner=_answering(CUDA_BOX))
    assert "auto" in str(caught.value.fix)


# --- the flags in the commands -------------------------------------------------------------


def test_every_decode_command_takes_the_flags_on_the_input_side() -> None:
    """``-hwaccel`` is an input option: it must come before ``-i`` or ffmpeg refuses it."""
    for argv in (
        still_command("ffmpeg", "in.mp4", "out.jpg", 1.0, 1568, CUDA_FLAGS),
        scene_command("ffmpeg", "in.mp4", 0.4, CUDA_FLAGS),
        sample_command("ffmpeg", "in.mp4", "out.gray", 0.0, 5.0, 1.0, 96, 54, CUDA_FLAGS),
    ):
        assert argv.index("-hwaccel") < argv.index("-i")
        assert argv[argv.index("-hwaccel") + 1] == "cuda"


def test_without_the_flags_the_commands_are_the_software_ones() -> None:
    assert "-hwaccel" not in still_command("ffmpeg", "in.mp4", "out.jpg", 1.0, 1568)
    assert "-hwaccel" not in scene_command("ffmpeg", "in.mp4", 0.4)
    assert "-hwaccel" not in sample_command("ffmpeg", "in.mp4", "out.gray", 0.0, 5.0, 1.0, 96, 54)


# --- the routes carry the choice -----------------------------------------------------------


def test_a_grab_on_a_cuda_box_decodes_with_the_flag_and_reports_the_device(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, CUDA_BOX)
        if probed is not None:
            return probed
        calls.append(list(argv))
        write_jpeg(Path(argv[-1]), width=64, height=36)
        return Completed(0, "")

    written = video_ffmpeg.grab(tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner)

    assert "-hwaccel" in calls[0]
    assert written.decode == {"device": "cuda", "reason": None}


def test_a_grab_on_a_boxwithout_cuda_reports_the_cpu_and_the_reason(tmp_path: Path) -> None:
    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, ())
        if probed is not None:
            return probed
        write_jpeg(Path(argv[-1]), width=64, height=36)
        return Completed(0, "")

    written = video_ffmpeg.grab(tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner)

    assert written.decode["device"] == "cpu"
    assert written.decode["reason"]


def test_a_hardware_decode_that_fails_retries_in_software_and_reports_the_fallback(
    tmp_path: Path,
) -> None:
    """NVDEC refuses codecs it does not know; the file may be fine. The retry must both
    succeed and confess — a fallback the record does not carry is the G10 silence."""
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, CUDA_BOX)
        if probed is not None:
            return probed
        calls.append(list(argv))
        if "-hwaccel" in argv:
            return Completed(1, "No decoder surface left")
        write_jpeg(Path(argv[-1]), width=64, height=36)
        return Completed(0, "")

    written = video_ffmpeg.grab(tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner)

    assert len(calls) == 2
    assert "-hwaccel" in calls[0] and "-hwaccel" not in calls[1]
    assert written.decode["device"] == "cpu"
    assert "retried" in written.decode["reason"]


def test_a_decode_ffmpeg_quietly_finished_in_software_is_reported_as_the_cpu(
    tmp_path: Path,
) -> None:
    """ffmpeg's own fallback is the sneakiest one: NVDEC lacks the codec profile (this
    box's 4:2:2 concert footage, live-measured 2026-08-14), ffmpeg warns on stderr,
    decodes in software and exits 0 — so the exit-code retry never fires and the record
    would claim a decode the card never did. The stderr line is the only witness."""
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, CUDA_BOX)
        if probed is not None:
            return probed
        calls.append(list(argv))
        write_jpeg(Path(argv[-1]), width=64, height=36)
        return Completed(
            0,
            "[hevc @ 0x1] Hardware is lacking required capabilities\n"
            "[hevc @ 0x1] Failed setup for format cuda: hwaccel initialisation returned error.",
        )

    written = video_ffmpeg.grab(tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner)

    assert len(calls) == 1, "the frames arrived — nothing to retry"
    assert written.decode["device"] == "cpu"
    assert written.decode["reason"] is not None and "codec" in written.decode["reason"]


def test_even_a_forced_cuda_decode_confesses_ffmpegs_internal_fallback(
    tmp_path: Path,
) -> None:
    """Forcing fails loudly on a broken decode, but an internal fallback still exits 0
    with good frames — discarding them helps nobody, so the loudness is the record."""
    config = replace(get_config(), ffmpeg_hwaccel="cuda")

    def runner(argv: Sequence[str]) -> Completed:
        write_jpeg(Path(argv[-1]), width=64, height=36)
        return Completed(0, "[hevc @ 0x1] Failed setup for format cuda: nope.")

    written = video_ffmpeg.grab(
        tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner, config
    )

    assert written.decode["device"] == "cpu"
    assert written.decode["reason"]


def test_a_forced_cuda_decode_that_fails_fails_rather_than_quietly_going_software(
    tmp_path: Path,
) -> None:
    config = replace(get_config(), ffmpeg_hwaccel="cuda")
    calls: list[list[str]] = []

    def runner(argv: Sequence[str]) -> Completed:
        calls.append(list(argv))
        return Completed(1, "Cannot load nvcuda.dll")

    with pytest.raises(FrameGrabError):
        video_ffmpeg.grab(tmp_path / "in.mp4", tmp_path / "out.jpg", 1.0, 1568, runner, config)
    assert len(calls) == 1


def test_a_scan_and_a_sample_carry_the_same_report_shape(tmp_path: Path) -> None:
    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv, CUDA_BOX)
        if probed is not None:
            return probed
        if "rawvideo" in argv:
            Path(argv[-1]).write_bytes(bytes(96 * 54))
        return Completed(0, "pts_time:1.0")

    scanned = video_ffmpeg.scan(tmp_path / "in.mp4", 0.4, runner)
    sampled = video_ffmpeg.sample(
        tmp_path / "in.mp4", tmp_path / "o.gray", 0.0, 5.0, 1.0, 96, 54, runner
    )

    assert scanned.decode == {"device": "cuda", "reason": None}
    assert sampled.decode == {"device": "cuda", "reason": None}
