"""The style layer is the agent's, not the server's.

Story 59 of spec #22: the server never writes the director's cut files, style profiles or
angle sidecars. The profiles and sidecars are the half of that promise nothing else tests,
because the way it would break is not a failing assertion somewhere — it is a helpful
convenience landing in ``src/`` one day that reads ``styles/concert.md`` "just to check",
and from then on taste is server behaviour.

So this reads the source: no module names the style layer, no directory the server writes
to is inside it, and the one tool that consumes angle labels takes them as a mapping the
caller already lifted out of its own document rather than as a file it opens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from resolve_mcp.analysis import correlate
from resolve_mcp.config import Config
from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.resolve.connection import get_connection

SOURCE = Path(__file__).resolve().parent.parent / "src" / "resolve_mcp"

STYLE_LAYER = re.compile(
    r"""
      \bstyles [/\\]            # the directory itself, in a path
    | \bsidecars? [/\\]         # or a sidecar named as one
    | (?:base|concert) \.md     # or a profile by name
    """,
    re.VERBOSE | re.IGNORECASE,
)
"""What reading or writing the style layer would have to look like in source."""

STYLE_LAYER_WORDS = re.compile(r"style[_ ]?profile|sidecar|angle[_ ]?label", re.IGNORECASE)
"""The vocabulary — allowed in prose, never in the name of a directory the server owns."""

SOURCE_FILES = sorted(SOURCE.rglob("*.py"))

DIRECTORIES = (
    "cache_dir",
    "snapshot_dir",
    "listing_dir",
    "job_dir",
    "result_dir",
    "audio_dir",
    "stems_dir",
    "frame_dir",
    "analysis_dir",
    "render_dir",
    "interchange_dir",
)
"""Every directory the server writes into, by the name Config gives it."""


def test_the_source_tree_is_there_to_be_scanned() -> None:
    """A scan over nothing passes loudly, so the corpus of files is asserted first."""
    assert len(SOURCE_FILES) > 20


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda path: path.name)
def test_no_module_names_the_style_layer(path: Path) -> None:
    """No server module reaches for a profile or a sidecar by path."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        assert not STYLE_LAYER.search(line), f"{path.name}:{number} names the style layer: {line!r}"


def test_no_directory_the_server_writes_to_is_the_style_layer(tmp_path: Path) -> None:
    """Config is the whole list of places the server writes; none of them is ``styles/``."""
    config = Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path)})
    for name in DIRECTORIES:
        directory = str(getattr(config, name))
        assert not STYLE_LAYER.search(directory), f"config.{name} is inside the style layer"
        assert not STYLE_LAYER_WORDS.search(directory), f"config.{name} names the style layer"


def test_config_grew_no_directory_this_test_does_not_know_about(tmp_path: Path) -> None:
    """The list above is only a guard while it is the whole list."""
    config = Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path)})
    found = {name for name in dir(config) if name.endswith("_dir")}
    assert found == set(DIRECTORIES)


def test_correlate_timeline_refuses_a_sidecar_path_as_angles() -> None:
    """angles is labels already lifted out of the sidecar, never the sidecar to go and read."""
    with pytest.raises(InvalidRequestError) as raised:
        correlate.correlate_timeline(
            get_connection(),
            beats="beats.json",
            angles="styles/angles/sunset-set.json",  # type: ignore[arg-type]
        )

    assert "mapping" in raised.value.cause
