"""The interpreter guard: refuse to attach where loading the library would crash.

The failure this prevents is a Windows access violation, not an exception — so the check
has to happen before the scripting library is loaded, and it has to be cheap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.config import Config
from resolve_mcp.errors import ResolveMcpError
from resolve_mcp.interpreter import ensure_supported, is_supported

REGISTERED = Path.cwd() / "registered-python"
STANDALONE = Path.cwd() / "uv" / "cpython-3.11-standalone"


def test_a_registered_install_can_attach() -> None:
    assert is_supported(REGISTERED, [REGISTERED], platform="win32") is True


def test_a_standalone_build_cannot() -> None:
    assert is_supported(STANDALONE, [REGISTERED], platform="win32") is False


def test_no_registered_python_at_all_is_not_supported() -> None:
    assert is_supported(STANDALONE, [], platform="win32") is False


def test_the_check_is_windows_only() -> None:
    assert is_supported(STANDALONE, [], platform="linux") is True


def test_refusing_says_which_interpreter_and_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("resolve_mcp.interpreter.is_supported", lambda: False)
    config = Config.from_env({})

    with pytest.raises(ResolveMcpError) as excinfo:
        ensure_supported(config)

    error = excinfo.value
    assert error.code == "unsupported_interpreter"
    assert "python.org" in error.fix
    assert error.detail["base_prefix"]


def test_the_bypass_env_var_lets_the_curious_retest_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("resolve_mcp.interpreter.is_supported", lambda: False)
    config = Config.from_env({"RESOLVE_MCP_ALLOW_ANY_PYTHON": "1"})

    ensure_supported(config)


def test_a_supported_interpreter_passes_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("resolve_mcp.interpreter.is_supported", lambda: True)

    ensure_supported(Config.from_env({}))
