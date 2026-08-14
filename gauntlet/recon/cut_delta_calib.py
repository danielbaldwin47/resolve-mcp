"""Calibrate the per-cut visual delta threshold on the human deliverables. READ-ONLY.

The flag in `resolve_mcp.video.framing` says "this cut barely changed the picture".
What counts as barely is not a number anyone can pick from a chair: it has to sit
under the human editor's own cuts, because those are the ones a judge accepted, and
above the near-jump-cut the fixture tier fixes. This script produces the evidence.

Each deliverable is decoded once to the framing grid at native rate, its cuts come
from the same ffmpeg scene detector the A/B pack uses, and every boundary is read.
The output is the whole distribution -- per-song quantiles and the lowest handful of
cuts by name -- not just a minimum, because one dissolve mis-detected as a hard cut
would otherwise set the threshold for the repo.

Usage: uv run python gauntlet/recon/cut_delta_calib.py [--songs N] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

from resolve_mcp.video import framing  # noqa: E402

HUMAN_DIR = Path(r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
OURS_DIR = ROOT / "gauntlet" / "renders"
OUT = ROOT / "gauntlet" / "recon" / "cut_delta_calib.json"

# The lowest-scoring cuts are the interesting ones -- the threshold lives among them.
SHOW_LOWEST = 8

# The rule that turns the distribution into a number, so the threshold is derived
# rather than chosen.
#
# Setting it *at* the bottom of the human's distribution would be the obvious move
# and is the wrong one. Their cuts are all between genuinely different cameras, so
# their floor is the floor of *real angle changes* -- a threshold there would flag
# every cut that merely steps less than their smallest step, which is a report on
# the footage, not on the edit. A jump cut is not a small step; it is a step of
# almost nothing, an order below anything here. So the threshold goes at half the
# human floor: a cut has to change the picture less than half as much as the least
# of their cuts before it is worth a human's eye. Both ends of the margin are in
# the receipt -- the human floor above it, the synthetic near-jump-cuts the fixture
# tier fixes (~0.01-0.05) well below.
THRESHOLD_MARGIN = 0.5
THRESHOLD_GRID = 0.05
THRESHOLD_FLOOR, THRESHOLD_CEILING = 0.10, 0.40
RULE = (
    f"{THRESHOLD_MARGIN:g} x the human pooled minimum, rounded down to a "
    f"{THRESHOLD_GRID} grid and clamped to "
    f"[{THRESHOLD_FLOOR}, {THRESHOLD_CEILING}]"
)


def recommend(values: np.ndarray) -> float:
    """The threshold the pooled human distribution asks for, by RULE."""
    margin = float(values.min()) * THRESHOLD_MARGIN
    grid = math.floor(margin / THRESHOLD_GRID) * THRESHOLD_GRID
    return round(min(THRESHOLD_CEILING, max(THRESHOLD_FLOOR, grid)), 2)


def decode_all(clip: Path) -> np.ndarray:
    """The whole clip on the pack's boundary grid at its native rate, as uint8.

    One decode for the whole song rather than one per cut: seeking a 2-4 GB file
    forty times costs more than reading it once, and every boundary window is then
    a slice. uint8 because a six-minute song is 8k frames -- 80 MB of bytes against
    640 MB of doubles -- and each window is widened to float where it is read.
    """
    return ab_pack.decode_grey(
        clip, ab_pack.TRANS_W, ab_pack.TRANS_H, dtype=np.uint8
    )


def read_clip(name: str, clip: Path) -> dict[str, Any]:
    """Every detected cut in one clip, read exactly as a pack build would read it.

    The pack types each boundary and then reads the delta across the ends that
    typing found; the calibration has to do the same or it calibrates a threshold
    against numbers production never produces. A deliverable's dissolves are the
    case that matters: read across a fixed guard, their blend frames sit on both
    sides and the two shots score as versions of each other, dragging the human's
    own distribution down towards the flag.
    """
    fps = ab_pack.probe_fps(clip)
    duration = ab_pack.probe_duration(clip)
    cuts = ab_pack.detect_cuts(clip)
    frames = decode_all(clip)
    print(f"  {name}: {duration:.1f} s, {fps:.3f} fps, {len(frames)} frames, {len(cuts)} cuts")

    rows: list[dict[str, Any]] = []
    readings: list[framing.Delta] = []
    unread: list[dict[str, Any]] = []
    kinds: dict[str, int] = {}
    for index, t in enumerate(cuts):
        at = int(round(t * fps))
        half = ab_pack.TRANS_HALF_FRAMES
        window = frames[max(0, at - half) : at + half + 1].astype(np.float64)
        transition = ab_pack.transition_from(window, max(t - half / fps, 0.0))
        kind = str(transition.get("type"))
        kinds[kind] = kinds.get(kind, 0) + 1
        reading = ab_pack.cut_delta(window, transition)
        if reading is None:
            unread.append({"index": index, "t": t, "transition": kind})
            continue
        readings.append(reading)
        rows.append({"index": index, "t": t, "transition": kind, **reading.as_record()})

    lowest = sorted(rows, key=lambda r: r["delta"])[:SHOW_LOWEST]
    return {
        "clip": str(clip),
        "fps": round(fps, 3),
        "duration_sec": round(duration, 3),
        "cuts_detected": len(cuts),
        "transition_types": kinds,
        "summary": framing.summarize(readings, unread=len(unread)),
        "unread": unread,
        "lowest": lowest,
        "cuts": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=int, default=0, help="cap the number of deliverables")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    humans = sorted(HUMAN_DIR.glob("*.mp4"))
    if args.songs:
        humans = humans[: args.songs]
    ours = sorted(OURS_DIR.glob("*.mp4")) if OURS_DIR.exists() else []

    report: dict[str, Any] = {"human": {}, "ours": {}}
    print(f"human deliverables ({len(humans)}):")
    for clip in humans:
        report["human"][clip.stem] = read_clip(clip.stem, clip)
    print(f"our renders ({len(ours)}):")
    for clip in ours:
        report["ours"][clip.stem] = read_clip(clip.stem, clip)

    pooled = [row["delta"] for song in report["human"].values() for row in song["cuts"]]
    if pooled:
        values = np.asarray(pooled, dtype=np.float64)
        report["human_pooled"] = {
            "cuts": len(pooled),
            "min": round(float(values.min()), 4),
            "p01": round(float(np.quantile(values, 0.01)), 4),
            "p05": round(float(np.quantile(values, 0.05)), 4),
            "p10": round(float(np.quantile(values, 0.10)), 4),
            "median": round(float(np.median(values)), 4),
            "max": round(float(values.max()), 4),
            "recommended_threshold": recommend(values),
            "rule": RULE,
            "in_force": framing.JUMP_DELTA,
            "flagged_at_in_force": int((values < framing.JUMP_DELTA).sum()),
        }
        print("\nhuman pooled:", json.dumps(report["human_pooled"], indent=2))

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
