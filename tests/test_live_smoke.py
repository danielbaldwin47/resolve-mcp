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

import ast
import importlib.util
import json
import os
import shutil
import zlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from resolve_mcp.audio import separator
from resolve_mcp.audio.acquire import acquire_timeline_audio
from resolve_mcp.audio.stems import (
    DRUM_STEMS,
    FOUR_STEMS,
    WIND_KEYS,
    multi_pass,
    separation_params,
)
from resolve_mcp.config import get_config
from resolve_mcp.errors import BinNotFoundError, FfmpegUnavailableError
from resolve_mcp.ffmpeg import hwaccels
from resolve_mcp.jobs import cache
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.naming import timestamped_name
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.resolve.pool import (
    AUDIO_CHANNELS,
    apply_still_workaround,
    audio_channels,
    ensure_bin,
    find_bin,
    find_clip,
    import_into,
    media_pool,
    properties,
)
from resolve_mcp.resolve.session import current_project
from resolve_mcp.resolve.timeline import current_timeline, find_timeline
from resolve_mcp.tools.analysis import correlate_timeline
from resolve_mcp.tools.cut import build_timeline, swap_take, validate_cut
from resolve_mcp.tools.escape_hatch import run_python
from resolve_mcp.tools.jobs import get_job, list_jobs
from resolve_mcp.tools.media import inspect_clip, list_media
from resolve_mcp.tools.project import get_status, list_projects, snapshot_project
from resolve_mcp.tools.render import list_render_presets, render_timeline
from resolve_mcp.tools.timeline import (
    export_timeline,
    import_timeline,
    inspect_timeline,
    list_markers,
    list_timelines,
    set_markers,
)
from resolve_mcp.tools.titles import apply_titles, edit_title, list_titles
from resolve_mcp.tools.video import analyze_quality, detect_scene_cuts, grab_frames
from resolve_mcp.video import ffmpeg as video_ffmpeg
from resolve_mcp.video import jpeg

from . import otio
from .fakes import write_wav
from .live_state import (
    named_scan_clip,
    restore_current,
    sweep_suite_timelines,
    write_hard_cut_clip,
)
from .text_plus_probe import TEMPLATE_ENV, probe_template_append

pytestmark = pytest.mark.live

CORRELATE_BEATS_ENV = "RESOLVE_MCP_CORRELATE_BEATS"
CORRELATE_TIMELINE_ENV = "RESOLVE_MCP_CORRELATE_TIMELINE"
CORRELATE_AUDIO_ENV = "RESOLVE_MCP_CORRELATE_AUDIO"
"""Opt-in for #40's live AC: a real beats file, and the hand-edited cut to measure with it."""

SCENE_SCAN_CLIP_ENV = "RESOLVE_MCP_SCENE_SCAN_CLIP"
"""Override for #34's live AC: a pool clip with hard cuts, so the scan has cuts to map.

Leave it unset and the suite builds its own — see :func:`a_clip_with_hard_cuts`. Set it to
scan a real edit instead, which is the stronger check when the project has a flattened
render in the pool: a generated clip proves the mapping on synthetic cuts, a real one
proves it on the cuts a camera and an editor made.
"""

SCAN_BIN = "resolve-mcp-scratch"
"""Where the generated scan clip is imported. Deleted with its clip when the test ends."""

QUALITY_SCAN_SECONDS = 20.0
"""How much of a real angle the image-quality smoke decodes. Long enough to hold several
seconds of camera movement — the stability reading needs neighbours — and short enough that
a 4K master does not turn the live tier into a render queue."""

SMOKE_CUT = "resolve-mcp-smoke"
"""Every build here materialises a new version of this name; delete them when you are done."""

SMOKE_SONG = "resolve-mcp-130"
"""The blue marker #130's carry moves — named so a leftover in the GUI says where it came from."""

LOCKED_TRACK_PROBE = """
def under(folder):
    found = list(folder.GetClipList() or [])
    for sub in (folder.GetSubFolderList() or []):
        found += under(sub)
    return found

pool = project.GetMediaPool()
was_on = project.GetCurrentTimeline()
timeline = pool.CreateEmptyTimeline("resolve-mcp-lock-probe")
project.SetCurrentTimeline(timeline)
timeline.SetTrackLock("video", 1, True)
clips = [c for c in under(pool.GetRootFolder()) if c.GetName() == {name}]
returned = pool.AppendToTimeline([
    {{"mediaPoolItem": clips[0], "startFrame": {start}, "endFrame": {end},
      "mediaType": 1, "trackIndex": 1, "recordFrame": timeline.GetStartFrame()}}
]) if clips else None
result = {{
    "found": bool(clips),
    "returned_truthy": bool(returned),
    "items_on_track": len(timeline.GetItemListInTrack("video", 1) or []),
    "was_open": was_on is not None,
    "put_back": bool(was_on is None or project.SetCurrentTimeline(was_on)),
}}
result["swept"] = bool(result["was_open"] and result["put_back"]
                       and pool.DeleteTimelines([timeline]))
"""
"""Spike #18 (d), reduced to its claim: a locked track reports a placement it did not make.

The clip is looked for through the whole pool rather than in its root folder: a project that
keeps its footage in bins — every real one — has an empty root, and the probe skipped on it,
so the footgun this guards went unmeasured on the machine it guards (#135).

The probe puts Resolve back on the timeline it found open and deletes its own empty cut
before it answers. Leaving Resolve sitting on an empty locked timeline is what made the
audio export downstream render nothing (#119 §B) — the probe's currency switch is the
cause, so undoing it belongs here rather than in the tests that trip over it. Both
outcomes travel back in the result, because a silent failure to put it back would be the
same bug wearing a fix.
"""


@pytest.fixture(autouse=True)
def _requires_resolve() -> None:
    result = get_status()
    if not result["ok"]:
        pytest.skip(f"Resolve unreachable: {result['error']['cause']}")


@pytest.fixture(scope="session", autouse=True)
def _a_project_without_the_last_runs_leftovers() -> Iterator[None]:
    """Delete the timelines the previous run built, and leave the director's cut open.

    Every build here materialises a new ``resolve-mcp-smoke`` version and every OTIO import
    materialises another named cut, so a project the suite has run against a few times holds
    dozens of them — and the run after that reads the pool through them. That is how #34's
    scene scan ended up picking a timeline, and how the locked-track probe met a name it had
    already created.

    Both halves are housekeeping, and neither may sink the run: a failure here would error
    *every* test at setup and bury the one message that says why — which is exactly what a
    dying Resolve did to the first run of this fixture (#135). ``_requires_resolve`` is what
    reports an unreachable Resolve, per test, with a cause.
    """
    if not get_status()["ok"]:
        yield
        return
    connection = get_connection()
    project = was_on = None
    try:
        # Inside the guard, not before it: a Resolve that is up with no project open raises
        # NoProjectOpenError here, and that is the shape this fixture exists not to have.
        project = current_project(connection, "No project is open, so there is nothing to sweep.")
        was_on = project.GetCurrentTimeline()
        swept = sweep_suite_timelines(media_pool(connection), project)
    except Exception as unswept:  # noqa: BLE001 - see the docstring: never sink the run
        print(f"\nnothing swept: {unswept}")
    else:
        if swept.deleted or swept.kept:
            print(f"\nswept {len(swept.deleted)} leftover timeline(s); kept {swept.kept}")

    yield

    if project is None:
        return
    try:
        restore_current(project, was_on)
    except Exception as stuck:  # noqa: BLE001 - a departed Resolve must not fail the run
        print(f"\nleft Resolve where the last test put it: {stuck}")


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
    """The one live check the fakes cannot make: that these key names exist at all.

    Filtered, because the assertions below are about a clip with a file behind it, and the
    first pool entry is a build timeline on any project this suite has run once — which
    has no ``File Path`` and would fail this for a reason that is not the one it tests.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No media pool")
    decodable = _decodable(listing["clips"])
    if not decodable:
        pytest.skip("No clips with media behind them in the media pool")
    first = decodable[0]

    result = inspect_clip(first["name"], bin=first["bin"])

    assert result["ok"] is True
    assert "File Path" in result["properties"]
    assert result["bounds"]["media"]["duration"] is not None


def test_the_audio_ch_key_reads_the_way_e7_reads_it() -> None:
    """#62 item 5: E7 fails open whenever ``audio_channels`` reads ``None``.

    That happens on a renamed key *and* on a kept key whose value stopped parsing —
    either way `validate_cut` would silently pass every clip as having audio, so the
    assertion is E7's own read, not key presence. Swept across the pool rather than
    pinned to one clip, and skipped when no clip carries audio keys at all: an
    image-sequence-only pool has nothing E7 could misread.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No media pool")
    decodable = _decodable(listing["clips"])
    if not decodable:
        pytest.skip("No clips with media behind them in the media pool")

    seen_keys: set[str] = set()
    for clip in decodable:
        result = inspect_clip(clip["name"], bin=clip["bin"])
        if not result["ok"]:
            continue
        properties = result["properties"]
        seen_keys.update(properties)
        if audio_channels(properties) is not None:
            return

    audio_ish = sorted(key for key in seen_keys if "audio" in key.lower())
    if not audio_ish:
        pytest.skip("No clip in the pool enumerates audio keys at all")
    raise AssertionError(
        f"No clip gave a readable {AUDIO_CHANNELS!r} count; audio keys seen: {audio_ish}"
    )


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


