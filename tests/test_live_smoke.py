"""Live smoke tier: run by hand against real DaVinci Resolve Studio.

Excluded from the default suite (``-m 'not live'``). Run it before a release and after a
Resolve upgrade, with Resolve running and a project open:

    uv run pytest -m live

Everything here goes through the real connection singleton — no fakes — so it is the only
place the direct-attach path itself is exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.tools.escape_hatch import run_python
from resolve_mcp.tools.media import inspect_clip, list_media
from resolve_mcp.tools.project import get_status, list_projects, snapshot_project

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _requires_resolve() -> None:
    result = get_status()
    if not result["ok"]:
        pytest.skip(f"Resolve unreachable: {result['error']['cause']}")


def test_attaches_to_resolve_and_reports_a_version() -> None:
    context = get_status()["context"]

    assert context["connected"] is True
    assert context["resolve_version"]


def test_lists_real_projects() -> None:
    result = list_projects()

    assert result["ok"] is True
    assert isinstance(result["projects"], list)


def test_escape_hatch_reaches_the_real_scripting_api() -> None:
    result = run_python("resolve.GetProductName()")

    assert result["ok"] is True
    assert "Resolve" in (result["result"] or "")


def test_lists_the_real_media_pool() -> None:
    """Read-only. The mutating media ACs (import, relink) are run by hand — see the ticket."""
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = list_media()

    assert result["ok"] is True
    assert isinstance(result["clips"], list)


def test_inspects_a_real_clip_with_the_property_keys_the_wrappers_assume() -> None:
    """The one live check the fakes cannot make: that these key names exist at all."""
    listing = list_media()
    if not listing["ok"] or not listing["clips"]:
        pytest.skip("No clips in the media pool")
    first = listing["clips"][0]

    result = inspect_clip(first["name"], bin=first["bin"] or None)

    assert result["ok"] is True
    assert "File Path" in result["properties"]
    assert result["bounds"]["media"]["duration"] is not None


def test_snapshot_writes_a_real_drp(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = snapshot_project(str(tmp_path / "smoke.drp"))

    assert result["ok"] is True
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    assert snapshot.stat().st_size > 0
