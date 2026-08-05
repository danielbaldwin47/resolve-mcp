"""Live smoke tier: run by hand against real DaVinci Resolve Studio.

Excluded from the default suite (``-m 'not live'``). Run it before a release and after a
Resolve upgrade, with Resolve running and a project open:

    uv run pytest -m live

Everything here goes through the real connection singleton — no fakes — so it is the only
place the direct-attach path itself is exercised.

Every test here is read-only except the Text+ probe, which creates and then deletes a
scratch bin and timeline in the open project. It stays skipped until you opt in by
pointing ``RESOLVE_MCP_TEXTPLUS_TEMPLATE`` at a ``.drb`` you exported from the GUI —
in PowerShell, which is the shell on the machine this runs on:

    $env:RESOLVE_MCP_TEXTPLUS_TEMPLATE = 'C:\\titles\\Titles.drb'
    uv run pytest -m live -s
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.audio.acquire import acquire_timeline_audio
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools.cut import build_timeline, validate_cut
from resolve_mcp.tools.escape_hatch import run_python
from resolve_mcp.tools.jobs import get_job, list_jobs
from resolve_mcp.tools.media import inspect_clip, list_media
from resolve_mcp.tools.project import get_status, list_projects, snapshot_project
from resolve_mcp.tools.timeline import (
    export_timeline,
    import_timeline,
    inspect_timeline,
    list_markers,
    list_timelines,
)
from resolve_mcp.tools.video import grab_frames

from . import otio
from .text_plus_probe import TEMPLATE_ENV, probe_template_append

pytestmark = pytest.mark.live

SMOKE_CUT = "resolve-mcp-smoke"
"""Every build here materialises a new version of this name; delete them when you are done."""

LOCKED_TRACK_PROBE = """
pool = project.GetMediaPool()
timeline = pool.CreateEmptyTimeline("resolve-mcp-lock-probe")
project.SetCurrentTimeline(timeline)
timeline.SetTrackLock("video", 1, True)
clips = [c for c in pool.GetRootFolder().GetClipList() if c.GetName() == {name}]
returned = pool.AppendToTimeline([
    {{"mediaPoolItem": clips[0], "startFrame": {start}, "endFrame": {end},
      "mediaType": 1, "trackIndex": 1, "recordFrame": timeline.GetStartFrame()}}
]) if clips else None
result = {{
    "found": bool(clips),
    "returned_truthy": bool(returned),
    "items_on_track": len(timeline.GetItemListInTrack("video", 1) or []),
}}
"""
"""Spike #18 (d), reduced to its claim: a locked track reports a placement it did not make."""


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