def test_an_otio_round_trip_preserves_the_timeline_structure(
    tmp_path: Path, a_known_cut: KnownCut
) -> None:
    """AC: export to OTIO, import it back, and the cut is the same cut.

    The fakes prove the naming and the error shaping; only Resolve proves that what it
    wrote is what it reads back, which is the whole basis of the transition route.

    Read on a cut this suite built, so the comparison is three shots rather than however
    many are in whatever was open — ``_shape`` refuses a timeline too long to read in one
    go, and a concert-length edit is exactly that (#135).
    """
    before = _shape(a_known_cut.name)

    exported = export_timeline(
        timeline=a_known_cut.name, path=str(tmp_path / "round-trip.otio")
    )
    assert exported["ok"] is True, exported.get("error")
    assert exported["export_type"] == "EXPORT_OTIO"

    imported = import_timeline(exported["path"], name=_smoke_name("round-trip"))
    assert imported["ok"] is True, imported.get("error")

    after = _shape(imported["timeline"]["name"])
    assert after["duration"] == before["duration"]
    for track, shots in before["tracks"].items():
        assert after["tracks"].get(track) == shots, f"{track} came back differently"


def test_a_hand_injected_dissolve_imports_as_a_transition(
    tmp_path: Path, a_known_cut: KnownCut
) -> None:
    """AC: a dissolve edited into an exported OTIO comes back as a real transition.

    Transitions are the wall this route exists to get around, and the scripting API has no
    getter for them — so what is asserted here is that Resolve accepted the edited document
    and rebuilt the cut from it. **The dissolve and the fade to black themselves are
    confirmed by eye in the Resolve GUI on the imported timeline**, and the result goes on
    the ticket; a run that stops at green here has verified half the AC.

    The cut it injects into is the suite's own three-shot build (#135): two cuts, both
    between shots long enough to carry six frames of dissolve, every run.
    """
    exported = export_timeline(timeline=a_known_cut.name, path=str(tmp_path / "injected.otio"))
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


