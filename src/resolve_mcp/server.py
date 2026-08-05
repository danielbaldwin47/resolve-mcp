"""FastMCP app and tool registration. No logic lives here.

Everything below this file is callable without the MCP transport, which is what the test
suite does.
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import __version__
from .config import get_config
from .logging_config import configure_logging, get_logger
from .tools import cut, escape_hatch, jobs, media, project, timeline, video

log = get_logger("server")

INSTRUCTIONS = """\
Hands inside DaVinci Resolve Studio. You bring the editorial judgement; this server does
the mechanical work.

Call get_status first to see what Resolve has open — every result echoes the current
project, timeline and fps, so watch that context for switches. Take a snapshot_project
backup before any big or risky operation. Failures come back as ok:false with a cause and
a fix; act on the fix rather than retrying blindly.

Time is frames-first: every position comes back as frames, seconds, timecode and fps
together, ranges are half-open [in, out), and a time you give in seconds must say how to
snap it to a frame ({"seconds": 2.52, "snap": "floor"}).

Editing is declarative: you author a cut file, the server builds it. Call get_cut_schema
before writing one and validate_cut after every edit — do not guess the format.

When the audio evidence is ambiguous, look: grab_frames writes JPEGs you can read at any
moment on any angle, and detect_scene_cuts catalogs where a piece of b-roll changes shot.

Heavy work (renders, analysis) hands back a job_id straight away — carry on working and
poll it with get_job; list_jobs finds what you started before a restart. Results are cached
against the media and the parameters, so an unchanged rerun comes back instantly.
"""


def build_server() -> FastMCP:
    """Build the MCP app with the tool catalog registered."""
    mcp: FastMCP = FastMCP(
        name="resolve-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for fn in (
        *project.TOOLS,
        *media.TOOLS,
        *timeline.TOOLS,
        *cut.TOOLS,
        *video.TOOLS,
        *jobs.TOOLS,
        *escape_hatch.TOOLS,
    ):
        mcp.tool(fn)
    return mcp


def main() -> None:
    """stdio entry point."""
    configure_logging(get_config())
    log.info("resolve-mcp %s starting on stdio", __version__)
    build_server().run(transport="stdio", show_banner=False)
