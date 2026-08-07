"""What the transcriber decides before a single sample is read: device, precision, DLLs.

The decisions are testable; the consequence is not. Which directories go on the search
path, in what order, on which platform — all of that is a pure function of the venv layout
and asserts here without a GPU. Whether CUDA then initialises is invisible at every seam
(the same shape as ADR 0001's attach), so it belongs to the live smoke and nowhere else.
"""

from __future__ import annotations

import os
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp import config as config_module
from resolve_mcp.analysis import cuda, whisper
from resolve_mcp.config import Config
from resolve_mcp.errors import TranscriberUnavailableError, TranscriptionError

# Whatever was on PATH before us. Deliberately drive-letter-free: these tests assert a
# Windows decision but run on the Linux CI runner too, where os.pathsep is the colon and a
# "C:\..." entry would split itself in half.
ALREADY_ON_PATH = r"\Windows\system32"
CUBLAS = ("cublas64_12.dll", "cublasLt64_12.dll")
CUDNN = ("cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_engines_precompiled64_9.dll")
NVRTC = ("nvrtc64_120_0.dll", "nvrtc-builtins64_129.dll")


def _site_packages(root: Path, **packages: tuple[str, ...]) -> Path:
    """A venv's site-packages with the nvidia wheels laid out the way pip installs them."""
    site = root / "Lib" / "site-packages"
    for package, libraries in packages.items():
        binaries = site / "nvidia" / package / "bin"
        binaries.mkdir(parents=True)
        for library in libraries:
            (binaries / library).write_bytes(b"")
    return site


@pytest.fixture(autouse=True)
def _forget_preparation() -> Iterator[None]:
    cuda.reset_preparation()
    yield
    cuda.reset_preparation()


def test_the_three_nvidia_bin_directories_come_back_in_load_order(tmp_path: Path) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)

    assert cuda.dll_directories(site, platform="win32") == (
        site / "nvidia" / "cublas" / "bin",
        site / "nvidia" / "cudnn" / "bin",
        site / "nvidia" / "cuda_nvrtc" / "bin",
    )


def test_a_wheel_that_is_not_installed_is_skipped_rather_than_offered(tmp_path: Path) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS)

    assert cuda.dll_directories(site, platform="win32") == (site / "nvidia" / "cublas" / "bin",)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_nothing_is_prepared_off_windows_where_the_loader_needs_no_help(
    tmp_path: Path, platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)
    monkeypatch.setenv("PATH", ALREADY_ON_PATH)

    assert cuda.dll_directories(site, platform=platform) == ()
    assert cuda.prepare(site, platform=platform) == ()
    assert os.environ["PATH"] == ALREADY_ON_PATH


def test_preparation_prepends_the_directories_ahead_of_everything_already_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)
    monkeypatch.setenv("PATH", ALREADY_ON_PATH)

    prepared = cuda.prepare(site, platform="win32")

    assert prepared == cuda.dll_directories(site, platform="win32")
    assert all(one.is_absolute() for one in prepared)
    assert os.environ["PATH"].split(os.pathsep) == [str(one) for one in prepared] + [
        ALREADY_ON_PATH
    ]


def test_a_second_preparation_does_not_lengthen_the_path_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One process transcribes many jobs; the search path must not grow a copy per job."""
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)
    monkeypatch.setenv("PATH", ALREADY_ON_PATH)

    cuda.prepare(site, platform="win32")
    once = os.environ["PATH"]

    assert cuda.prepare(site, platform="win32") == ()
    assert os.environ["PATH"] == once


def test_a_venv_without_the_cuda_wheels_is_a_quiet_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CPU-only install is a supported install: nothing to prepare, nothing to say."""
    monkeypatch.setenv("PATH", ALREADY_ON_PATH)

    assert cuda.prepare(tmp_path, platform="win32") == ()
    assert os.environ["PATH"] == ALREADY_ON_PATH


class _Info:
    language = "en"


