"""Making the CUDA runtime that the ``analysis`` extra ships findable on Windows.

``uv sync --extra analysis`` installs the runtime as wheels (#128): the DLLs land under
``site-packages/nvidia/<package>/bin``, which is on nobody's search path. CTranslate2 —
faster-whisper's backend — then fails to resolve ``cublas64_12.dll`` at import and the
transcriber is broken on exactly the machines a GPU makes it useful on.

Three mechanisms were considered; what is known about each (#35's live pass, #128):

* Those directories on ``PATH`` **before the process starts** — verified working.
* ``os.add_dll_directory()`` on them — verified *not* working. CTranslate2's loader does
  not consult the added-directory list; it still failed on ``cublas64_12.dll``.
* Mutating ``os.environ["PATH"]`` in-process — a hypothesis. Python ≥3.8 loads extension
  modules with ``LOAD_LIBRARY_SEARCH_DEFAULT_DIRS``, which excludes ``PATH`` when the
  loader resolves an extension's dependencies, so it may fail the way the second did.

So the mechanism shipped here is the one that does not depend on the loader's search
order at all: preload each library by absolute path before faster-whisper is imported.
A DLL already in the process is matched by base name, so CTranslate2's own resolution
finds it whatever flags it uses. ``PATH`` is prepended too — it is free, it costs one
string, and it is what any child process would need — but nothing rests on it.

Preloading by path also carries ``LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR``, so each library's
own directory is searched for *its* dependencies. That is why the list below stops at
the entry points: cuDNN's multi-hundred-megabyte engine libraries are loaded by
``cudnn64_9`` itself, out of the directory we just loaded it from.

Nothing here can observe whether CUDA subsequently initialises — the same shape as ADR
0001's attach problem. The decisions (which directories, in what order, on which
platform) are asserted in the fake tier; the consequence belongs to the live smoke.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

from ..logging_config import get_logger

log = get_logger("analysis")

# In the order the loader would want them, and the order #128 records.
NVIDIA_PACKAGES = ("cublas", "cudnn", "cuda_nvrtc")

# Matched by pattern rather than by pinned name so a cuDNN 9 → 10 bump is an install, not
# a code change. Deliberately absent: cudnn_engines_* and cudnn_heuristic*, which cuDNN
# loads itself, and nvrtc-builtins, which nvrtc loads itself.
PRELOAD_PATTERNS = (
    "cublas64_*.dll",
    "cublasLt64_*.dll",
    "cudnn64_*.dll",
    "cudnn_adv*.dll",
    "cudnn_cnn*.dll",
    "cudnn_graph*.dll",
    "cudnn_ops*.dll",
    "nvrtc64_*.dll",
)

_prepared = False


def site_packages() -> Path:
    """Where this venv's wheels live — the root the nvidia layout hangs off."""
    return Path(sysconfig.get_paths()["purelib"])


def dll_directories(root: Path, platform: str | None = None) -> tuple[Path, ...]:
    """The nvidia ``bin`` directories present under ``root``, in load order.

    Empty off Windows: every other platform's loader finds these through the wheel's own
    ``RPATH``, and there is nothing to prepare.
    """
    if (platform or sys.platform) != "win32":
        return ()
    nvidia = Path(root) / "nvidia"
    candidates = (nvidia / package / "bin" for package in NVIDIA_PACKAGES)
    return tuple(one for one in candidates if one.is_dir())


def preloadable_libraries(root: Path, platform: str | None = None) -> tuple[Path, ...]:
    """Every runtime library worth loading up front, deduplicated, in a stable order."""
    found: list[Path] = []
    for directory in dll_directories(root, platform=platform):
        for pattern in PRELOAD_PATTERNS:
            found.extend(sorted(directory.glob(pattern)))
    return tuple(dict.fromkeys(found))


def prepare(
    root: Path | None = None,
    platform: str | None = None,
    load: Callable[[str], object] | None = None,
) -> tuple[Path, ...]:
    """Put the CUDA runtime within reach, once per process. Returns what actually loaded.

    A library that refuses to load is a warning, not a failure: a box with the wheels and
    no GPU should transcribe slowly on the CPU rather than not at all.
    """
    global _prepared
    if _prepared:
        return ()
    _prepared = True

    root = site_packages() if root is None else Path(root)
    directories = dll_directories(root, platform=platform)
    if not directories:
        log.debug("No bundled CUDA runtime under %s; leaving the loader alone", root)
        return ()

    _prepend_to_path(directories)
    loader = _load if load is None else load
    loaded: list[Path] = []
    for library in preloadable_libraries(root, platform=platform):
        try:
            loader(str(library))
        except OSError as exc:
            log.warning(
                "CUDA runtime library %s did not load (%s) — transcription will be slow "
                "or CPU-bound rather than broken",
                library.name,
                exc,
            )
            continue
        loaded.append(library)
    log.info(
        "CUDA runtime prepared: %d director(y/ies) prepended to PATH, %d librar(y/ies) preloaded",
        len(directories),
        len(loaded),
    )
    return tuple(loaded)


def reset_preparation() -> None:
    """Forget that preparation happened. For tests; the server prepares once and stays."""
    global _prepared
    _prepared = False


def _prepend_to_path(directories: tuple[Path, ...]) -> None:
    ahead = [str(one) for one in directories]
    behind = [one for one in os.environ.get("PATH", "").split(os.pathsep) if one not in ahead]
    os.environ["PATH"] = os.pathsep.join([*ahead, *behind])


def _load(path: str) -> object:
    """Load one DLL by absolute path, keeping it in the process for whoever needs it next."""
    import ctypes

    # WinDLL and CDLL load identically — the difference is the calling convention of the
    # functions inside, and nothing here calls one. CDLL is the spelling that type-checks
    # on the Linux runner as well.
    loader = getattr(ctypes, "WinDLL", ctypes.CDLL)
    return loader(path)
