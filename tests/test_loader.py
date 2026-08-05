"""Locating the Resolve scripting module — the one part of attach that runs before Resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.config import Config
from resolve_mcp.errors import ResolveMcpError
from resolve_mcp.resolve import loader as loader_module
from resolve_mcp.resolve.loader import load_resolve

# What the real DaVinciResolveScript.py does: load the native library, then swap itself
# out of sys.modules for it. Anything that keeps the original module object ends up
# holding a shell with no scriptapp on it.
SELF_SWAPPING_MODULE = """
import sys, types
fusionscript = types.ModuleType("fusionscript")
fusionscript.scriptapp = lambda name: f"handle:{name}"
sys.modules[__name__] = fusionscript
"""

NO_SWAP_MODULE = "value = 1\n"


@pytest.fixture(autouse=True)
def _interpreter_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("resolve_mcp.interpreter.is_supported", lambda: True)
    loader_module.reset_module_cache()


def _scripting_dir(tmp_path: Path, source: str) -> Config:
    modules = tmp_path / "Scripting" / "Modules"
    modules.mkdir(parents=True)
    (modules / "DaVinciResolveScript.py").write_text(source)
    return Config.from_env({"RESOLVE_SCRIPT_API": str(tmp_path / "Scripting")})


def test_follows_the_module_swap_into_the_native_library(tmp_path: Path) -> None:
    config = _scripting_dir(tmp_path, SELF_SWAPPING_MODULE)

    assert load_resolve(config) == "handle:Resolve"


def test_the_native_library_is_imported_once_and_kept(tmp_path: Path) -> None:
    config = _scripting_dir(tmp_path, SELF_SWAPPING_MODULE)

    first = loader_module.scripting_module(config)
    second = loader_module.scripting_module(config)

    assert first is second


def test_a_module_that_never_reaches_the_library_is_a_clear_error(tmp_path: Path) -> None:
    config = _scripting_dir(tmp_path, NO_SWAP_MODULE)

    with pytest.raises(ResolveMcpError) as excinfo:
        load_resolve(config)

    assert "scriptapp" in excinfo.value.cause
    assert "RESOLVE_SCRIPT_LIB" in excinfo.value.fix


def test_a_missing_scripting_module_names_the_path_and_the_override(tmp_path: Path) -> None:
    config = Config.from_env({"RESOLVE_SCRIPT_API": str(tmp_path / "nowhere")})

    with pytest.raises(ResolveMcpError) as excinfo:
        load_resolve(config)

    error = excinfo.value
    assert error.code == "resolve_unavailable"
    assert "DaVinciResolveScript.py" in error.cause
    assert "RESOLVE_SCRIPT_API" in error.fix


def test_an_unsupported_interpreter_stops_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("resolve_mcp.interpreter.is_supported", lambda: False)
    config = _scripting_dir(tmp_path, "raise AssertionError('never imported')")

    with pytest.raises(ResolveMcpError) as excinfo:
        load_resolve(config)

    assert excinfo.value.code == "unsupported_interpreter"