class _RecordingModel:
    """Stands in for ``faster_whisper.WhisperModel`` to record how it was constructed."""

    built: list[dict[str, str]] = []

    def __init__(self, name: str, device: str, compute_type: str) -> None:
        self.built.append({"name": name, "device": device, "compute_type": compute_type})

    def transcribe(self, path: str, **kwargs: Any) -> tuple[tuple[Any, ...], _Info]:
        return (), _Info()


@pytest.fixture
def recording_backend(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingModel]:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _RecordingModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    _RecordingModel.built = []
    return _RecordingModel


def test_the_model_is_built_with_the_configured_device_and_precision(
    tmp_path: Path, recording_backend: type[_RecordingModel]
) -> None:
    config_module.set_config(
        Config.from_env(
            {
                "RESOLVE_MCP_WHISPER_DEVICE": "cuda",
                "RESOLVE_MCP_WHISPER_COMPUTE_TYPE": "float32",
                "RESOLVE_MCP_CACHE": str(tmp_path / "cache"),
            }
        )
    )

    whisper.transcribe(tmp_path / "take.wav", {})

    assert recording_backend.built == [
        {"name": whisper.DEFAULT_MODEL, "device": "cuda", "compute_type": "float32"}
    ]


def test_a_device_the_backend_refuses_names_the_value_and_the_variable_that_set_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These two are typed by a human now, so a typo has to arrive as advice, not a stack."""

    def _refuse(name: str, device: str, compute_type: str) -> object:
        raise ValueError(f"unsupported device {device}")

    monkeypatch.setattr(whisper, "_build", _refuse)
    monkeypatch.setattr(cuda, "prepare", lambda: ())
    config_module.set_config(
        Config.from_env(
            {
                "RESOLVE_MCP_WHISPER_DEVICE": "gpu",
                "RESOLVE_MCP_CACHE": str(tmp_path / "cache"),
            }
        )
    )

    with pytest.raises(TranscriptionError) as raised:
        whisper._model("large-v3")

    assert "device='gpu'" in raised.value.cause
    assert "RESOLVE_MCP_WHISPER_DEVICE" in raised.value.fix
    assert raised.value.detail["device"] == "gpu"


def test_a_stock_config_that_cannot_load_is_told_about_the_install_not_the_settings(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """#128's own failure, on defaults: a missing CUDA runtime must not read as a typo."""

    def _refuse(name: str, device: str, compute_type: str) -> object:
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    monkeypatch.setattr(whisper, "_build", _refuse)
    monkeypatch.setattr(cuda, "prepare", lambda: ())

    with pytest.raises(TranscriptionError) as raised:
        whisper._model("large-v3")

    assert "uv sync --extra analysis" in raised.value.fix
    assert "RESOLVE_MCP_WHISPER_DEVICE" not in raised.value.fix


def test_a_missing_backend_still_says_it_is_missing_rather_than_blaming_the_device(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unavailable-backend error is the older, more specific one; shaping must not eat it."""

    def _absent(name: str, device: str, compute_type: str) -> object:
        raise TranscriberUnavailableError(cause="faster-whisper is not installed.")

    monkeypatch.setattr(whisper, "_build", _absent)
    monkeypatch.setattr(cuda, "prepare", lambda: ())

    with pytest.raises(TranscriberUnavailableError):
        whisper._model("large-v3")


def test_the_cuda_runtime_is_prepared_before_the_model_is_built(
    tmp_path: Path, recording_backend: type[_RecordingModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CTranslate2 loads cuBLAS at the first CUDA allocation — after this point is too late."""
    order: list[str] = []

    def _prepare() -> tuple[Path, ...]:
        order.append("prepare")
        return ()

    def _build(name: str, device: str, compute_type: str) -> object:
        order.append("build")
        return object()

    monkeypatch.setattr(cuda, "prepare", _prepare)
    monkeypatch.setattr(whisper, "_build", _build)

    whisper._model("large-v3")

    assert order == ["prepare", "build"]
