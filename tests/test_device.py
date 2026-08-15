"""``analysis/device.py``: the torch paths say which device they infer on (#202, #245).

The stub stands in for torch via ``sys.modules`` so the decisions — what the note carries,
when the announcement warns, that it logs once, which device the inference sites ask the
models for — verify on a box with any torch or none.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import applause, beats, device


class _Cuda:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _Torch:
    def __init__(self, version: str, available: bool) -> None:
        self.__version__ = version
        self.cuda = _Cuda(available)


def _install(monkeypatch: pytest.MonkeyPatch, version: str, available: bool) -> None:
    monkeypatch.setitem(sys.modules, "torch", _Torch(version, available))


def test_the_note_carries_the_build_and_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cu130", available=True)
    assert device.torch_note() == {
        "torch": "2.13.0+cu130",
        "cuda_available": True,
        "device": "cuda",
    }


def test_a_cpu_build_notes_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    note = device.torch_note()
    assert note is not None
    assert note["device"] == "cpu"
    assert note["cuda_available"] is False


def test_no_torch_is_no_note_rather_than_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)  # import machinery reads None as absent
    assert device.torch_note() is None
    assert device.announce("beat_this") is None


def test_a_cuda_run_announces_the_card(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, "2.13.0+cu130", available=True)
    with caplog.at_level("INFO"):
        device.announce("beat_this")
    assert any("inference on CUDA" in one.message for one in caplog.records)
    assert not [one for one in caplog.records if one.levelname == "WARNING"]


def test_a_cpu_build_warns_and_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#245 flipped these models to CUDA: a ``+cpu`` build in the venv is now the same class
    of broken install as a ``+cpu`` separator, not the corpus policy it used to be."""
    _install(monkeypatch, "2.13.0+cpu", available=False)
    with caplog.at_level("INFO"):
        device.announce("beat_this")
    warned = [one for one in caplog.records if one.levelname == "WARNING"]
    assert warned, "a +cpu build is a broken install, so it warns"
    assert "uv sync --extra analysis" in warned[0].message
    assert not any("corpus policy" in one.message for one in caplog.records)


def test_a_cuda_build_that_cannot_see_a_card_warns_too(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A CUDA build on the CPU is a box with a driver problem rather than a wheel problem,
    so it keeps its own message — the fix is not another `uv sync`."""
    _install(monkeypatch, "2.13.0+cu130", available=False)
    with caplog.at_level("INFO"):
        device.announce("beat_this")
    warned = [one for one in caplog.records if one.levelname == "WARNING"]
    assert warned and "cannot see a CUDA device" in warned[0].message


def test_each_component_announces_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    with caplog.at_level("INFO"):
        device.announce("beat_this")
        device.announce("beat_this")
        device.announce("PANNs")
    mentions = [one for one in caplog.records if "inference on the CPU" in one.message]
    assert len(mentions) == 2


def test_the_note_still_returns_when_the_announcement_is_already_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job record needs the note every time; only the log line is deduplicated."""
    _install(monkeypatch, "2.13.0+cpu", available=False)
    first = device.announce("beat_this")
    second = device.announce("beat_this")
    assert first == second and second is not None


def test_the_inference_device_follows_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cu130", available=True)
    assert device.inference_device() == "cuda"


def test_the_inference_device_is_the_cpu_without_a_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    assert device.inference_device() == "cpu"


def test_the_inference_device_is_the_cpu_without_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No torch is no card. The models raise their own dependency error a moment later; a
    device string is not the place to report a missing stack."""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert device.inference_device() == "cpu"


class _File2Beats:
    """beat_this's ``File2Beats``, which defaults to ``device="cpu"`` upstream."""

    seen: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _File2Beats.seen = dict(kwargs)

    def __call__(self, path: str) -> tuple[list[float], list[float]]:
        return [1.0, 2.0], [1.0]


class _SoundEventDetection:
    seen: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _SoundEventDetection.seen = dict(kwargs)


class _Panns:
    labels = ("Applause", "Speech")
    SoundEventDetection = _SoundEventDetection


def test_the_beat_model_is_asked_for_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """beat_this defaults to the CPU whatever torch is installed, so the device has to be
    handed to it — otherwise the job record says ``cuda`` over a grid the CPU computed."""
    _install(monkeypatch, "2.13.0+cu130", available=True)
    module = type("_BeatThis", (), {"File2Beats": _File2Beats})
    monkeypatch.setattr(beats, "_loaded", lambda: module)
    beats.beat_this_detector(Path("concert.wav"))
    assert _File2Beats.seen == {"device": "cuda"}


def test_the_beat_model_gets_the_cpu_when_there_is_no_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    module = type("_BeatThis", (), {"File2Beats": _File2Beats})
    monkeypatch.setattr(beats, "_loaded", lambda: module)
    beats.beat_this_detector(Path("concert.wav"))
    assert _File2Beats.seen == {"device": "cpu"}


def test_the_applause_model_is_asked_for_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """PANNs defaults to ``cuda`` and silently falls back, which reads the same as a run
    that reached the card — naming the device makes the record answerable."""
    _install(monkeypatch, "2.13.0+cu130", available=True)
    monkeypatch.setattr(applause, "_loaded", lambda: _Panns)
    monkeypatch.setattr(applause, "_chunks", lambda path: [])
    applause.panns_tagger(Path("concert.wav"))
    assert _SoundEventDetection.seen == {"checkpoint_path": None, "device": "cuda"}


def test_the_applause_model_gets_the_cpu_when_there_is_no_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    monkeypatch.setattr(applause, "_loaded", lambda: _Panns)
    monkeypatch.setattr(applause, "_chunks", lambda path: [])
    applause.panns_tagger(Path("concert.wav"))
    assert _SoundEventDetection.seen == {"checkpoint_path": None, "device": "cpu"}
