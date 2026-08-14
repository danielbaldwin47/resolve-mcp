"""``analysis/device.py``: the torch paths say which device they infer on (#202).

The stub stands in for torch via ``sys.modules`` so the decisions — what the note carries,
when the announcement warns, that it logs once — verify on a box with any torch or none.
"""

from __future__ import annotations

import sys

import pytest

from resolve_mcp.analysis import device


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
    _install(monkeypatch, "2.13.0+cu126", available=True)
    assert device.torch_note() == {
        "torch": "2.13.0+cu126",
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


def test_the_cpu_build_announcement_names_the_corpus_policy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    with caplog.at_level("INFO"):
        device.announce("beat_this")
    assert any("corpus policy" in one.message for one in caplog.records)


def test_a_cuda_build_that_cannot_see_a_card_warns_instead(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A CUDA build on the CPU is not the corpus policy — it is a box with a driver problem,
    and calling it policy would hide exactly the failure the announcement exists for."""
    _install(monkeypatch, "2.13.0+cu126", available=False)
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
