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
    assert device.inference_device(device.torch_note()) == "cuda"


def test_the_inference_device_is_the_cpu_without_a_card(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    assert device.inference_device(device.torch_note()) == "cpu"


def test_the_inference_device_is_the_cpu_without_torch() -> None:
    """No torch is no card. The models raise their own dependency error a moment later; a
    device string is not the place to report a missing stack."""
    assert device.inference_device(None) == "cpu"


def test_the_inference_device_reads_the_note_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It does not re-read torch. A second reading could disagree with the line already
    logged, and the disagreeing pair would be the log and the run it claims to describe."""
    _install(monkeypatch, "2.13.0+cpu", available=False)
    stale = {"torch": "2.13.0+cu130", "cuda_available": True, "device": "cuda"}
    assert device.inference_device(stale) == "cuda"


def _pyproject() -> dict[str, Any]:
    import tomllib

    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_torch_packages_are_sourced_from_the_cuda_index_on_windows() -> None:
    """AC1's fake-tier half (#245): the shape of the pin, not the wheel it fetches.

    A dropped entry here is invisible until someone syncs on Windows and gets a `+cpu`
    build back — which is the state this ticket exists to leave behind.
    """
    sources = _pyproject()["tool"]["uv"]["sources"]
    for name in ("torch", "torchaudio", "torchcodec"):
        entries = sources[name]
        assert len(entries) == 1, f"{name} should have exactly one source"
        assert entries[0]["index"] == "pytorch-cu130"
        # win32 only: the same versions' Linux wheels off PyPI are already CUDA builds,
        # and a universal lock should not carry Windows-only artifacts for CI to resolve.
        assert entries[0]["marker"] == "sys_platform == 'win32'"


def test_the_cuda_index_is_declared_and_explicit() -> None:
    """`explicit` is the load-bearing word: without it every package in the tree could be
    resolved from the torch index, which is not what a wheel mirror is for."""
    indexes = _pyproject()["tool"]["uv"]["index"]
    named = {one["name"]: one for one in indexes}
    assert named["pytorch-cu130"]["url"] == "https://download.pytorch.org/whl/cu130"
    assert named["pytorch-cu130"]["explicit"] is True


def test_the_measured_pins_did_not_move_with_the_build() -> None:
    """The corpus diff has one variable in it — the device. If a pin moves in the same
    change, a beat that lands somewhere new cannot be attributed to anything."""
    analysis = _pyproject()["project"]["optional-dependencies"]["analysis"]
    pinned = {one.split(">=")[0]: one for one in analysis if ">=" in one}
    assert pinned["torch"] == "torch>=2.13,<2.14"
    assert pinned["torchaudio"] == "torchaudio>=2.11,<2.12"
    assert pinned["torchcodec"] == "torchcodec>=0.15,<0.16"
    assert any("beat_this@b95c8ab0c58c2d9fcfd40508ae8dffbc05ac4f5c" in one for one in analysis)


@pytest.fixture
def beat_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for beat_this and hand back the kwargs its ``File2Beats`` was built with.

    Fresh per test — a stub that remembered the last run would let a site that stopped
    passing a device keep passing, on the reading before it.
    """
    seen: dict[str, Any] = {}

    class _File2Beats:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def __call__(self, path: str) -> tuple[list[float], list[float]]:
            return [1.0, 2.0], [1.0]

    module = type("_BeatThis", (), {"File2Beats": _File2Beats})
    monkeypatch.setattr(beats, "_loaded", lambda: module)
    return seen


@pytest.fixture
def applause_model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The same for PANNs. ``_chunks`` returns nothing, so no audio is decoded and the
    construction — the only thing under test here — is all that runs."""
    seen: dict[str, Any] = {}

    class _SoundEventDetection:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

    module = type(
        "_Panns",
        (),
        {"labels": ("Applause", "Speech"), "SoundEventDetection": _SoundEventDetection},
    )
    monkeypatch.setattr(applause, "_loaded", lambda: module)
    monkeypatch.setattr(applause, "_chunks", lambda path: [])
    return seen


def test_the_beat_model_is_asked_for_the_card(
    monkeypatch: pytest.MonkeyPatch, beat_model: dict[str, Any]
) -> None:
    """beat_this defaults to the CPU whatever torch is installed, so the device has to be
    handed to it — otherwise the job record says ``cuda`` over a grid the CPU computed."""
    _install(monkeypatch, "2.13.0+cu130", available=True)
    beats.beat_this_detector(Path("concert.wav"))
    assert beat_model == {"device": "cuda"}


def test_the_beat_model_gets_the_cpu_when_there_is_no_card(
    monkeypatch: pytest.MonkeyPatch, beat_model: dict[str, Any]
) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    beats.beat_this_detector(Path("concert.wav"))
    assert beat_model == {"device": "cpu"}


def test_the_applause_model_is_asked_for_the_card(
    monkeypatch: pytest.MonkeyPatch, applause_model: dict[str, Any]
) -> None:
    """PANNs defaults to ``cuda`` and silently falls back, which reads the same as a run
    that reached the card — naming the device makes the record answerable."""
    _install(monkeypatch, "2.13.0+cu130", available=True)
    applause.panns_tagger(Path("concert.wav"))
    assert applause_model == {"checkpoint_path": None, "device": "cuda"}


def test_the_applause_model_gets_the_cpu_when_there_is_no_card(
    monkeypatch: pytest.MonkeyPatch, applause_model: dict[str, Any]
) -> None:
    _install(monkeypatch, "2.13.0+cpu", available=False)
    applause.panns_tagger(Path("concert.wav"))
    assert applause_model == {"checkpoint_path": None, "device": "cpu"}
