"""FastMCP app and tool registration. No logic lives here.

Everything below this file is callable without the MCP transport, which is what the test
suite does.
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import __version__
from .config import get_config
from .logging_config import configure_logging, get_logger
from .tools import escape_hatch, media, project

log = get_logger("server")

INSTRUCTIONS = """\
Hands inside DaVinci Resolve Studio. You bring the editorial judgement; this server does
the mechanical work.

Call get_status first to see what Resolve has open — every result echoes the current
project, timeline and fps, so watch that context for switches. Take a snapshot_project
backup before any big or risky operation. Failures come back as ok:false with a cause and
a fix; act on the fix rather than retrying blindly.
"""


def build_server() -> FastMCP:
    """Build the MCP app with the tool catalog registered."""
    mcp: FastMCP = FastMCP(
        name="resolve-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for fn in (*project.TOOLS, *media.TOOLS, *escape_hatch.TOOLS):
        mcp.tool(fn)
    return mcp


def main() -> None:
    """stdio entry point."""
    configure_logging(get_config())
    log.info("resolve-mcp %s starting on stdio", __version__)
    build_server().run(transport="stdio", show_banner=False)
