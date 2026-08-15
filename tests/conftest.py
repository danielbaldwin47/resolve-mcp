from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

from resolve_mcp import config as config_module
from resolve_mcp import ffmpeg as ffmpeg_module
from resolve_mcp.analysis import device as device_module
from resolve_mcp.config import Config
from resolve_mcp.resolve import connection as connection_module
from resolve_mcp.resolve.connection import ResolveConnection
from resolve_mcp.video import ffmpeg as video_ffmpeg_module

from .fakes import FakeConnector, FakeResolve


class Attach(Protocol):
    def __call__(self, *handles: FakeResolve | None) -> FakeConnector: ...


@pytest.fixture(autouse=True)
def _clean_globals(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[None]:
    """Keep the connection and config singletons from leaking between tests.

    The fake tier gets a hermetic config — machine-local ``RESOLVE_MCP_*`` variables
    must not change what a unit test asserts. Live tests get the real process env:
    the documented overrides (``RESOLVE_MCP_AUDIO_SEPARATOR``, ``RESOLVE_MCP_DRUM_MODEL``,
    …) exist precisely so a live run can point at the machine's installs, and a
    conftest that erased them made those knobs silently dead in the live tier.
    Both tiers still get their cache redirected into ``tmp_path``.
    """
    connection_module.reset_connection()
    base = dict(os.environ) if request.node.get_closest_marker("live") else {}
    config_module.set_config(
        Config.from_env({**base, "RESOLVE_MCP_CACHE": str(tmp_path / "cache")})
    )
    ffmpeg_module.reset_hwaccel_probe()
    video_ffmpeg_module.reset_decode_announcements()
    device_module.reset_announcements()
    yield
    connection_module.reset_connection()
    config_module.reset_config()
    ffmpeg_module.reset_hwaccel_probe()
    video_ffmpeg_module.reset_decode_announcements()
    device_module.reset_announcements()


@pytest.fixture
def machine_cache() -> Config:
    """The machine's own cache, for the live tests that are about what is already in it.

    ``_clean_globals`` redirects every test's cache into ``tmp_path``, which is right for every
    test that makes its own inputs: nothing one writes can be read by the next. A live test over
    the director's separated stems has the opposite need — the directory it is about is keyed on
    a gigabyte of audio's own bytes and exists in exactly one place, and a temporary copy of it
    under a temporary key would be a different question wearing the same shape.

    Declared here, beside the redirect, so the exception is part of the contract rather than a
    config a test quietly re-derives. It is never autouse: a test asking for this is asking to
    read and write the real cache, and that has to be visible in its signature.
    """
    return Config.from_env(dict(os.environ))


@pytest.fixture
def attach() -> Attach:
    """Substitute the Resolve singleton with fakes, one per connect attempt."""

    def _attach(*handles: FakeResolve | None) -> FakeConnector:
        connector = FakeConnector(*handles)
        connection_module.set_connection(ResolveConnection(connector))
        return connector

    return _attach
