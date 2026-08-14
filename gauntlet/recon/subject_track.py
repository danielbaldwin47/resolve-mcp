"""LIVE check of the on-soloist track (#181) against a Taurus People cut.

READ-ONLY as far as Resolve is concerned: it lists timelines and measures one.
Everything it writes goes to subject_track.json next to this file; the frame
grabs for the spot check land in the media cache grab_frames already owns, and
the receipt names their paths.

    uv run python gauntlet/recon/subject_track.py [--timeline NAME]

Ground truth for the spot check is a frame of the shot itself: the sidecar's
labels were read off frames of each source clip, so a shot the track calls
"drums" should be a frame of the drum kit.
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("subject_track.json")
SIDECAR = Path(__file__).resolve().parents[2] / "styles" / "angles" / "mcp-tests-zinc.json"
ANALYSIS = Path.home() / "AppData" / "Local" / "resolve-mcp" / "analysis"
BEATS = ANALYSIS / "Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"
SOLOS = ANALYSIS / "Zinc-Set-2-Reaper-v4-0f3a70a16e52-solos.json"
SAMPLE = 8  # how many shots to grab a frame for
FOOTAGE_BIN = "Zinc Bar/Footage"
"""Where this night's cameras live, for the clip names the whole pool holds more than once."""

DEFAULT_TIMELINES = ("Taurus People Opening R3 v3", "Taurus People Full P4 R2 v3")
"""The two cuts the gauntlet closed pieces on: the R3 opening and the full-song capstone."""

report: dict[str, Any] = {"errors": [], "timelines": [], "inputs": {}}


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {"where": where, "type": type(exc).__name__, "message": str(exc),
         "traceback": traceback.format_exc(limit=6)}
    )


def timelines() -> list[str]:
    from resolve_mcp.resolve.connection import get_connection

    project = get_connection().handle().GetProjectManager().GetCurrentProject()
    found = []
    for index in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is not None:
            found.append(str(timeline.GetName()))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeline", action="append", default=None, help="timeline to measure")
    args = parser.parse_args()

    from resolve_mcp import interpreter as interp

    interp.ensure_supported()

    report["timelines"] = timelines()
    names = args.timeline or [one for one in DEFAULT_TIMELINES if one in report["timelines"]]
    if not names:
        OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("none of the default timelines are in this project; wrote the list to", OUT)
        return

    angles = json.loads(SIDECAR.read_text(encoding="utf-8"))["angles"]
    report["inputs"] = {
        "timelines": names,
        "sidecar": str(SIDECAR),
        "beats": str(BEATS),
        "solos": str(SOLOS),
        "subjects": {clip: entry.get("subject") for clip, entry in angles.items()},
    }
    report["measured"] = [measure(name, angles) for name in names]
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("wrote", OUT)


def measure(name: str, angles: dict[str, Any]) -> dict[str, Any]:
    """One timeline: the reading, every shot's subject columns, and the spot-check grabs."""
    from resolve_mcp.analysis import correlate
    from resolve_mcp.jobs.runner import wait_for
    from resolve_mcp.resolve.connection import get_connection

    measured: dict[str, Any] = {"timeline": name}
    started = correlate.correlate_timeline(
        get_connection(),
        beats=str(BEATS),
        timeline=name,
        solos=str(SOLOS),
        angles=angles,
        refresh=True,
    )
    record = wait_for(started["job_id"])
    result = record.result or {}
    measured["job"] = {"state": record.state, "error": record.error}
    measured["reading"] = {
        key: result.get(key)
        for key in ("alignment", "cuts", "openings", "on_soloist", "subjects", "roles", "solos")
    }

    path = result.get("path")
    measured["cuts_file"] = path
    if not path:
        return measured

    cuts = json.loads(Path(path).read_text(encoding="utf-8"))["cuts"]
    measured["shots"] = [
        {
            key: shot.get(key)
            for key in ("cut", "clip", "t", "seconds", "role", "subject", "subject_kind",
                        "front", "on_soloist", "on_soloist_seconds")
        }
        for shot in cuts
    ]

    # The spot check. Every camera on this rig is locked -- the sidecar's own labels were read
    # off frames of these clips -- so what a shot is framed on is a fact about the camera it
    # came from, and a frame of that clip is the ground truth for every shot that used it.
    # Grabs are taken inside the clip's own bounds rather than at the shot's time: the shot's
    # time counts from the master mix's zero and a clip's frames count from its own, and the
    # rebase between them (G6) is the thing this check must not depend on.
    from resolve_mcp.tools import media as media_tools
    from resolve_mcp.tools import video as video_tools

    held: dict[str, list[int]] = {}
    for shot in cuts:
        clip = shot.get("clip")
        if clip is not None:
            held.setdefault(str(clip), []).append(int(shot["cut"]))

    grabs = []
    for clip, used in held.items():
        first = next(one for one in cuts if one.get("clip") == clip)
        try:
            # The pool holds several copies of some of these names -- the same camera roll
            # under two projects' bins, the same card imported twice -- so a bare name is
            # ambiguous and the footage bin is what disambiguates it.
            info = media_tools.inspect_clip(clip=clip)
            bin_path = None
            if not info.get("ok", True):
                bin_path = FOOTAGE_BIN
                info = media_tools.inspect_clip(clip=clip, bin=bin_path)
            bounds = info.get("bounds") or (info.get("clip") or {}).get("bounds") or {}
            media_bounds = bounds.get("media") or bounds
            span = (
                float(media_bounds["in"]["seconds"]),
                float(media_bounds["out"]["seconds"]),
            )
            at = [round(span[0] + (span[1] - span[0]) * where, 2) for where in (0.25, 0.75)]
            grabbed = video_tools.grab_frames(
                clip=clip, bin=bin_path, times=[{"seconds": one, "snap": "floor"} for one in at]
            )
        except Exception as exc:  # noqa: BLE001 - a receipt of the failure is the point
            note(f"grab {clip}", exc)
            continue
        grabs.append(
            {
                "clip": clip,
                "shots": used[:SAMPLE],
                "shot_count": len(used),
                "claimed_subject": first.get("subject"),
                "claimed_kind": first.get("subject_kind"),
                "bin": bin_path,
                "grabbed_at_sec": at,
                "frames": grabbed.get("frames"),
                "ok": grabbed.get("ok"),
                "error": grabbed.get("error"),
            }
        )
    measured["spot_check"] = grabs
    # A refusal comes back in the envelope rather than as an exception, so it never reaches
    # the errors list: a receipt whose errors are empty while half its grabs failed reads as
    # a clean check. Name the failures at the top of the check instead.
    measured["spot_check_failed"] = [
        {"clip": one["clip"], "cause": (one.get("error") or {}).get("cause")}
        for one in grabs
        if not one.get("ok")
    ]
    return measured


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - the receipt is the point
        note("main", exc)
        OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise
