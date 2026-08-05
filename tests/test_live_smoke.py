"""Live smoke tier: run by hand against real DaVinci Resolve Studio.

Excluded from the default suite (``-m 'not live'``). Run it before a release and after a
Resolve upgrade, with Resolve running and a project open:

    uv run pytest -m live

Everything here goes through the real connection singleton — no fakes — so it is the only
place the direct-attach path itself is exercised.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.tools.escape_hatch import run_python
from resolve_mcp.tools.media import inspect_clip, list_media
from resolve_mcp.tools.project import get_status, list_projects, snapshot_project
from resolve_mcp.tools.timeline import (
    export_timeline,
    import_timeline,
    inspect_timeline,
    list_timelines,
)

from . import otio

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


def test_lists_the_real_timelines() -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = list_timelines()

    assert result["ok"] is True
    assert isinstance(result["timelines"], list)
    for entry in result["timelines"]:
        assert entry["fps"]
        assert entry["duration"] is not None


def test_the_frame_math_holds_on_a_real_timeline() -> None:
    """The fakes prove the arithmetic; only Resolve proves the numbers fed into it.

    Every reported out point is derived from ``GetDuration`` rather than ``GetEnd`` — see
    the wrapper docstring — so this is where that derivation meets real timeline items:
    durations must close, shots must land inside the timeline, and the sync offset must be
    the difference the agent will plan against.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")

    whole = inspect_timeline(detail="summary")
    assert whole["ok"] is True
    ends_at = whole["timeline"]["end"]["frames"]

    # The timeline's own duration is the one number taken from GetEndFrame on trust (a
    # timeline has no duration getter). A Resolve that reported the last frame rather than
    # one past it shows up here as an off-by-one and nowhere else — so read the tail
    # rather than the whole cut, which on a real concert timeline would truncate and skip
    # this check exactly where it matters.
    tail = inspect_timeline(
        detail="clips", start=max(ends_at - 600, whole["timeline"]["start"]["frames"])
    )
    assert tail["ok"] is True
    assert tail["truncated"] is False, "the tail was still too long to check the end frame"
    outs = [item["record"]["out"]["frames"] for track in tail["tracks"] for item in track["items"]]
    if outs:
        assert ends_at == max(outs)

    result = inspect_timeline(detail="clips")
    assert result["ok"] is True
    for track in result["tracks"]:
        for item in track["items"]:
            record = item["record"]
            assert record["out"]["frames"] - record["in"]["frames"] == record["duration"]["frames"]
            assert record["out"]["frames"] <= ends_at
            if item["source"]["in"] is not None:
                assert (
                    item["sync_offset"]["frames"]
                    == record["in"]["frames"] - item["source"]["in"]["frames"]
                )


# --- interchange ---------------------------------------------------------------------------
#
# These two import timelines into the open project, so they leave "resolve-mcp smoke …"
# timelines behind — delete them after the run. Take a snapshot_project first if the open
# project is one you care about.


def _shape(name: str | None = None) -> dict[str, Any]:
    """The structure a round trip has to preserve: tracks, and the shots on each."""
    reading = inspect_timeline(timeline=name, detail="clips")
    assert reading["ok"] is True, reading.get("error")
    assert reading["truncated"] is False, "the timeline is too long to compare in one read"
    return {
        "duration": reading["timeline"]["duration"]["frames"],
        "tracks": {
            f"{track['type']}{track['index']}": [
                (item["record"]["in"]["frames"], item["record"]["duration"]["frames"])
                for item in track["items"]
            ]
            for track in reading["tracks"]
        },
    }


def _smoke_name(what: str) -> str:
    return f"resolve-mcp smoke {what} {datetime.now().strftime('%H%M%S')}"


def test_an_otio_round_trip_preserves_the_timeline_structure(tmp_path: Path) -> None:
    """AC: export to OTIO, import it back, and the cut is the same cut.

    The fakes prove the naming and the error shaping; only Resolve proves that what it
    wrote is what it reads back, which is the whole basis of the transition route.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")
    before = _shape()

    exported = export_timeline(path=str(tmp_path / "round-trip.otio"))
    assert exported["ok"] is True, exported.get("error")
    assert exported["export_type"] == "EXPORT_OTIO"

    imported = import_timeline(exported["path"], name=_smoke_name("round-trip"))
    assert imported["ok"] is True, imported.get("error")

    after = _shape(imported["timeline"]["name"])
    assert after["duration"] == before["duration"]
    for track, shots in before["tracks"].items():
        assert after["tracks"].get(track) == shots, f"{track} came back differently"


def test_a_hand_injected_dissolve_imports_as_a_transition(tmp_path: Path) -> None:
    """AC: a dissolve edited into an exported OTIO comes back as a real transition.

    Transitions are the wall this route exists to get around, and the scripting API has no
    getter for them — so what is asserted here is that Resolve accepted the edited document
    and rebuilt the cut from it. **The dissolve and the fade to black themselves are
    confirmed by eye in the Resolve GUI on the imported timeline**, and the result goes on
    the ticket; a run that stops at green here has verified half the AC.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")

    exported = export_timeline(path=str(tmp_path / "injected.otio"))
    assert exported["ok"] is True, exported.get("error")
    document = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))

    tracks = otio.video_tracks(document)
    if not tracks:
        pytest.skip("The open timeline has no video track to inject into")
    dissolved = any(otio.inject_dissolve(track, frames=6) for track in tracks)
    faded = otio.inject_fade_to_black(tracks[0], frames=6)
    if not dissolved:
        pytest.skip("No cut between two clips long enough to carry a dissolve")
    Path(exported["path"]).write_text(json.dumps(document, indent=2), encoding="utf-8")

    imported = import_timeline(exported["path"], name=_smoke_name("dissolve"))

    assert imported["ok"] is True, imported.get("error")
    assert faded, "the fade-to-black half of the AC was not injected — check by eye anyway"
    landed = _shape(imported["timeline"]["name"])
    assert landed["tracks"], "the injected document imported as an empty timeline"


def test_the_interchange_formats_all_write_a_file(tmp_path: Path) -> None:
    """AC: export to OTIO, FCPXML and DRT. Read-only — nothing is imported back."""
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")

    for export_format in ("otio", "fcpxml", "drt"):
        result = export_timeline(format=export_format, path=str(tmp_path / "export"))

        assert result["ok"] is True, result.get("error")
        assert Path(result["path"]).stat().st_size == result["bytes"] > 0


def test_snapshot_writes_a_real_drp(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = snapshot_project(str(tmp_path / "smoke.drp"))

    assert result["ok"] is True
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    assert snapshot.stat().st_size > 0
