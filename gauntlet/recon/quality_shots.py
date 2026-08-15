"""Image quality per shot on a real cut, and per window on a real angle. READ-ONLY.

#182's first acceptance criterion, on the two surfaces it names. The calibration receipt
(`image_quality_calib.py`) says the readings separate good footage from bad; this one says
they land somewhere useful when pointed at the two things a builder actually asks about.

* **Per shot** — the Taurus People deliverable, cut by the human editor. Its shots come from
  the same scene detector the A/B pack uses, and each gets the summary `analysis.correlate`
  puts on a shot and `ab_pack` puts on a shot doc. What to look for: shots that differ from
  each other, and a verdict that does not veto a delivered cut.
* **Per window** — one angle out of the open Resolve project, scanned through the tool the
  agent would call, `analyze_quality`. What to look for: windows that are stretches rather
  than a scatter of quarter-seconds.

The angle half needs Resolve up with a project open; without it that half is skipped and the
per-shot half still runs.

Usage: uv run python gauntlet/recon/quality_shots.py [--song NAME] [--angle-seconds N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

from resolve_mcp.jobs.runner import wait_for  # noqa: E402
from resolve_mcp.tools.media import inspect_clip, list_media  # noqa: E402
from resolve_mcp.tools.video import analyze_quality  # noqa: E402
from resolve_mcp.video import picture  # noqa: E402

HUMAN_DIR = Path(r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
SONG = "Taurus People"
OUT = ROOT / "gauntlet" / "recon" / "quality_shots.json"

ANGLE_SECONDS = 120.0
"""How much of one angle to scan. A song's worth would be the real call; two minutes is
enough to see whether the windows are stretches, at a fraction of the decode."""


def per_shot(song: Path) -> dict[str, Any]:
    """Every shot of a delivered cut, scored the way a pack scores one."""
    duration = ab_pack.probe_duration(song)
    cuts = ab_pack.detect_cuts(song)
    shots = ab_pack.shots_from_cuts(cuts, duration)
    track = ab_pack.quality_track(song)

    rows = []
    for index, (start, end) in enumerate(shots, start=1):
        found = ab_pack.shot_quality(track, start, end)
        rows.append(
            {
                "shot": index,
                "start": round(start, 3),
                "seconds": round(end - start, 3),
                **found,
            }
        )
    measured = [one for one in rows if one.get("samples")]
    return {
        "song": song.stem,
        "duration_sec": round(duration, 2),
        "cuts": len(cuts),
        "shots": len(rows),
        "floors": ab_pack.QUALITY_FLOORS._asdict(),
        "unusable_shots": [one["shot"] for one in measured if not one.get("usable", True)],
        "sharpness_range": [
            min((one["sharpness"] for one in measured), default=None),
            max((one["sharpness"] for one in measured), default=None),
        ],
        "stability_range": [
            min((one["stability"] for one in measured if one["stability"] is not None), default=None),
            max((one["stability"] for one in measured if one["stability"] is not None), default=None),
        ],
        "shots_detail": rows,
    }


def per_window(seconds: float) -> dict[str, Any]:
    """One real angle out of the open project, scanned through the agent-facing tool."""
    listing = list_media()
    if not listing["ok"]:
        return {"skipped": "no project open in Resolve", "error": listing.get("error")}
    footage = next(
        (
            one
            for one in listing["clips"]
            if one.get("file_path") and one.get("fps") and not one.get("offline")
        ),
        None,
    )
    if footage is None:
        return {"skipped": "no online clip with a frame rate in the media pool"}

    bounds = inspect_clip(footage["name"], bin=footage["bin"])["bounds"]["media"]
    fps = float(bounds["in"]["fps"] or 25.0)
    first = int(bounds["in"]["frames"]) + int(bounds["duration"]["frames"]) // 3
    last = min(int(bounds["out"]["frames"]), first + int(fps * seconds))

    started = analyze_quality(footage["name"], bin=footage["bin"], start=first, end=last)
    if not started["ok"]:
        return {"skipped": "the scan was refused", "error": started.get("error")}
    record = wait_for(started["job_id"], timeout=1800.0)
    if record.state != "completed" or record.result is None:
        return {"skipped": f"the scan {record.state}", "error": record.error}

    gist = record.result
    catalog = json.loads(Path(gist["path"]).read_text(encoding="utf-8"))
    return {
        "clip": footage["name"],
        "bin": footage["bin"],
        "range_frames": [first, last],
        "seconds": round((last - first) / fps, 2),
        "sample_fps": gist["sample_fps"],
        "decode": gist["decode"],
        "samples": gist["samples"],
        "unusable_samples": gist["unusable_samples"],
        "quality": gist["quality"],
        "windows": gist["windows"],
        "worst_windows": gist["worst_windows"],
        "window_seconds": [one["duration_seconds"] for one in catalog["windows"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song", default=SONG)
    parser.add_argument("--angle-seconds", type=float, default=ANGLE_SECONDS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    songs = [one for one in sorted(HUMAN_DIR.glob("*.mp4")) if args.song.lower() in one.stem.lower()]
    if not songs:
        print(f"error: no deliverable matching {args.song!r} under {HUMAN_DIR}", file=sys.stderr)
        return 1

    receipt = {
        "question": "does the image-quality reading land somewhere useful per shot on a real "
        "cut and per window on a real angle (#182 AC1)",
        "generated_by": "gauntlet/recon/quality_shots.py",
        "grid": f"{picture.GRID_WIDTH}x{picture.GRID_HEIGHT}",
        "per_shot": per_shot(songs[0]),
        "per_window": per_window(args.angle_seconds),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    shot = receipt["per_shot"]
    print(
        f"per shot: {shot['shots']} shots, sharpness {shot['sharpness_range']}, "
        f"stability {shot['stability_range']}, unusable {shot['unusable_shots']}"
    )
    print(f"per window: {json.dumps({k: v for k, v in receipt['per_window'].items() if k != 'worst_windows'})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
