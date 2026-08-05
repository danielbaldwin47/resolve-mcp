"""Which Python interpreters can attach to Resolve.

``fusionscript.dll`` resolves the Python C API at runtime rather than importing a
``pythonXX.dll`` — and under a redistributable standalone interpreter (the kind uv
installs and manages) that lookup ends in a Windows access violation that takes the whole
process down. Measured on this machine, Resolve 21.0.3:

* python.org 3.12 (a registered PEP 514 install) — attaches, ``scriptapp("Resolve")`` works
* uv-managed 3.11 and 3.13 (python-build-standalone) — hard crash, no catchable exception

A crash is not something the connection manager can recover from, so the interpreter is
checked *before* the module is ever loaded. The registry is the discriminator that
matched the measurement: a PEP 514-registered install works, an unregistered standalone
build does not.

``RESOLVE_MCP_ALLOW_ANY_PYTHON=1`` bypasses the check for anyone who wants to retest it.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from .config import BYPASS_ENV, Config, get_config
from .errors import UnsupportedInterpreterError
from .logging_config import get_logger

log = get_logger("interpreter")

FIX = (
    "Run this server on a python.org (or otherwise PEP 514-registered) CPython install "
    "rather than a uv-managed standalone build: install CPython 3.11 or 3.12 x64 from "
    "python.org, then recreate the venv against it "
    "(uv venv --python <path-to-python.exe>). "
    f"Set {BYPASS_ENV}=1 to bypass this check."
)


def registered_install_paths() -> list[Path]:
    """Install paths of every PEP 514-registered CPython on this machine."""
    if sys.platform != "win32":
        return []
    import winreg

    paths: list[Path] = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            core = winreg.OpenKey(root, r"SOFTWARE\Python\PythonCore")
        except OSError:
            continue
        with core:
            for index in range(winreg.QueryInfoKey(core)[0]):
                tag = winreg.EnumKey(core, index)
                try:
                    with winreg.OpenKey(core, rf"{tag}\InstallPath") as key:
                        paths.append(Path(str(winreg.QueryValueEx(key, "")[0])))
                except OSError:
                    continue
    return paths


def is_supported(
    base_prefix: str | Path | None = None,
    registered: Iterable[Path] | None = None,
    platform: str | None = None,
) -> bool:
    """Whether the running interpreter can load the Resolve scripting library safely."""
    if (platform or sys.platform) != "win32":
        return True
    prefix = Path(base_prefix or sys.base_prefix)
    known = list(registered) if registered is not None else registered_install_paths()
    return any(_same_path(prefix, candidate) for candidate in known)


def ensure_supported(config: Config | None = None) -> None:
    """Raise before loading the scripting library on an interpreter that would crash."""
    config = config or get_config()
    if config.allow_any_python or is_supported():
        return
    raise UnsupportedInterpreterError(
        cause=(
            f"The interpreter at {sys.base_prefix} is not a registered CPython install. "
            "Loading the Resolve scripting library from a standalone build crashes the "
            "process outright (Windows access violation)."
        ),
        fix=FIX,
        detail={"base_prefix": sys.base_prefix, "python_version": sys.version.split()[0]},
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:  # a registry entry can point at a path that no longer exists
        return False
