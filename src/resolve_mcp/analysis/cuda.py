"""Making the CUDA runtime that the ``analysis`` extra ships findable on Windows.

``uv sync --extra analysis`` installs the runtime as wheels (#128): the DLLs land under
``site-packages/nvidia/<package>/bin``, which is on nobody's search path. CTranslate2 —
faster-whisper's backend — loads them by bare name the first time a model touches the
GPU, and without help fails with ``Library cublas64_12.dll is not found or cannot be
loaded``. Note *when*: not at import, but at the first CUDA allocation, which is why the
preparation has to be in place before the model is built rather than merely before the
backend is imported.

Measured on the live box (RTX, CUDA 12.9 wheels, ctranslate2 4.8.1, faster-whisper 1.2.1)
by building ``tiny`` on ``device="cuda"`` under each candidate mechanism in turn:

===========================  ======================================================
``PATH`` prepended in-proc   **works** — the mechanism shipped here
``ctypes.WinDLL`` preload    works, by absolute path; the fallback if the above ever
                             stops (it does not depend on the loader's search order)
``os.add_dll_directory()``   fails — ``cublas64_12.dll`` still not found (also #35)
nothing at all               fails — this is the bug #128 was filed for
===========================  ======================================================

``PATH`` wins on cost rather than on principle: preloading means mapping ~1.4 GiB of DLLs
(``cublasLt64_12.dll`` alone is 668 MB) into every job, including on a box that will
transcribe on the CPU and never touch one of them. That CTranslate2 reads ``PATH`` at all
is what the table above establishes — Python ≥3.8 excludes ``PATH`` when *it* resolves an
extension module's dependencies, but CTranslate2 does its own ``LoadLibrary`` at runtime,
which is the plain Windows search order. The same fact explains why ``add_dll_directory``
does nothing here: that list is consulted for the flag-controlled search, not this one.

Nothing here can observe whether CUDA subsequently initialises — the same shape as ADR
0001's attach problem. The decisions (which directories, in what order, on which
platform) are asserted in the fake tier; the consequence belongs to the live smoke.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

from ..logging_config import get_logger

log = get_logger("analysis")

# Installed by nvidia-cublas-cu12 and nvidia-cudnn-cu12; cuda_nvrtc rides in as a
# dependency of cuBLAS (checked in its wheel metadata, not assumed). Listed in the order
# the runtime layers on top of itself — and each one is still filtered on existing, so a
# runtime that stops shipping one of them degrades to "not found" rather than to a crash.
NVIDIA_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc")

_prepared = False


def _site_packages() -> Path:
    """Where this venv's wheels live — the root the nvidia layout hangs off."""
    return Path(sysconfig.get_paths()["purelib"])


def dll_directories(root: Path, platform: str | None = None) -> tuple[Path, ...]:
    """The nvidia ``bin`` directories present under ``root``, in load order.

    Empty off Windows: every other platform's loader finds these through the wheel's own
    ``RPATH``, and there is nothing to prepare. Empty too when the wheels are simply not
    installed — a CPU-only install is a supported one.
    """
    if (platform or sys.platform) != "win32":
        return ()
    nvidia = Path(root) / "nvidia"
    candidates = (nvidia / package / "bin" for package in NVIDIA_PACKAGES)
    return tuple(one for one in candidates if one.is_dir())


def prepare(root: Path | None = None, platform: str | None = None) -> tuple[Path, ...]:
    """Put the bundled CUDA runtime on the search path, once per process.

    Returns the directories prepended — empty when there is nothing to do, and empty on
    every call after the first, so a second job does not lengthen ``PATH`` again.
    """
    global _prepared
    if _prepared:
        return ()
    _prepared = True

    root = _site_packages() if root is None else Path(root)
    directories = dll_directories(root, platform=platform)
    if not directories:
        log.debug("No bundled CUDA runtime under %s; leaving the search path alone", root)
        return ()

    ahead = [str(one) for one in directories]
    behind = [one for one in os.environ.get("PATH", "").split(os.pathsep) if one not in ahead]
    os.environ["PATH"] = os.pathsep.join([*ahead, *behind])
    log.info("CUDA runtime on the search path: %s", os.pathsep.join(ahead))
    return directories


def reset_preparation() -> None:
    """Forget that preparation happened. For tests; the server prepares once and stays."""
    global _prepared
    _prepared = False
