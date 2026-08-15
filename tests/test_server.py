"""The MCP surface: which tools are registered, and how they present themselves."""

from __future__ import annotations

import asyncio
from typing import Any

from resolve_mcp.server import build_server


def tool_names() -> set[str]:
    tools = asyncio.run(build_server().list_tools())
    return {tool.name for tool in tools}


def descriptions() -> dict[str, str]:
    tools = asyncio.run(build_server().list_tools())
    return {tool.name: tool.description or "" for tool in tools}


def test_registers_the_p1_session_media_timeline_cut_titling_render_and_job_tools() -> None:
    assert tool_names() == {
        "get_status",
        "list_projects",
        "load_project",
        "snapshot_project",
        "import_media",
        "list_media",
        "inspect_clip",
        "set_clip_metadata",
        "organize_media",
        "relink_media",
        "list_timelines",
        "inspect_timeline",
        "list_markers",
        "set_markers",
        "export_timeline",
        "import_timeline",
        "get_cut_schema",
        "validate_cut",
        "virtual_transcript",
        "build_timeline",
        "get_titles_schema",
        "validate_titles",
        "apply_titles",
        "list_titles",
        "edit_title",
        "swap_take",
        "grab_frames",
        "detect_scene_cuts",
        "analyze_occlusion",
        "analyze_quality",
        "transcribe_audio",
        "analyze_music",
        "analyze_structure",
        "detect_drum_fills",
        "detect_phrases",
        "detect_bars",
        "correlate_timeline",
        "separate_stems",
        "list_render_presets",
        "render_timeline",
        "get_job",
        "list_jobs",
        "run_python",
    }


def test_every_tool_describes_itself() -> None:
    assert all(len(text) > 30 for text in descriptions().values())


def test_the_escape_hatch_steers_back_to_the_real_tools() -> None:
    assert "prefer" in descriptions()["run_python"].lower()


def schemas() -> dict[str, dict[str, Any]]:
    tools = asyncio.run(build_server().list_tools())
    return {tool.name: tool.parameters for tool in tools}


def test_the_injected_connection_never_reaches_the_transport() -> None:
    """The decorator hands the connection in (#229); the agent must never see the parameter."""
    offenders = {
        name: schema
        for name, schema in schemas().items()
        if "connection" in (schema.get("properties") or {})
        or "connection" in (schema.get("required") or [])
    }

    assert offenders == {}


def test_a_tool_s_own_parameters_survive_the_injection() -> None:
    """Stripping one parameter must not strip the ones the agent is supposed to fill in."""
    assert schemas()["load_project"]["required"] == ["name"]
    assert set(schemas()["grab_frames"]["properties"]) >= {"clip", "times", "max_edge"}


def test_python_m_entry_is_the_server_main() -> None:
    """``python -m resolve_mcp`` runs ``server.main`` and nothing of its own."""
    import importlib

    from resolve_mcp import server

    entry = importlib.import_module("resolve_mcp.__main__")

    assert entry.main is server.main


def test_logging_goes_to_stderr_only() -> None:
    """stdout is the MCP transport; every handler the server installs writes to stderr."""
    import logging
    import sys

    from resolve_mcp.logging_config import LOGGER_NAME, configure_logging, get_logger

    logger = configure_logging()
    child = get_logger("probe")

    assert logger.name == LOGGER_NAME
    assert child.name == f"{LOGGER_NAME}.probe"
    assert logger.propagate is False
    assert logger.handlers
    assert all(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr for h in logger.handlers
    )
    assert configure_logging() is logger and len(logger.handlers) == 1