def _decodable(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pool entries with media behind them that ffmpeg can open.

    A timeline is a pool clip too — it reports a frame count and a rate and is never
    offline — and every build test here leaves one behind, so on a project the suite has
    run once the shortest "clip" in the pool is a timeline with no file on disk (#34,
    live).

    The test that matters is therefore **a file path**, not the type name. Compound clips,
    multicams, Fusion compositions and generators are all pathless in exactly the same
    way, all report a rate and a frame count, and none of them is typed ``Timeline``; and
    a pathless entry is reported as *pathless rather than offline*, so the offline flag
    does not catch them either. Requiring a path covers every one of them, and covers
    timelines as a side effect rather than by naming a string this build happens to use.
    """
    return [
        one
        for one in clips
        if one["fps"]
        and one["frames"]
        and not one["offline"]
        and one["file_path"]
        and one["type"] != "Timeline"
    ]


def a_source_clip() -> dict[str, Any]:
    """A pool clip long enough to cut three shots out of, with a rate the cut can declare.

    Timelines are excluded for the same reason the video tests exclude them: every build
    here leaves one in the pool, and a timeline reports a frame count and a rate while
    having no media behind it to cut from (#34, live).
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No media pool")
    for entry in _decodable(listing["clips"]):
        clip = inspect_clip(entry["name"], bin=entry["bin"])
        if not clip["ok"]:
            continue
        media = clip["bounds"]["media"]
        fps = clip["properties"].get("FPS")
        if media["duration"] is None or media["duration"]["frames"] < 200 or not fps:
            continue
        return {
            "name": entry["name"],
            # Verbatim, not `or None`: the bin a listing reports reads the same clip back,
            # and "" is the pool root itself — the root copy of a duplicated name (#122).
            "bin": entry["bin"],
            "fps": float(fps),
            # inspect_clip reports media bounds as in/out/duration — there is no "start".
            "start": media["in"]["frames"],
        }
    pytest.skip("No pool clip long enough to cut a smoke timeline from")


def a_smoke_cut(
    tmp_path: Path,
    source: dict[str, Any],
    durations: tuple[int, ...],
    alternate_at: int | None = None,
    audio_in: int | None = None,
    name: str = "smoke.cut.json",
    resolution: tuple[int, int] | None = None,
) -> str:
    """A rough-cut shaped file (no master audio) built from one real clip.

    ``alternate_at`` gives every segment one equal-duration alternate starting there — the
    same clip is a legitimate alternate source, and one clip is all a smoke run can count on.

    ``audio_in`` turns it into a concert-shaped cut instead, laying the same clip's audio
    under the whole thing from that source frame — the continuous substrate #130's marker
    carry rides. Whether the clip has audio at all is the pool's business, so the caller
    validates and skips rather than this guessing.
    """
    at = source["start"]
    segments: list[dict[str, Any]] = []
    for index, length in enumerate(durations):
        segment: dict[str, Any] = {
            "id": f"s{index:03d}",
            "source": "angle",
            "in": at,
            "out": at + length,
        }
        if alternate_at is not None:
            segment["alternates"] = [
                {"source": "angle", "in": alternate_at, "out": alternate_at + length}
            ]
        segments.append(segment)
        at += length
    angle = {"clip": source["name"]}
    if source["bin"] is not None:
        angle["bin"] = source["bin"]
    doc: dict[str, Any] = {
        "schema": 1,
        "timeline": {"name": SMOKE_CUT, "fps": source["fps"]},
        "sources": {"angle": angle},
        "segments": segments,
    }
    if resolution is not None:
        doc["timeline"]["resolution"] = {"width": resolution[0], "height": resolution[1]}
    if audio_in is not None:
        doc["audio"] = {"source": "angle", "in": audio_in, "out": audio_in + sum(durations)}
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


class KnownCut(NamedTuple):
    """The cut :func:`a_known_cut` built, and whether a mix went under it."""

    name: str
    audio: bool


@pytest.fixture
def a_known_cut(tmp_path: Path) -> Iterator[KnownCut]:
    """A short cut of the suite's own making, current for the length of one test.

    Three tests here work on *the current timeline* — the OTIO round trip, the dissolve
    import and the audio export — and for a long time that meant whatever the test before
    them left open. Suite ordering decided it, so the audio export was measuring an empty
    locked probe timeline and the round trip was comparing a concert-length edit shot by
    shot (#119 §B). None of them is about that timeline; each is about what Resolve does
    with one, so the suite builds one and says which.

    Master audio is laid under it when the source clip has any, because the export test
    needs a mix to render — ``validate_cut`` answers that, and a pool of silent clips
    leaves the picture-only cut, which still serves the other two.

    The director's timeline goes back afterwards: the later render tests need a long one,
    and somebody may be sitting in front of the GUI.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    durations = (48, 24, 36)
    path = a_smoke_cut(tmp_path, source, durations, audio_in=source["start"], name="known.cut.json")
    with_audio = bool(validate_cut(path)["valid"])
    if not with_audio:
        path = a_smoke_cut(tmp_path, source, durations, name="known.cut.json")
        checked = validate_cut(path)
        assert checked["valid"] is True, checked["errors"]

    built = build_timeline(path)
    assert built["ok"] is True, built.get("error")
    name = str(built["timeline"]["name"])

    project = current_project(get_connection())
    with current_timeline(project, find_timeline(project, name)):
        yield KnownCut(name=name, audio=with_audio)


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
    assert result["placed"] == {
        "segments": 3,
        "gaps": 0,
        "overlays": 0,
        "audio": False,
        "selectors": 0,
    }
    built = inspect_timeline(result["timeline"]["name"], detail="clips")
    assert built["ok"] is True
    video = [track for track in built["tracks"] if track["type"] == "video"][0]
    starts = [item["record"]["in"]["frames"] for item in video["items"]]
    timeline_start = built["timeline"]["start"]["frames"]
    assert [item["record"]["duration"]["frames"] for item in video["items"]] == list(durations)
    assert starts == [timeline_start, timeline_start + 48, timeline_start + 72]


def test_a_real_build_delivers_the_resolution_the_cut_asked_for(tmp_path: Path) -> None:
    """#187's live AC: 1920x1080 out of a project that creates timelines at 4K.

    Nothing below this is checkable against a fake. Whether ``useCustomSettings`` is what
    detaches a timeline from its project, whether the resolution keys take a string, and
    whether a render then follows the *timeline* rather than the project are three facts
    about Resolve, and the whole device is worthless if any of them is different from what
    the wrapper assumes.

    The render is the evidence, not the timeline setting: the frame grabbed back off the
    file is what the delivery actually is. ``max_edge`` is far past 4K on purpose — the
    still command only ever scales down, so an unchanged grab is the render's own size.

    Skips unless the open project creates timelines at something other than the cut's own
    resolution: a 1080p project would pass this test without the device doing anything.
    Leaves one more ``resolve-mcp-smoke`` version and one rendered file behind.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    probe = run_python("result = project.GetSetting('timelineResolutionWidth')")
    assert probe["ok"] is True, probe.get("error")
    # run_python renders its value with repr, so the setting comes back as source text.
    project_width = str(ast.literal_eval(probe["result"]))
    if project_width == "1920":
        pytest.skip("This project already creates timelines at 1920 wide")

    presets = list_render_presets()
    assert presets["ok"] is True, presets.get("error")
    if not presets["presets"]:
        pytest.skip("No render presets in this project")
    preset = os.environ.get("RESOLVE_MCP_RENDER_PRESET") or presets["presets"][0]

    source = a_source_clip()
    built = build_timeline(a_smoke_cut(tmp_path, source, (48, 24), resolution=(1920, 1080)))
    assert built["ok"] is True, built.get("error")
    assert built["timeline"]["resolution"] == {"width": 1920, "height": 1080}

    project = current_project(get_connection())
    with current_timeline(project, find_timeline(project, str(built["timeline"]["name"]))):
        started = render_timeline(
            preset=preset,
            name="resolve-mcp-smoke-1080",
            target_dir=str(tmp_path),
        )
        assert started["ok"] is True, started.get("error")
        record = wait_for(started["job"]["job_id"], timeout=1800.0)

    assert record.state == "completed", record.error
    assert record.result is not None
    written = Path(record.result["path"])
    grabbed = video_ffmpeg.grab(written, tmp_path / "delivered.jpg", seconds=0.2, max_edge=8192)
    delivered = jpeg.describe(grabbed.path)
    print(
        f"\npreset {preset!r} delivered {delivered['width']}x{delivered['height']} "
        f"from a {project_width}-wide project: {written}"
    )
    assert (delivered["width"], delivered["height"]) == (1920, 1080)

    # The tail route ships the *import*, not the timeline the shots were appended to, and
    # Resolve creates that one at the project's default like any other. A fake can only
    # agree with itself about that, and the tail is the shape the corpus actually delivers —
    # so the second build is checked here rather than assumed. No second render: what is
    # under test is which timeline the setting landed on.
    tailed_file = Path(
        a_smoke_cut(tmp_path, source, (48, 24), resolution=(1920, 1080), name="tailed.cut.json")
    )
    tailed_doc = json.loads(tailed_file.read_text(encoding="utf-8"))
    tailed_doc["tail"] = {"type": "dissolve_to_black", "duration_frames": 12}
    tailed_file.write_text(json.dumps(tailed_doc), encoding="utf-8")

    tailed = build_timeline(str(tailed_file))

    assert tailed["ok"] is True, tailed.get("error")
    assert tailed["tail"]["route"] == "otio_round_trip"
    assert tailed["timeline"]["resolution"] == {"width": 1920, "height": 1080}


def a_device_cut(tmp_path: Path, source: dict[str, Any]) -> str:
    """#141's two devices in one real cut: black on V1, and overlays on V2 and V3.

    Two shots with 30 frames of black between them, so the second is appended at a
    ``recordFrame`` 30 frames past the end of V1's media — the one thing about gaps no
    fake can settle, because Resolve is free to slide it back to the free frame instead.
    The overlays reuse the same clip's opening frames, so the run needs no second source.
    """
    at = source["start"]
    angle: dict[str, Any] = {"clip": source["name"]}
    if source["bin"] is not None:
        angle["bin"] = source["bin"]
    doc: dict[str, Any] = {
        "schema": 1,
        "timeline": {"name": SMOKE_CUT, "fps": source["fps"]},
        "sources": {"angle": angle},
        "segments": [
            {"id": "s000", "source": "angle", "in": at, "out": at + 48},
            {"id": "g001", "gap": 30, "note": "false ending"},
            {"id": "s001", "source": "angle", "in": at + 48, "out": at + 84},
        ],
        "overlays": [
            # Over the black itself, which is only expressible because the gap is.
            {
                "id": "b01",
                "source": "angle",
                "in": at,
                "out": at + 24,
                "over": {"segment": "g001", "offset": 0},
            },
            # V3, and deliberately over the same frames b01 would want if it shared a
            # track — the per-track E10 reading, checked against a real timeline.
            {
                "id": "b02",
                "source": "angle",
                "in": at + 24,
                "out": at + 44,
                "over": {"segment": "s000", "offset": 40},
                "track": 3,
            },
        ],
    }
    path = tmp_path / "devices.cut.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_a_real_build_leaves_black_on_v1_and_stacks_overlays_above_it(tmp_path: Path) -> None:
    """#141's live AC: a real timeline holds a gap and the overlays over it.

    The gap is the risk. Every other placement in a cut is butt-joined, so this is the
    first time the build sends a ``recordFrame`` past the end of what is on the track —
    and #18 found Resolve willing to slide an append it cannot honour while reporting
    success. If it slides, the second shot lands at 48 instead of 78 and this fails; the
    build's own read-back would fail it too, which is the point of having both.

    Leaves one more ``resolve-mcp-smoke`` version behind, as every build test here does.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    cut_file = a_device_cut(tmp_path, source)
    assert validate_cut(cut_file)["valid"] is True

    result = build_timeline(cut_file)

    assert result["ok"] is True, result.get("error")
    assert result["placed"] == {
        "segments": 2,
        "gaps": 1,
        "overlays": 2,
        "audio": False,
        "selectors": 0,
    }
    built = inspect_timeline(result["timeline"]["name"], detail="clips")
    assert built["ok"] is True
    start = built["timeline"]["start"]["frames"]
    video = [track for track in built["tracks"] if track["type"] == "video"]
    assert len(video) == 3, "the V3 overlay should have grown the timeline to three tracks"
    placed = [
        [
            (item["record"]["in"]["frames"] - start, item["record"]["duration"]["frames"])
            for item in track["items"]
        ]
        for track in video
    ]
    # V1: 48 frames of picture, 30 of nothing, then 36 more — the hole is the device.
    assert placed[0] == [(0, 48), (78, 36)]
    assert placed[1] == [(48, 24)]
    assert placed[2] == [(40, 20)]


def test_a_real_rebuild_carries_the_song_markers_onto_the_new_version(tmp_path: Path) -> None:
    """#130 end to end, and the part no fake can settle: whether a frame derived from
    Resolve's own reading of where the mix sits is a frame Resolve will take a marker at.

    A blue marker names a song, and a human places it. The rebuild has to move it by the
    frame of the master mix under it rather than by copying the number — so this second cut
    starts its mix 24 frames later and reorders the shots underneath. 24 frames earlier is
    then the only correct answer, and copying, or re-deriving off the picture, gives another.

    Leaves two more ``resolve-mcp-smoke`` versions behind, as every build test here does.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    first = a_smoke_cut(tmp_path, source, (48, 24, 36), audio_in=source["start"], name="a.cut.json")
    checked = validate_cut(first)
    if not checked["valid"]:
        pytest.skip(f"No pool clip this cut can lay a master mix from: {checked['errors']}")

    made = build_timeline(first)
    assert made["ok"] is True, made.get("error")
    earlier = str(made["timeline"]["name"])
    marked = set_markers(
        [{"frame": made["timeline"]["start"]["frames"] + 60, "color": "Blue", "name": SMOKE_SONG}],
        timeline=earlier,
    )
    assert marked["ok"] is True and marked["added"] == 1, marked

    second = a_smoke_cut(
        tmp_path,
        source,
        (36, 48, 24),
        audio_in=source["start"] + 24,
        name="b.cut.json",
    )
    rebuilt = build_timeline(second)

    assert rebuilt["ok"] is True, rebuilt.get("error")
    assert rebuilt["markers"]["from"] == earlier
    assert rebuilt["markers"]["carried"] == 1, rebuilt["markers"]
    assert rebuilt["markers"]["shift"] == -24
    carried = list_markers(rebuilt["timeline"]["name"], color="Blue")
    assert carried["ok"] is True
    assert [(one["record"]["frames"], one["name"]) for one in carried["markers"]] == [
        (rebuilt["timeline"]["start"]["frames"] + 36, SMOKE_SONG)
    ]


def test_a_real_take_selector_swaps_the_angle_without_moving_the_shot(tmp_path: Path) -> None:
    """The take path end to end, and the three things no fake can settle about it.

    (a) whether ``AddTake`` reads ``endFrame`` half-open the way ``AppendToTimeline`` does —
    a selector whose takes are all the same length is the only thing making an in-place swap
    legal, so an off-by-one here is the whole feature; (b) where Resolve leaves the selection
    after an add, which the build refuses to assume and sets to the main take explicitly; and
    (c) whether the swapped shot really plays the alternate's frames while keeping its own
    position and length.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    source = a_source_clip()
    durations = (48, 24, 36)
    alternate_at = source["start"] + sum(durations)
    cut_file = a_smoke_cut(tmp_path, source, durations, alternate_at=alternate_at)
    assert validate_cut(cut_file)["valid"] is True

    result = build_timeline(cut_file)

    assert result["ok"] is True, result.get("error")
    assert result["placed"] == {
        "segments": 3,
        "gaps": 0,
        "overlays": 0,
        "audio": False,
        "selectors": 3,
    }
    name = result["timeline"]["name"]
    before = _video_items(name)
    assert [item["takes"] for item in before] == [2, 2, 2]
    assert [item["source"]["in"]["frames"] for item in before][0] == source["start"]
    # #120's second half, and it has to be measured on a shot whose getters both read true:
    # Resolve's end getter is already one past the last frame, so a wrapper that adds one
    # to it reports a 48-frame shot covering 49 source frames. On the *swapped* shot the
    # start getter's own lost frame cancels that, which is why this is asserted here.
    assert before[0]["source"]["out"]["frames"] == source["start"] + durations[0]

    swapped = swap_take(cut_file, "s000", 2, timeline=name)

    assert swapped["ok"] is True, swapped.get("error")
    assert swapped["changed"] is True
    assert swapped["sync"]["in"] == alternate_at
    after = _video_items(name)
    assert after[0]["source"]["in"]["frames"] == alternate_at
    assert after[0]["source"]["out"]["frames"] == alternate_at + durations[0]
    assert [item["record"]["in"]["frames"] for item in after] == [
        item["record"]["in"]["frames"] for item in before
    ]
    assert [item["record"]["duration"]["frames"] for item in after] == list(durations)

    assert swap_take(cut_file, "s000", 1, timeline=name)["ok"] is True
    assert _video_items(name)[0]["source"]["in"]["frames"] == source["start"]


def _video_items(name: str) -> list[dict[str, Any]]:
    """The shots on V1, read so that ``takes`` is a number rather than ``None``.

    ``make_current`` is explicit rather than relied upon: #84 makes the take count readable
    only on the project's current timeline, and this passed before only because the build
    happened to leave its timeline current. That is the coincidence the ticket was opened
    on — asking for the switch says what the reading needs instead of inheriting it.
    """
    read = inspect_timeline(name, detail="clips", make_current=True)
    assert read["ok"] is True, read.get("error")
    assert read["currency"]["read_as_current"] is True
    video = [track for track in read["tracks"] if track["type"] == "video"][0]
    items: list[dict[str, Any]] = video["items"]
    return items


def test_a_non_current_timeline_reports_unknown_rather_than_a_confident_zero() -> None:
    """#84, the half no fake can settle: that Resolve really does answer falsely.

    The fakes model this defect, but only because this test measured it — they hand back
    the same object whether or not a timeline is current, so left to themselves they would
    agree with any wrapper. What is checked here is the premise: that reading a timeline
    the project does not have open yields false/zero from Resolve itself, and that the
    wrapper turns that into ``null`` rather than passing it on.

    Read-only: it names a timeline that is *not* current and never switches.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    listing = list_timelines()
    assert listing["ok"] is True
    others = [entry["name"] for entry in listing["timelines"] if not entry["current"]]
    if not others:
        pytest.skip("Project has no timeline other than the open one")

    read = inspect_timeline(others[0], detail="clips")

    assert read["ok"] is True, read.get("error")
    assert read["timeline"]["current"] is False
    currency = read["currency"]
    assert currency["read_as_current"] is False
    assert currency["made_current"] is False
    assert currency["unknown_fields"] == ["enabled", "locked", "takes"]
    for track in read["tracks"]:
        assert track["enabled"] is None
        assert track["locked"] is None
        # The fields the #84 sweep proved currency-safe still answer, so the nulls above
        # are a named exception and not a whole reading gone dark.
        assert track["name"]
        for item in track["items"]:
            assert item["takes"] is None
            assert item["record"]["duration"]["frames"] is not None


def test_make_current_reads_the_real_flags_and_puts_the_timeline_back() -> None:
    """The opt-in, live: the switch is what makes those three fields readable at all.

    The #84 probe ruled out every cheaper route — a fresh timeline handle, and even a
    fresh project object, still read false/zero — so this is the only way to the numbers,
    and putting the director's timeline back is the price of taking it.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")
    listing = list_timelines()
    was_open = listing["current"]
    others = [entry["name"] for entry in listing["timelines"] if not entry["current"]]
    if not others or was_open is None:
        pytest.skip("Project needs an open timeline and one other")

    read = inspect_timeline(others[0], detail="clips", make_current=True)

    assert read["ok"] is True, read.get("error")
    assert read["currency"] == {
        "read_as_current": True,
        "made_current": True,
        "unknown_fields": [],
        "fix": None,
    }
    for track in read["tracks"]:
        assert track["enabled"] is not None
        assert track["locked"] is not None
        for item in track["items"]:
            assert item["takes"] is not None
    assert list_timelines()["current"] == was_open


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
    # Resolve's own type name, not the suffix: a PNG *sequence* is a multi-frame clip with
    # a ``.png`` path, and asking one for 90 frames is not the one-time Out write this test
    # is about — it is a request for frames the clip does not have (#135).
    stills = [
        entry
        for entry in listing["clips"]
        if entry["type"] == "Still"
        and Path(str(entry.get("file_path") or "")).suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    if not stills:
        pytest.skip("No still image in the media pool")
    still = stills[0]
    fps = get_status()["context"]["fps"] or 24.0
    doc = {
        "schema": 1,
        "timeline": {"name": SMOKE_CUT, "fps": fps},
        # Verbatim, not ``or None``: a still that also sits in the pool root is two clips
        # of one name, a source naming no bin cannot say which, and "" is the root itself
        # — which is where this project's duplicates live (#122).
        "sources": {"still": {"clip": still["name"], "bin": still["bin"]}},
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
    # run_python renders its value with repr, so the probe's dict comes back as source text.
    reported = ast.literal_eval(probe["result"])
    # Asserted before the skip below: the probe moves Resolve and creates a timeline
    # whether or not it finds a clip to append, and the tests after it read the current
    # timeline — so where it left the GUI is part of what it has to get right, on every
    # path out of here.
    assert reported["put_back"] is True, "the probe left Resolve on its own empty timeline"
    if reported["was_open"]:
        assert reported["swept"] is True, "the probe left its empty timeline in the project"
        assert list_timelines()["current"] != "resolve-mcp-lock-probe"
    # A session that had no timeline open has nowhere to move to, and Resolve will not
    # delete the cut it is sitting on — so the probe's timeline stays, and the next run's
    # sweep is what takes it. Failing here instead would report a leak as a broken probe.
    if not reported["found"]:
        pytest.skip("The chosen clip is nowhere in the media pool, so the probe could not run")
    assert reported["returned_truthy"] is True
    assert reported["items_on_track"] == 0


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


def test_the_render_queue_exports_the_real_timeline_mix(a_known_cut: KnownCut) -> None:
    """The AC no seam can check: that these render-settings keys are the ones Resolve takes.

    The fakes prove the job machinery, the caching and the failure shaping. Whether
    ``SetRenderSettings`` accepts ``ExportVideo``/``AudioBitDepth``/``AudioSampleRate`` under
    those names, and whether an audio-only wav/lpcm job renders at all, is only answerable
    here.

    The queue renders the *current* timeline, so what is current is the whole input: this
    used to render whatever the test before it left open, which after the locked-track probe
    was an empty timeline with no mix on it at all (#119 §B). It now renders a cut this
    suite built, with a master mix laid under it — four seconds rather than a whole concert.
    """
    if not a_known_cut.audio:
        pytest.skip("No pool clip this suite can lay a master mix from, so there is no mix")

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


def test_the_real_separator_produces_the_stems_the_passes_expect(tmp_path: Path) -> None:
    """The AC no seam can check: that these models exist and label their output that way.

    The fakes prove the three commands, the progress mapping, the caching and every refusal.
    What they cannot prove is that ``htdemucs_ft.yaml``, the DrumSep checkpoint and
    ``17_HP-Wind_Inst-UVR.pth`` are names audio-separator resolves, that the drum model
    yields kick/snare/toms at all, or that the real CLI writes ``<input>_(Label)_<model>.wav``
    — the naming the stem mapping reads back. The wind pass is here for the label above all:
    ``WIND_STEMS`` spells the two halves the way that model writes them, and a fake cannot
    tell a right guess from a wrong one. Slow: the first run downloads all three models.

    The skip asks the *configured* executable, not the default name: a director who pointed
    ``RESOLVE_MCP_AUDIO_SEPARATOR`` at an install off PATH would otherwise silently skip the
    one check no fake can stand in for.
    """
    if shutil.which(get_config().audio_separator) is None:
        pytest.skip(f"No audio-separator at {get_config().audio_separator!r}")

    fixture = write_wav(tmp_path / "separator-probe.wav", seconds=4.0)
    audio = {
        "path": str(fixture),
        "content_sha256": cache.content_hash(fixture),
        "scope": "live",
    }

    output = multi_pass(
        audio,
        separation_params(),
        lambda fraction, step: None,
        split_wind=True,
        reuse=False,
    )

    assert set(output.result["stems"]) >= set(FOUR_STEMS)
    assert set(output.result["drums"]) >= set(DRUM_STEMS)
    assert set(output.result["other"]) == set(WIND_KEYS.values())
    assert all(Path(one).stat().st_size > 0 for one in output.result["stems"].values())
    assert all(Path(one).stat().st_size > 0 for one in output.result["drums"].values())
    assert all(Path(one).stat().st_size > 0 for one in output.result["other"].values())
    # The other thing no fake can answer: that the real banner still names its device in
    # words ``device_of`` reads (#188), and that the device is the card. A CPU reading here
    # is a broken install, not a slow box (CLAUDE.md, "Compute device"): the separator's own
    # torch decides, and a ``+cpu`` build ran hours on the CPU under a WARNING nobody acted
    # on (#202). That build now refuses in ``environment()`` before this line is reached —
    # ``multi_pass`` raises SeparatorUnavailableError, red all the same; the assertion is
    # for the other case, a ``+cu`` build whose passes still landed on the CPU. Red is the
    # only signal a session cannot wait out. ``reuse=False`` because the fixture tone hashes
    # the same every run, and stems read off disk announce nothing.
    report = output.result["separator"]
    assert report["device"] != separator.UNKNOWN_DEVICE
    assert report["device"] != separator.CPU_DEVICE, (
        f"separation ran on the CPU (torch {report.get('torch')!r}); the live box's "
        "audio-separator needs a +cu torch build — see CLAUDE.md, Compute device"
    )


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is None,
    reason="the analysis extra is not installed (uv sync --extra analysis)",
)
def test_faster_whisper_reports_words_in_the_shape_the_worker_reads(tmp_path: Path) -> None:
    """The other AC no seam can check: what a real faster-whisper word object looks like.

    The fake tier substitutes the model entirely, so every assertion above it is about
    ``Word``, a shape this repo made up. That ``segment.words`` is populated at all with
    ``word_timestamps=True``, and that each one carries ``word``/``start``/``end``/
    ``probability``, is only answerable with the real model loaded. Slow on first run: it
    downloads large-v3 and needs a GPU to be quick about it.

    Two seconds of tone is deliberately not speech — the claim under test is the shape of
    the reply, not what was heard, and a model that hears nothing still has to return
    cleanly rather than raise.
    """
    from resolve_mcp.analysis import whisper

    from .fakes import write_wav

    audio = write_wav(tmp_path / "tone.wav", seconds=2.0)

    heard = whisper.transcribe(audio, {"model": whisper.DEFAULT_MODEL})

    assert isinstance(heard.words, tuple)
    for word in heard.words:
        assert word.text
        assert 0.0 <= word.start <= word.end
        assert 0.0 <= word.confidence <= 1.0


def test_a_real_frame_grab_lands_on_the_moment_resolve_numbers_it_at() -> None:
    """The AC no seam can check: that Start really is the offset between the two clocks.

    The fakes prove the command shape, the cap and the caching against a clip whose Start
    this test wrote itself. Whether real footage — an hour-based start timecode, a codec
    ffmpeg has to seek inside — is numbered the way the wrapper assumes only shows up here.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No project open in Resolve")
    footage = next(iter(_decodable(listing["clips"])), None)
    if footage is None:
        pytest.skip("No online clip with a frame rate in the media pool")
    bounds = inspect_clip(footage["name"], bin=footage["bin"])["bounds"]["media"]
    middle = bounds["in"]["frames"] + bounds["duration"]["frames"] // 2

    result = grab_frames(footage["name"], [middle], bin=footage["bin"])

    assert result["ok"] is True, result.get("error")
    grabbed = result["frames"][0]
    assert Path(grabbed["path"]).exists()
    assert grabbed["time"]["frames"] == middle
    assert max(grabbed["width"], grabbed["height"]) <= 1568

    again = grab_frames(footage["name"], [middle], bin=footage["bin"])
    assert again["cached"] is True, "unchanged media must be a cache hit"


def test_a_real_quality_scan_reads_an_angle_on_its_own_clock() -> None:
    """#182's live AC: the four readings, off real 4K footage, over a span of a real angle.

    What no fake can show is whether the readings *mean* anything on a concert master. The
    fixture tier proves the arithmetic separates a composed sharp frame from a composed soft
    one; here the frames are a camera in a dark club at 4K, decoded through the real scaler,
    and what is checked is that the numbers land in their ranges rather than at the ends of
    them — a sharpness of 0.0 or a stability of exactly 1.0 across a whole span would both be
    a reading that had stopped reading. The floors themselves are calibrated separately
    (docs/reference/image-quality-calibration.md); this is the route, not the threshold.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No project open in Resolve")
    footage = next(iter(_decodable(listing["clips"])), None)
    if footage is None:
        pytest.skip("No online clip with a frame rate in the media pool")
    bounds = inspect_clip(footage["name"], bin=footage["bin"])["bounds"]["media"]
    fps = bounds["in"]["fps"] or 25.0
    first = bounds["in"]["frames"] + bounds["duration"]["frames"] // 3
    last = min(bounds["out"]["frames"], first + int(fps * QUALITY_SCAN_SECONDS))

    started = analyze_quality(footage["name"], bin=footage["bin"], start=first, end=last)
    # The one envelope shape for a started job, on a real box (#219): the video tools used
    # to splice the record into the top level and the analysis tools to wrap it.
    record = wait_for(started["job"]["job_id"], timeout=1800.0)

    assert started["ok"] is True, started.get("error")
    assert record.state == "completed", record.error
    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    samples = catalog["samples"]
    assert len(samples) >= 4, "a scan of several seconds has to have several samples in it"
    for sample in samples:
        assert first <= sample["time"]["frames"] <= last
        assert 0.0 <= sample["sharpness"] <= 1.0
        assert 0.0 <= sample["exposure"] <= 1.0
        assert 0.0 <= sample["clipped"] <= 1.0
    # Not a threshold, a liveness check: a decode that handed back one repeated frame, or a
    # scaler that flattened the picture, would leave every reading identical.
    assert len({sample["sharpness"] for sample in samples}) > 1
    steady = [one["stability"] for one in samples if one["stability"] is not None]
    assert steady, "a continuous angle has neighbouring frames to compare"
    assert all(0.0 <= one <= 1.0 for one in steady)


def test_nvdec_decodes_a_real_clip_on_this_box(tmp_path: Path) -> None:
    """#202's live AC: the fakes prove the flag shaping, only a real decode proves NVDEC.

    Forcing ``cuda`` disables the software retry, so a frame coming back here means the
    hardware decoder itself produced it — an ffmpeg that ran ``-hwaccel cuda`` and quietly
    decoded in software anyway would still pass, but that substitution is ffmpeg's
    documented contract violation, not ours to test. A box whose ffmpeg lists no cuda
    skips: degrading there is the design (``auto`` records the reason), not a failure.
    """
    try:
        methods = hwaccels()
    except FfmpegUnavailableError as unavailable:
        pytest.skip(f"No ffmpeg to probe: {unavailable.cause}")
    if "cuda" not in methods:
        pytest.skip("this box's ffmpeg lists no cuda hwaccel")
    source = write_hard_cut_clip(tmp_path / "resolve-mcp-nvdec.mp4")

    forced = replace(get_config(), ffmpeg_hwaccel="cuda")
    grabbed = video_ffmpeg.grab(source, tmp_path / "nvdec.jpg", 1.5, 1568, config=forced)

    assert grabbed.path.exists() and grabbed.path.stat().st_size > 0
    assert grabbed.decode == {"device": "cuda", "reason": None}


def _sweep_scan_bin(pool: Any) -> None:
    """Delete the scan bin a crashed run left behind, so the import lands one clip not two."""
    try:
        located = find_bin(pool, SCAN_BIN)
    except BinNotFoundError:
        return
    pool.DeleteFolders([located.folder])


@pytest.fixture
def a_clip_with_hard_cuts(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """A pool clip the scan is guaranteed to find cuts in, built here unless one is named.

    This used to be "the shortest clip in the pool", which is how the scan ended up on a
    27-frame PNG sequence with nothing to detect — a clip with no cuts passes the mapping
    loop vacuously, so the half of #34 the test exists for went unchecked every run (#119
    §B). A shot log is not something a project owes the suite, and the live box's pool
    holds only raw continuous angles, so the suite generates what it needs: four solid
    colours a second apart, which is three cuts no detector can miss.

    ``RESOLVE_MCP_SCENE_SCAN_CLIP`` still names a real pool clip to scan instead, which is
    the stronger check where a project has a flattened render to point it at.
    """
    listing = list_media()
    if not listing["ok"]:
        pytest.skip("No project open in Resolve")
    named = named_scan_clip(_decodable(listing["clips"]), os.environ.get(SCENE_SCAN_CLIP_ENV, ""))
    if named is not None:
        yield named
        return

    try:
        generated = write_hard_cut_clip(tmp_path / "resolve-mcp-hard-cuts.mp4")
    except FfmpegUnavailableError as unavailable:
        pytest.skip(f"No ffmpeg to build a scan clip with: {unavailable.cause}")

    pool = media_pool(get_connection())
    _sweep_scan_bin(pool)
    target = ensure_bin(pool, SCAN_BIN)
    try:
        assert import_into(pool, [str(generated)], target), (
            f"Resolve imported nothing from {generated}"
        )
        entries = _decodable(list_media(bin=SCAN_BIN)["clips"])
        assert entries, "the generated clip did not read back as decodable footage"
        yield entries[0]
    finally:
        # The bin goes, and the clip with it: the file itself lives in tmp_path and is
        # about to vanish, and a pool entry pointing at a deleted file reads as offline
        # media in the GUI for whoever opens the project next.
        pool.DeleteFolders([target.folder])


def test_a_real_scene_scan_reports_cuts_on_the_clips_own_clock(
    a_clip_with_hard_cuts: dict[str, Any],
) -> None:
    """The other half of the clock check: pts_time counts from the file, cuts from the clip.

    What it is for is the mapping the fakes replay rather than produce: that ffmpeg's
    reported times become frame numbers inside the bounds ``inspect_clip`` reports for the
    same clip. That needs a clip with cuts in it, which is what the fixture guarantees — so
    finding none is now a failure rather than a skip.
    """
    chosen = a_clip_with_hard_cuts
    bounds = inspect_clip(chosen["name"], bin=chosen["bin"])["bounds"]["media"]

    started = detect_scene_cuts(chosen["name"], bin=chosen["bin"])
    record = wait_for(started["job"]["job_id"], timeout=1800.0)

    assert started["ok"] is True, started.get("error")
    assert record.state == "completed", record.error
    assert record.result is not None
    assert Path(record.result["path"]).exists()
    assert record.result["first_cuts"], (
        f"{chosen['name']!r} was chosen for its hard cuts and the scan found none — "
        "the clip-clock mapping went unexercised"
    )
    for cut in record.result["first_cuts"]:
        assert bounds["in"]["frames"] < cut["frames"] < bounds["out"]["frames"]


def test_the_preset_list_is_the_one_in_the_deliver_page() -> None:
    """#33: whether ``GetRenderPresetList`` answers at all, and with what spelling.

    Run with ``-s`` and record the names on the ticket — every render_timeline call names
    one of them, and they are per project and per machine.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    reply = list_render_presets()

    assert reply["ok"] is True, reply.get("error")
    assert reply["count"] > 0, "a stock Resolve ships presets; an empty list means the getter lied"
    print(f"\nrender presets: {reply['presets']}")


def test_the_shipped_default_preset_is_a_built_in_on_this_machine(tmp_path: Path) -> None:
    """#96: whether ``H.265 Master`` is really a name a stock Resolve offers.

    The fakes prove that an omitted preset resolves to the config default, that the marker
    says which of the two it was, and that an unknown name refuses. What only Resolve can
    say is whether the *shipped* default is spelled the way this project spells it — get
    that wrong and every render that names no preset refuses on a fresh install, which is
    exactly the call this ticket made the normal one. Slow: it renders a two-second span.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")
    default = get_config().default_render_preset
    listed = list_render_presets()
    assert listed["ok"] is True, listed.get("error")
    assert default in listed["presets"], (
        f"the default preset {default!r} is not in {listed['presets']} — a bare "
        "render_timeline would refuse on this install"
    )

    whole = inspect_timeline(detail="summary")
    assert whole["ok"] is True
    first = whole["timeline"]["start"]["frames"]
    fps = whole["timeline"]["fps"] or 24
    if whole["timeline"]["duration"]["frames"] < int(fps * 4):
        pytest.skip("The open timeline is too short to render a span out of")

    started = render_timeline(
        name="resolve-mcp-smoke-default",
        target_dir=str(tmp_path),
        start=first + int(fps),
        end=first + int(fps * 3),
    )
    record = wait_for(started["job"]["job_id"], timeout=1800.0)

    assert record.state == "completed", record.error
    assert record.params["preset_source"] == "default"
    assert record.result is not None
    assert record.result["preset"] == default
    written = Path(record.result["path"])
    assert written.exists() and written.stat().st_size > 0
    print(f"\ndefault preset {default!r} rendered {written} ({written.stat().st_size} bytes)")


def test_a_range_render_covers_the_frames_it_was_given(tmp_path: Path) -> None:
    """#33: the AC no fake can answer — that MarkIn/MarkOut are read on the timeline's clock.

    The fakes prove the conversion (half-open in, inclusive out) and that the settings
    reach ``SetRenderSettings``. What only Resolve can say is whether those frame numbers
    are absolute timeline frames — a timeline starting at 01:00:00:00 starts at frame 86400,
    and a Resolve reading them as offsets from zero would render the wrong part of the set
    while reporting success.

    So two ranges are rendered, one three times the other: if the marks were ignored, both
    would be the whole timeline and the files would be the same size. Slow — it renders
    twice. Check the shorter file opens and starts where the range said.
    """
    if get_status()["context"]["timeline"] is None:
        pytest.skip("No timeline open in Resolve")
    presets = list_render_presets()
    assert presets["ok"] is True, presets.get("error")
    if not presets["presets"]:
        pytest.skip("No render presets in this project")
    preset = os.environ.get("RESOLVE_MCP_RENDER_PRESET") or presets["presets"][0]

    whole = inspect_timeline(detail="summary")
    assert whole["ok"] is True
    first = whole["timeline"]["start"]["frames"]
    fps = whole["timeline"]["fps"] or 24
    if whole["timeline"]["duration"]["frames"] < int(fps * 13):
        pytest.skip("The open timeline is too short to render two ranges out of")

    short = render_timeline(
        preset=preset,
        name="resolve-mcp-smoke-short",
        target_dir=str(tmp_path),
        start=first + int(fps),
        end=first + int(fps * 3),
    )
    assert short["ok"] is True, short.get("error")
    short_record = wait_for(short["job"]["job_id"], timeout=1800.0)
    assert short_record.state == "completed", short_record.error

    long = render_timeline(
        preset=preset,
        name="resolve-mcp-smoke-long",
        target_dir=str(tmp_path),
        start=first + int(fps),
        end=first + int(fps * 7),
    )
    assert long["ok"] is True, long.get("error")
    long_record = wait_for(long["job"]["job_id"], timeout=1800.0)
    assert long_record.state == "completed", long_record.error

    assert short_record.result is not None
    assert long_record.result is not None
    shorter = Path(short_record.result["path"])
    longer = Path(long_record.result["path"])
    assert shorter.exists() and longer.exists()
    print(f"\nrendered {shorter} ({shorter.stat().st_size} bytes) with preset {preset!r}")
    assert longer.stat().st_size > shorter.stat().st_size * 1.5, (
        "a three-times-longer range rendered the same size — MarkIn/MarkOut were ignored"
    )

    polled = get_job(short["job"]["job_id"])
    assert polled["ok"] is True
    assert polled["job"]["state"] == "completed"


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


TITLES_TEMPLATE_ENV = "RESOLVE_MCP_TITLES_TEMPLATE"
"""The media-pool clip *name* of an imported Text+ template — not a path; #41 imports it."""

SMOKE_SONGS = ("smoke-song-one", "smoke-song-two")
FILLER_FRAMES = 120
"""Long enough to hold two short titles, short enough for a stock five-second template."""

TITLE_FRAMES = 50
FADE_FRAMES = 12


def test_apply_titles_places_text_plus_instances_with_their_own_text_and_fades(
    tmp_path: Path,
) -> None:
    """#42 ACs 1, 2 and 4: the titles land at the right frames with the right words, the
    opacity spline reads back, and a second apply replaces rather than stacks.

    Runs on a scratch timeline it creates, marks, titles and deletes again — the same
    shape as the #41 probe, so it never touches the cut anyone is reviewing. The template
    must already be in the media pool (import its `.drb` once); pass its clip name.

    Paste the printed report onto the ticket — that is the record the ticket asks for.
    """
    wanted = os.environ.get(TITLES_TEMPLATE_ENV, "").strip()
    if not wanted:
        pytest.skip(f"Set {TITLES_TEMPLATE_ENV} to the pool clip name of a Text+ template")
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    connection = get_connection()
    pool = media_pool(connection)
    project = connection.handle().GetProjectManager().GetCurrentProject()
    template = find_clip(pool, wanted)
    was_on = project.GetCurrentTimeline()

    name = timestamped_name("apply-titles-smoke", "", "apply-titles-smoke")
    scratch = pool.CreateEmptyTimeline(name)
    assert scratch is not None, f"Resolve created no timeline called {name!r}"
    try:
        assert project.SetCurrentTimeline(scratch), f"Resolve would not open {name!r}"
        start = int(scratch.GetStartFrame())
        # Something on V1 so the timeline has a span for the titles to land inside.
        placed = pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": template.clip,
                    "startFrame": 0,
                    "endFrame": FILLER_FRAMES,
                    "mediaType": 1,
                    "trackIndex": 1,
                    "recordFrame": start,
                }
            ]
        )
        assert placed, f"{wanted!r} would not append at {FILLER_FRAMES} frames — too short?"
        for offset, key in zip((0, 60), SMOKE_SONGS, strict=True):
            assert scratch.AddMarker(offset, "Blue", key, "apply_titles smoke", 1), (
                f"Resolve refused a blue marker at {offset}"
            )

        file = tmp_path / "titles.json"
        file.write_text(json.dumps(_smoke_titles(name, wanted)), encoding="utf-8")

        result = apply_titles(str(file))
        again = apply_titles(str(file))

        print("\n" + _render_titles(result, again))
        assert result["ok"] is True, result.get("error")
        assert [one["id"] for one in result["placed"]] == ["smoke-01", "smoke-02"]
        assert [one["record"]["frames"] for one in result["placed"]] == [start, start + 60]
        assert again["ok"] is True, again.get("error")
        assert again["cleared"] == 2, "a second apply must replace the titles, not stack them"
    finally:
        if was_on is not None:
            project.SetCurrentTimeline(was_on)
        pool.DeleteTimelines([scratch])