def test_hand_placed_markers_read_back_on_the_clock_the_agent_plans_in() -> None:
    """The one thing no fake can settle: which frame Resolve keys a GUI marker by.

    The wrapper takes ``GetMarkers`` keys as relative to the timeline start and adds the
    start frame to reach a record frame. If that assumption is wrong the addition happens
    twice, and every marker on a timeline starting at 01:00:00:00 lands an hour past the
    end — which is exactly what the bounds check below catches. On a timeline starting at
    frame 0 the two clocks coincide and nothing is proved, so that case skips rather than
    passing emptily.

    Place a marker by hand in the GUI over a known shot first, then read the printed table
    against what the GUI shows: colour, name and note are the agent's work queue, and only
    a human at the machine can confirm they came back as typed.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")

    result = list_markers()
    assert result["ok"] is True
    if not result["markers"]:
        pytest.skip("No markers on the open timeline — add one in the GUI and rerun")

    bounds = result["timeline"]
    if bounds["start"]["frames"] == 0:
        pytest.skip("Timeline starts at frame 0, where both marker clocks agree — use 01:00:00:00")

    for marker in result["markers"]:
        print(  # noqa: T201 - the human at the machine compares this against the GUI
            f"{marker['record']['timecode']} {marker['color']:<10} "
            f"{marker['name']!r} {marker['note']!r}"
        )
        assert bounds["start"]["frames"] <= marker["record"]["frames"] < bounds["end"]["frames"]
        assert marker["end"]["frames"] > marker["record"]["frames"]
        assert marker["color"]


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


def test_a_still_lands_at_the_duration_the_cut_asked_for(tmp_path: Path) -> None:
    """The one-time Out write, end to end: without it every still is 120 frames (#18 (a))."""
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    listing = list_media()
    stills = [
        entry
        for entry in listing["clips"]
        if Path(str(entry.get("file_path") or "")).suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    if not stills:
        pytest.skip("No still image in the media pool")
    still = stills[0]
    fps = get_status()["context"]["fps"] or 24.0
    doc = {
        "schema": 1,
        "timeline": {"name": SMOKE_CUT, "fps": fps},
        "sources": {"still": {"clip": still["name"]}},
        "segments": [{"id": "s000", "source": "still", "in": 0, "out": 90}],
    }
    path = tmp_path / "still.cut.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = build_timeline(str(path))

    assert result["ok"] is True, result.get("error")
    built = inspect_timeline(result["timeline"]["name"], detail="clips")
    video = [track for track in built["tracks"] if track["type"] == "video"][0]
    assert [item["record"]["duration"]["frames"] for item in video["items"]] == [90]


def test_the_locked_track_footgun_is_still_real(tmp_path: Path) -> None:
    """The spike's (d) probe, kept alive: an append onto a locked track reports success.

    build_timeline cannot reach this through its own tools — it always creates a fresh,
    unlocked timeline — so the guard it carries rests on this API behaviour rather than on
    anything a fake can prove. If Resolve ever fixes it, this test is where that shows up,
    and the E11 check can go.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    probe = run_python(
        LOCKED_TRACK_PROBE.format(
            name=repr(source["name"]),
            start=source["start"],
            end=source["start"] + 48,
        )
    )

    assert probe["ok"] is True, probe.get("error")
    if not probe["result"]["found"]:
        pytest.skip("The chosen clip is not in the root folder, so the probe could not run")
    assert probe["result"]["returned_truthy"] is True
    assert probe["result"]["items_on_track"] == 0


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


def test_the_render_queue_exports_the_real_timeline_mix() -> None:
    """The AC no seam can check: that these render-settings keys are the ones Resolve takes.

    The fakes prove the job machinery, the caching and the failure shaping. Whether
    ``SetRenderSettings`` accepts ``ExportVideo``/``AudioBitDepth``/``AudioSampleRate`` under
    those names, and whether an audio-only wav/lpcm job renders at all, is only answerable
    here. Slow: this renders the open timeline's audio in full.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")

    started = acquire_timeline_audio(get_connection())
    record = wait_for(started["job_id"], timeout=1800.0)

    assert record.state == "completed", record.error
    assert record.result is not None
    exported = Path(record.result["path"])
    assert exported.exists()
    assert exported.stat().st_size > 0
    assert record.result["sample_rate"] == 48_000
    assert record.result["bit_depth"] == 24
    assert (record.result["duration_seconds"] or 0) > 0

    polled = get_job(started["job_id"])
    assert polled["ok"] is True
    assert polled["job"]["state"] == "completed"
    assert started["job_id"] in [one["job_id"] for one in list_jobs()["jobs"]]

    again = acquire_timeline_audio(get_connection())
    assert again["cached"] is True, "an unchanged timeline must be a cache hit"


def test_a_real_frame_grab_lands_on_the_moment_resolve_numbers_it_at() -> None:
    """The AC no seam can check: that Start really is the offset between the two clocks.

    The fakes prove the command shape, the cap and the caching against a clip whose Start
    this test wrote itself. Whether real footage — an hour-based start timecode, a codec
    ffmpeg has to seek inside — is numbered the way the wrapper assumes only shows up here.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No project open in Resolve")
    footage = next(
        (one for one in listing["clips"] if one["fps"] and not one["offline"] and one["frames"]),
        None,
    )
    if footage is None:
        pytest.skip("No online clip with a frame rate in the media pool")
    bounds = inspect_clip(footage["name"], bin=footage["bin"] or None)["bounds"]["media"]
    middle = bounds["in"]["frames"] + bounds["duration"]["frames"] // 2

    result = grab_frames(footage["name"], [middle], bin=footage["bin"] or None)

    assert result["ok"] is True, result.get("error")
    grabbed = result["frames"][0]
    assert Path(grabbed["path"]).exists()
    assert grabbed["time"]["frames"] == middle
    assert max(grabbed["width"], grabbed["height"]) <= 1568

    again = grab_frames(footage["name"], [middle], bin=footage["bin"] or None)
    assert again["cached"] is True, "unchanged media must be a cache hit"


def test_snapshot_writes_a_real_drp(tmp_path: Path) -> None:
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    result = snapshot_project(str(tmp_path / "smoke.drp"))

    assert result["ok"] is True
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    assert snapshot.stat().st_size > 0


def test_the_text_plus_template_route_survives_a_drb_round_trip() -> None:
    """#41: the one question no fake can answer — does Resolve give each placed instance
    of a GUI-authored Text+ template its own text?

    The whole titling design downstream assumes yes. This mutates the open project (a
    scratch bin and a scratch timeline, both deleted again), so it stays skipped until
    the template path is set; run it on a throwaway project the first time.

    Paste the printed report onto the ticket — that is the record the ticket asks for.
    """
    exported = os.environ.get(TEMPLATE_ENV, "").strip()
    if not exported:
        pytest.skip(f"Set {TEMPLATE_ENV} to a .drb holding one GUI-authored Text+ template")
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    report = probe_template_append(get_connection(), Path(exported))

    print("\n" + report.render())
    assert report.per_instance_text, report.render()
    assert report.cleaned_up, report.render()
