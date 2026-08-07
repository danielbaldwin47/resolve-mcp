"""What the transcriber decides before a single sample is read: device, precision, DLLs.

The decisions are testable; the consequence is not. Which directories go on the search
path, in what order, on which platform, and which libraries are preloaded by absolute
path — all of that is a pure function of the venv layout and asserts here without a GPU.
Whether CUDA then initialises is invisible at every seam (the same shape as ADR 0001's
attach), so it belongs to the live smoke and nowhere else.
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

CUBLAS = ("cublas64_12.dll", "cublasLt64_12.dll")
CUDNN = (
    "cudnn64_9.dll",
    "cudnn_adv64_9.dll",
    "cudnn_cnn64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_ops64_9.dll",
    # The two that are hundreds of megabytes each. cudnn64_9 loads them itself, out of
    # its own directory, so preloading them would cost the memory for nothing.
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_heuristic64_9.dll",
)
NVRTC = ("nvrtc64_120_0.dll", "nvrtc-builtins64_120.dll")


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
    tmp_path: Path, platform: str
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)

    assert cuda.dll_directories(site, platform=platform) == ()
    assert cuda.preloadable_libraries(site, platform=platform) == ()


def test_the_libraries_are_matched_by_pattern_so_a_version_bump_is_not_a_code_change(
    tmp_path: Path,
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)

    names = [one.name for one in cuda.preloadable_libraries(site, platform="win32")]

    assert names == [
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudnn64_9.dll",
        "cudnn_adv64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_graph64_9.dll",
        "cudnn_ops64_9.dll",
        "nvrtc64_120_0.dll",
    ]


def test_preparation_prepends_the_directories_and_preloads_by_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)
    monkeypatch.setenv("PATH", r"C:\Windows\system32")
    loaded: list[str] = []

    prepared = cuda.prepare(site, platform="win32", load=loaded.append)

    assert prepared == cuda.preloadable_libraries(site, platform="win32")
    assert loaded == [str(one) for one in prepared]
    assert all(one.is_absolute() for one in prepared)

    search = os.environ["PATH"].split(os.pathsep)
    assert search == [
        str(one) for one in cuda.dll_directories(site, platform="win32")
    ] + [r"C:\Windows\system32"]


def test_a_second_preparation_neither_reloads_nor_lengthens_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _site_packages(tmp_path, cublas=CUBLAS, cudnn=CUDNN, cuda_nvrtc=NVRTC)
    monkeypatch.setenv("PATH", r"C:\Windows\system32")
    loaded: list[str] = []

    cuda.prepare(site, platform="win32", load=loaded.append)
    once = os.environ["PATH"]
    assert cuda.prepare(site, platform="win32", load=loaded.append) == ()

    assert len(loaded) == 8
    assert os.environ["PATH"] == once


def test_a_library_that_refuses_to_load_is_logged_rather_than_failing_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine with no GPU has the wheels and cannot load them; that is 'slow', not 'broken'."""
    site = _site_packages(tmp_path, cublas=CUBLAS)
    monkeypatch.setenv("PATH", r"C:\Windows\system32")

    def _refuse(path: str) -> None:
        raise OSError("[WinError 126] The specified module could not be found")

    assert cuda.prepare(site, platform="win32", load=_refuse) == ()


def test_no_nvidia_wheels_at_all_is_a_quiet_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", r"C:\Windows\system32")
    loaded: list[str] = []

    assert cuda.prepare(tmp_path, platform="win32", load=loaded.append) == ()
    assert loaded == []


def test_the_device_and_precision_default_to_the_backends_own_choice() -> None:
    config = Config.from_env({})

    assert config.whisper_device == "auto"
    assert config.whisper_compute_type == "default"


def test_the_device_and_precision_are_overridable_for_a_box_without_a_gpu() -> None:
    config = Config.from_env(
        {
            "RESOLVE_MCP_WHISPER_DEVICE": "cpu",
            "RESOLVE_MCP_WHISPER_COMPUTE_TYPE": "float32",
        }
    )

    assert config.whisper_device == "cpu"
    assert config.whisper_compute_type == "float32"


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


def test_the_cuda_runtime_is_prepared_before_the_backend_is_imported(
    tmp_path: Path, recording_backend: type[_RecordingModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    def _prepare() -> tuple[Path, ...]:
        order.append("prepare")
        return ()

    def _build(name: str, device: str, compute_type: str) -> object:
        order.append("import")
        return object()

    monkeypatch.setattr(cuda, "prepare", _prepare)
    monkeypatch.setattr(whisper, "_build", _build)

    whisper._model("large-v3")

    assert order == ["prepare", "import"]
