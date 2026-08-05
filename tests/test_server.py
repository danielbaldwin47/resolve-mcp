"""The MCP surface: which tools are registered, and how they present themselves."""

from __future__ import annotations

import asyncio

from resolve_mcp.server import build_server


def tool_names() -> set[str]:
    tools = asyncio.run(build_server().list_tools())
    return {tool.name for tool in tools}


def descriptions() -> dict[str, str]:
    tools = asyncio.run(build_server().list_tools())
    return {tool.name: tool.description or "" for tool in tools}


def test_registers_the_p1_session_tools() -> None:
    assert tool_names() == {
        "get_status",
        "list_projects",
        "open_project",
        "snapshot_project",
        "run_python",
    }


def test_every_tool_describes_itself() -> None:
    assert all(len(text) > 30 for text in descriptions().values())


def test_the_escape_hatch_steers_back_to_the_real_tools() -> None:
    assert "prefer" in descriptions()["run_python"].lower()
