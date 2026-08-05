from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

from resolve_mcp import config as config_module
from resolve_mcp.config import Config
from resolve_mcp.resolve import connection as connection_module
from resolve_mcp.resolve.connection import ResolveConnection

from .fakes import FakeConnector, FakeResolve


class Attach(Protocol):
    def __call__(self, *handles: FakeResolve | None) -> FakeConnector: ...


@pytest.fixture(autouse=True)
def _clean_globals(tmp_path: Path) -> Iterator[None]:
    """Keep the connection and config singletons from leaking between tests."""
    connection_module.reset_connection()
    config_module.set_config(Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path / "cache")}))
    yield
    connection_module.reset_connection()
    config_module.reset_config()


@pytest.fixture
def attach() -> Attach:
    """Substitute the Resolve singleton with fakes, one per connect attempt."""

    def _attach(*handles: FakeResolve | None) -> FakeConnector:
        connector = FakeConnector(*handles)
        connection_module.set_connection(ResolveConnection(connector))
        return connector

    return _attach