SMOKE_TITLE = "Text+"
"""Resolve's own stock Fusion title, which every Studio install has in its Effects library."""


def test_edit_title_fixes_one_placed_title_and_leaves_the_other_alone() -> None:
    """#43's three ACs against real Text+ instances: text in place, params in place, alone.

    Everything here is a claim only a real comp can settle, and each one had a surprise in
    it. `GetInputList` on a live Text+ answers with **309** inputs, 194 of them external —
    which is why `list_titles` reports the handful the template *sets* rather than the lot.
    Whether `SetInput` on an input other than `StyledText` takes on a *placed* instance is
    the same open question the fade had. And whether editing one instance leaves its
    neighbour alone is the shared-comp finding #41 went looking for, asked the only way
    that answers it — by reading the neighbour back off the timeline afterwards.

    Unlike #41 and #42 this needs **no fixture and no environment variable**:
    `InsertFusionTitleIntoTimeline` places Resolve's own stock Text+ straight onto a
    timeline, so the whole run is built and torn down by the API. It works on a scratch
    timeline it creates and deletes again, so it never touches a cut anyone is reviewing.

        uv run pytest -m live -s -k edit_title

    Paste the printed report onto the ticket.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    connection = get_connection()
    pool = media_pool(connection)
    project = connection.handle().GetProjectManager().GetCurrentProject()
    was_on = project.GetCurrentTimeline()

    name = timestamped_name("edit-title-smoke", "", "edit-title-smoke")
    scratch = pool.CreateEmptyTimeline(name)
    assert scratch is not None, f"Resolve created no timeline called {name!r}"
    try:
        assert project.SetCurrentTimeline(scratch), f"Resolve would not open {name!r}"
        # The tool owns the track called Titles; a fresh timeline's only video track is V1.
        assert scratch.SetTrackName("video", 1, "Titles"), "Resolve would not name video 1"
        inserter = getattr(scratch, "InsertFusionTitleIntoTimeline", None)
        assert callable(inserter), "this build cannot insert a Fusion title"
        for timecode in ("01:00:00:00", "01:00:10:00"):
            assert scratch.SetCurrentTimecode(timecode), f"playhead would not go to {timecode}"
            assert inserter(SMOKE_TITLE) is not None, f"Resolve inserted no {SMOKE_TITLE!r}"

        listed = list_titles(timeline=name)
        assert listed["ok"] is True, listed.get("error")
        assert len(listed["titles"]) == 2, "two Text+ did not land on the Titles track"
        first, second = (one["record"]["frames"] for one in listed["titles"])

        # Both instances start life saying the same thing, so they are named by frame.
        fixed = edit_title(at=first, text="Live smoke fixed", timeline=name)
        param = _a_writable_param(listed["titles"][0]["params"])
        with_param = (
            None
            if param is None
            else edit_title(at=first, params={param[0]: param[1]}, timeline=name)
        )
        after = list_titles(timeline=name)

        print("\n" + _render_edit(listed, fixed, with_param, after))
        assert fixed["ok"] is True, fixed.get("error")
        assert fixed["other_titles_unchanged"] == 1, (
            "the neighbour was not re-read, or the two instances share one Fusion comp"
        )
        assert [one["text"] for one in after["titles"]] == ["Live smoke fixed", "Custom Title"]
        assert [one["record"]["frames"] for one in after["titles"]] == [first, second]
        if param is not None and with_param is not None:
            assert with_param["ok"] is True, with_param.get("error")
            assert with_param["title"]["params"]["values"][param[0]] == param[1]
    finally:
        if was_on is not None:
            project.SetCurrentTimeline(was_on)
        pool.DeleteTimelines([scratch])


def _a_writable_param(exposed: dict[str, Any] | None) -> tuple[str, float] | None:
    """A numeric input that is safe to nudge and read back, or nothing.

    Only ``Size``: it is a plain continuous slider, so a value near its own is taken
    verbatim. The other numbers a stock Text+ sets are enumerations wearing numbers — the
    two justifications, ``Wrap`` — and Fusion clamping one of those to a legal value would
    read back as a failed write when nothing had gone wrong.
    """
    if exposed is None or not exposed["read"]:
        return None
    value = exposed["values"].get("Size")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return ("Size", round(float(value) + 0.01, 4))


def _render_edit(
    listed: dict[str, Any],
    fixed: dict[str, Any],
    with_param: dict[str, Any] | None,
    after: dict[str, Any],
) -> str:
    """The report the ticket asks for: what was exposed, what was written, what moved."""
    if not listed["ok"]:
        return f"list_titles failed: {listed['error']['cause']}"
    lines = [f"timeline:      {listed['timeline']}", f"track:         {listed['track']}"]
    for one in listed["titles"]:
        params = one["params"]
        node = one["node"]
        lines.append(
            f"  before: {one['text']!r} @ {one['record']['frames']} for "
            f"{one['duration']['frames']}f — node {node and node['name']!r}, "
            f"{node and node['text_plus_in_comp']} Text+ in comp"
        )
        lines.append(
            f"          params read={params and params['read']}: {params and params['detail']}"
        )
        if params and params["values"]:
            lines.append(f"          sets: {params['values']}")
    if fixed["ok"]:
        lines.append(
            f"text edit:     {fixed['was']['text']!r} -> {fixed['title']['text']!r}, "
            f"edited={fixed['edited']}, others unchanged={fixed['other_titles_unchanged']}"
        )
    else:
        lines.append(
            f"text edit:     FAILED {fixed['error']['cause']}\n  fix: {fixed['error']['fix']}"
        )
    if with_param is None:
        lines.append("param edit:    skipped — this template set no plain numeric input")
    elif with_param["ok"]:
        lines.append(
            f"param edit:    edited={with_param['edited']}, "
            f"now={with_param['title']['params']['values']}"
        )
    else:
        lines.append(
            f"param edit:    FAILED {with_param['error']['cause']}\n"
            f"  fix: {with_param['error']['fix']}"
        )
    for one in after["titles"]:
        lines.append(f"  after:  {one['text']!r} @ {one['record']['frames']}")
    return "\n".join(lines)


def test_correlate_measures_a_real_hand_edited_timeline() -> None:
    """#40's live AC: the measurement survives a cut a person made, not one a build made.

    A hand-edited concert timeline is where the reading is actually hard — titles and
    generators with no media pool item, shots that were trimmed rather than placed, an audio
    track that may or may not be the mix the analysis ran on. #142 added the second half:
    the same cut read as the visible edit, where a stacked track and a gap are the two
    things a single-track reading got wrong. Opt in by pointing the two
    variables at a real cut and a real beats file, in PowerShell on the Resolve machine:

        $env:RESOLVE_MCP_CORRELATE_BEATS = 'C:\\cache\\analysis\\gig-beats.json'
        $env:RESOLVE_MCP_CORRELATE_TIMELINE = 'Sunset set 2024'   # optional; open one by default
        $env:RESOLVE_MCP_CORRELATE_AUDIO = 'C:\\audio\\gig.wav'   # optional; adds transients
        uv run pytest -m live -k correlate -s
    """
    beats = os.environ.get(CORRELATE_BEATS_ENV)
    if not beats:
        pytest.skip(f"Set {CORRELATE_BEATS_ENV} to a beats file from a real analysis")

    named = os.environ.get(CORRELATE_TIMELINE_ENV)
    envelope = correlate_timeline(
        beats=beats,
        timeline=named,
        audio=os.environ.get(CORRELATE_AUDIO_ENV),
        refresh=True,
    )
    assert envelope["ok"] is True, envelope.get("error")

    record = wait_for(envelope["job"]["job_id"], timeout=300.0)
    assert record.state == "completed", record.error
    assert record.result is not None
    result = record.result

    written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    cuts = written["cuts"]
    reading = inspect_timeline(timeline=named, detail="clips")
    tracks = reading["tracks"]
    video = [track for track in tracks if track["type"] == "video"]
    on_video_one = next(track for track in video if track["index"] == 1)

    assert [one["t"] for one in cuts] == sorted(one["t"] for one in cuts)
    # #142's AC. The strip runs from the timeline's own first frame — a film that opens on
    # black opens on a shot — and every track that holds picture is somewhere in it: on the
    # #46 recut the top track is where three shots live that a V1 reading never saw at all.
    assert cuts[0]["in"]["frames"] == reading["timeline"]["start"]["frames"]
    measured = set(result["visible"]["measured"])
    assert {one["track"] for one in cuts} <= measured | {None}
    holding = [track["index"] for track in video if track["item_count"]]
    assert max(holding) in {one["track"] for one in cuts}, "the top track is not on screen"
    assert result["beat_offsets"] is not None, "no cut measured against the grid"
    print(_render_correlation(result))

    # The same cut read as one track, which is what the tool did before #142: no overlays,
    # no black, and one shot per item Resolve hands back for V1. Measured without the audio
    # so the transient decode does not run a second time.
    alone = correlate_timeline(beats=beats, timeline=named, track=1, refresh=True)
    assert alone["ok"] is True, alone.get("error")
    one_track = wait_for(alone["job"]["job_id"], timeout=300.0)
    assert one_track.state == "completed", one_track.error
    assert one_track.result is not None
    assert one_track.result["visible"]["mode"] == "track"
    assert one_track.result["cuts"] <= on_video_one["item_count"]  # transitions are not shots
    print(f"track 1:   {one_track.result['cuts']} shots of {on_video_one['item_count']} items")


def _render_correlation(result: dict[str, Any]) -> str:
    """The reading a human puts on the ticket: is this cut on the grid, and how far off?"""
    return "\n".join(
        [
            f"file:      {result['path']}",
            f"alignment: {result['alignment']}",
            f"visible:   {result['visible']}",
            f"cuts:      {result['cuts']} ({result['openings']} opening)",
            f"beats:     {result['beat_offsets']}",
            f"transients:{result['transient_offsets']}",
            f"bars:      {result['bars']}",
            f"shots:     {result['shot_seconds']}",
            f"clips:     {result['clips']}",
        ]
    )


def _smoke_titles(timeline: str, template: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "timeline": timeline,
        "templates": {"title": {"clip": template}},
        "songs": [
            {
                "key": key,
                "events": [
                    {
                        "id": f"smoke-0{position}",
                        "kind": "title",
                        "text": f"Live smoke {position}",
                        "in": 0,
                        "out": TITLE_FRAMES,
                        "fade": {"in": FADE_FRAMES, "out": FADE_FRAMES},
                    }
                ],
            }
            for position, key in enumerate(SMOKE_SONGS, start=1)
        ],
    }


PNG_SONG = "smoke-png-song"
PNG_SEQUENCE_FRAMES = 60
"""What the card is baked to, and therefore exactly what its event may ask for (T11)."""

PNG_STILL_FRAMES = 45
"""What the one-image card is freeze-extended to — a length it does not have on disk."""


def _ramp(index: int) -> int:
    """The alpha of frame ``index``: up over the fade in, full, down over the fade out.

    The same shape a real exporter bakes (#14 §6), so the sequence Resolve imports is a
    card with ramps in it rather than sixty identical squares.
    """
    if index <= FADE_FRAMES:
        return round(255 * index / FADE_FRAMES)
    if index > PNG_SEQUENCE_FRAMES - FADE_FRAMES:
        return round(255 * (PNG_SEQUENCE_FRAMES - index + 1) / FADE_FRAMES)
    return 255


def _write_png(path: Path, alpha: int = 255, size: int = 64) -> None:
    """A real RGBA PNG at the given opacity, so the frames carry an actual alpha channel.

    Hand-rolled rather than a checked-in blob because the sequence needs *many* files and
    Resolve has to read them as an image sequence: a wrong byte would look like a Resolve
    refusal, which is the one failure this test must not be able to fake. ``alpha`` is what
    makes the baked ramp real rather than nominal — whether Resolve *keys* it is a visual
    check no API call can make, and belongs to a human looking at the timeline.
    """
    pixel = bytes((255, 255, 255, alpha))
    raw = b"".join(b"\x00" + pixel * size for _ in range(size))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            len(body).to_bytes(4, "big")
            + kind
            + body
            + zlib.crc32(kind + body).to_bytes(4, "big")
        )

    side = size.to_bytes(4, "big")
    header = side + side + b"\x08\x06\x00\x00\x00"  # 8-bit RGBA, no interlace
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_apply_titles_places_png_cards_at_the_exact_duration_asked_for(tmp_path: Path) -> None:
    """#44 ACs 1-4: the one thing no fake can answer — does Resolve honour ``endFrame`` on
    a freshly imported image once the out point has been written?

    Everything else about the PNG route is a decision and verifies against the fake. This
    is the API behaviour the whole route rests on: without the one-time ``Out`` write both
    cards land at the project's default still duration instead of the length the file asks
    for, and nothing in the return value says so — the durations read back off the track
    are the only evidence. The sequence and the one-image card are both here because they
    reach that behaviour differently: the sequence is placed whole, the still is frozen to
    a length it does not have on disk.

    It needs no Text+ template and no media of yours: the cards are written into a temp
    folder and the scratch timeline is created, marked, titled and deleted again.

    Paste the printed report onto the ticket — that is the record the ticket asks for.
    """
    if get_status()["context"]["project"] is None:
        pytest.skip("No project open in Resolve")

    cards = tmp_path / "cards" / PNG_SONG
    for index in range(1, PNG_SEQUENCE_FRAMES + 1):
        _write_png(cards / f"card_{index:04d}.png", alpha=_ramp(index))
    _write_png(cards / "still.png")

    connection = get_connection()
    pool = media_pool(connection)
    project = connection.handle().GetProjectManager().GetCurrentProject()
    was_on = project.GetCurrentTimeline()

    name = timestamped_name("apply-png-smoke", "", "apply-png-smoke")
    scratch = pool.CreateEmptyTimeline(name)
    assert scratch is not None, f"Resolve created no timeline called {name!r}"
    try:
        assert project.SetCurrentTimeline(scratch), f"Resolve would not open {name!r}"
        start = int(scratch.GetStartFrame())
        # Something on V1 so the timeline has a span for the titles to land inside. The
        # filler is the sequence itself, which is also the first proof that Resolve reads
        # these frames as an image sequence at all.
        cards_bin = ensure_bin(pool, "04_Assets/Text/" + PNG_SONG)
        imported = import_into(
            pool,
            [
                {
                    "FilePath": str(cards / "card_%04d.png"),
                    "StartIndex": 1,
                    "EndIndex": PNG_SEQUENCE_FRAMES,
                }
            ],
            cards_bin,
        )
        assert imported, "Resolve imported nothing for the baked card sequence"
        landed = imported[0]
        apply_still_workaround(landed, properties(landed))
        # Twice over, so V1 spans past the last title: T9 refuses an event that would land
        # outside the timeline, and the timeline is only as long as what is on it.
        assert pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": landed,
                    "startFrame": 0,
                    "endFrame": PNG_SEQUENCE_FRAMES,
                    "mediaType": 1,
                    "trackIndex": 1,
                    "recordFrame": start + offset,
                }
                for offset in (0, PNG_SEQUENCE_FRAMES)
            ]
        ), "the imported sequence would not append — Resolve did not read it as a sequence"
        assert scratch.AddMarker(0, "Blue", PNG_SONG, "png smoke", 1), (
            "Resolve refused the blue marker the song is anchored to"
        )

        file = tmp_path / "titles.json"
        file.write_text(json.dumps(_smoke_png_titles(name, cards)), encoding="utf-8")

        result = apply_titles(str(file))
        again = apply_titles(str(file))

        print("\n" + _render_png_titles(result, again))
        assert result["ok"] is True, result.get("error")
        assert [one["route"] for one in result["placed"]] == ["png", "png"]
        assert result["placed"][0]["fade"]["detail"] == "baked into the exported frames"
        # The claim itself: what the file asked for is what stands on the track.
        placed = inspect_timeline(name, detail="clips")
        titles = [track for track in placed["tracks"] if track["name"] == "Titles"][0]
        assert [item["record"]["duration"]["frames"] for item in titles["items"]] == [
            PNG_SEQUENCE_FRAMES,
            PNG_STILL_FRAMES,
        ]
        assert again["ok"] is True, again.get("error")
        assert again["cleared"] == 2, "a second apply must replace the cards, not stack them"
    finally:
        if was_on is not None:
            project.SetCurrentTimeline(was_on)
        pool.DeleteTimelines([scratch])
        # The cards go too. They are a temp folder's worth of 64-pixel squares, and a bin
        # left behind would make the *next* run of this test ambiguous rather than failing.
        pool.DeleteFolders([ensure_bin(pool, "04_Assets/Text/" + PNG_SONG).folder])


def _smoke_png_titles(timeline: str, cards: Path) -> dict[str, Any]:
    """A PNG-only titles file: the sequence card, then the one-image card held after it."""
    return {
        "schema": 1,
        "timeline": timeline,
        "songs": [
            {
                "key": PNG_SONG,
                "events": [
                    {
                        "id": "png-01",
                        "kind": "title",
                        "route": "png",
                        "asset": str(cards / "card_%04d.png"),
                        "in": 0,
                        "out": PNG_SEQUENCE_FRAMES,
                        # Baked, not written: the ramps are in the frames the exporter made,
                        # so this only has to fit inside the card and be reported as such.
                        "fade": {"in": FADE_FRAMES, "out": FADE_FRAMES},
                    },
                    {
                        "id": "png-02",
                        "kind": "personnel",
                        "route": "png",
                        "asset": str(cards / "still.png"),
                        "in": PNG_SEQUENCE_FRAMES,
                        "out": PNG_SEQUENCE_FRAMES + PNG_STILL_FRAMES,
                    },
                ],
            }
        ],
    }


def _render_png_titles(first: dict[str, Any], second: dict[str, Any]) -> str:
    """The report the ticket asks for: what landed, at what length, out of which bin."""
    if not first["ok"]:
        return f"apply failed: {first['error']['cause']}\nfix: {first['error']['fix']}"
    lines = [
        f"timeline:      {first['timeline']['name']}",
        f"track:         {first['track']['name']} "
        f"(video {first['track']['index']}, created={first['track']['created']})",
        f"second apply:  cleared {second.get('cleared')}, ok={second['ok']}",
    ]
    for one in first["placed"]:
        lines.append(
            f"  {one['id']}: {Path(one['asset']).name} @ {one['record']['frames']} for "
            f"{one['duration']['frames']}f — {one['frames']} frame(s) on disk, bin "
            f"{one['bin']!r}, fade {one['fade']['in']}/{one['fade']['out']} "
            f"({one['fade']['detail']})"
        )
    return "\n".join(lines)


def _render_titles(first: dict[str, Any], second: dict[str, Any]) -> str:
    """The report the ticket asks for: what landed, and what the fades said."""
    if not first["ok"]:
        return f"apply failed: {first['error']['cause']}\nfix: {first['error']['fix']}"
    lines = [
        f"timeline:      {first['timeline']['name']}",
        f"track:         {first['track']['name']} "
        f"(video {first['track']['index']}, created={first['track']['created']})",
        f"second apply:  cleared {second.get('cleared')}, ok={second['ok']}",
    ]
    for one in first["placed"]:
        lines.append(
            f"  {one['id']}: {one['text']!r} @ {one['record']['frames']} for "
            f"{one['duration']['frames']}f — node {one['node']['name']!r} "
            f"({one['node']['text_plus_in_comp']} Text+ in comp), fade "
            f"{one['fade']['in']}/{one['fade']['out']} verified={one['fade']['verified']} "
            f"({one['fade']['detail']})"
        )
    return "\n".join(lines)
