"""Live smoke tier: run by hand against real DaVinci Resolve Studio.

Excluded from the default suite (``-m 'not live'``). Run it before a release and after a
Resolve upgrade, with Resolve running and a project open:

    uv run pytest -m live

Everything here goes through the real connection singleton — no fakes — so it is the only
place the direct-attach path itself is exercised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.tools.cut import build_timeline, validate_cut
from resolve_mcp.tools.escape_hatch import run_python
from resolve_mcp.tools.media import inspect_clip, list_media
from resolve_mcp.tools.project import get_status, list_projects, snapshot_project
from resolve_mcp.tools.timeline import inspect_timeline, list_timelines

pytestmark = pytest.mark.live

SMOKE_CUT = "resolve-mcp-smoke"
"""Every build here materialises a new version of this name; delete them when you are done."""


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


# --- build_timeline: the footgun wraps, on the only API that can confirm them --------------


def a_source_clip() -> dict[str, Any]:
    """A pool clip long enough to cut three shots out of, with a rate the cut can declare."""
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No media pool")
    for entry in listing["clips"]:
        clip = inspect_clip(entry["name"], bin=entry["bin"] or None)
        if not clip["ok"]:
            continue
        media = clip["bounds"]["media"]
        fps = clip["properties"].get("FPS")
        if media["duration"] is None or media["duration"]["frames"] < 200 or not fps:
            continue
        return {
            "name": entry["name"],
            "bin": entry["bin"] or None,
            "fps": float(fps),
            "start": media["start"]["frames"],
        }
    pytest.skip("No pool clip long enough to cut a smoke timeline from")


def a_smoke_cut(tmp_path: Path, source: dict[str, Any], durations: tuple[int, ...]) -> str:
    """A rough-cut shaped file (no master audio) built from one real clip."""
    at = source["start"]
    segments = []
    for index, length in enumerate(durations):
        segments.append(
            {
                "id": f"s{index:03d}",
                "source": "angle",
                "in": at,
                "out": at + length,
            }
        )
        at += length
    angle = {"clip": source["name"]}
    if source["bin"] is not None:
        angle["bin"] = source["bin"]
    doc = {
        "schema": 1,
        "timeline": {"name": SMOKE_CUT, "fps": source["fps"]},
        "sources": {"angle": angle},
        "segments": segments,
    }
    path = tmp_path / "smoke.cut.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_a_real_build_places_every_shot_exactly_where_the_cut_puts_it(tmp_path: Path) -> None:
    """The one check no fake can make: that Resolve honours the placement we send it.

    Four #18 footguns land here at once — an absolute recordFrame against a one-hour start
    timecode, mediaType travelling with trackIndex, exact endFrame durations, and no
    silent relocation — because every one of them shows up as a placement that is off.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    durations = (48, 24, 36)
    cut_file = a_smoke_cut(tmp_path, source, durations)
    assert validate_cut(cut_file)["valid"] is True

    result = build_timeline(cut_file)

    assert result["ok"] is True, result.get("error")
    assert result["placed"] == {"segments": 3, "audio": False}
    built = inspect_timeline(result["timeline"]["name"], detail="clips")
    assert built["ok"] is True
    video = [track for track in built["tracks"] if track["type"] == "video"][0]
    starts = [item["record"]["in"]["frames"] for item in video["items"]]
    timeline_start = built["timeline"]["start"]["frames"]
    assert [item["record"]["duration"]["frames"] for item in video["items"]] == list(durations)
    assert starts == [timeline_start, timeline_start + 48, timeline_start + 72]


def test_a_rebuild_makes_the_next_version_and_leaves_the_last_one_alone(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    first = build_timeline(a_smoke_cut(tmp_path, source, (48, 24, 36)))
    assert first["ok"] is True, first.get("error")

    second = build_timeline(a_smoke_cut(tmp_path, source, (60, 30)))

    assert second["ok"] is True, second.get("error")
    assert second["timeline"]["version"] == first["timeline"]["version"] + 1
    earlier = inspect_timeline(first["timeline"]["name"], detail="summary")
    assert earlier["timeline"]["duration"]["frames"] == 108


def test_an_invalid_cut_creates_no_timeline_on_a_real_project(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    cut_file = Path(a_smoke_cut(tmp_path, source, (48, 24, 36)))
    doc = json.loads(cut_file.read_text(encoding="utf-8"))
    doc["segments"][1]["out"] = doc["segments"][1]["in"]
    cut_file.write_text(json.dumps(doc), encoding="utf-8")
    before = {entry["name"] for entry in list_timelines()["timelines"]}

    result = build_timeline(str(cut_file))

    assert result["ok"] is False
    assert result["error"]["code"] == "cut_invalid"
    assert {entry["name"] for entry in list_timelines()["timelines"]} == before


def test_snapshot_writes_a_real_drp(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = snapshot_project(str(tmp_path / "smoke.drp"))

    assert result["ok"] is True
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    assert snapshot.stat().st_size > 0
