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
import subprocess
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


def decode_all(clip: Path) -> np.ndarray:
    """The whole clip on the framing grid at its native rate, as uint8.

    uint8 rather than ab_pack's float64: a six-minute song is 8k frames, which is
    80 MB of bytes and 640 MB of doubles, and every consumer divides by 255 anyway.
    """
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(clip),
        "-an",
        "-vf",
        f"scale={framing.GRID_WIDTH}:{framing.GRID_HEIGHT}",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 and not proc.stdout:
        sys.exit(f"error: grey decode failed\n{proc.stderr[-2000:].decode('utf-8', 'replace')}")
    stride = framing.GRID_WIDTH * framing.GRID_HEIGHT
    n = len(proc.stdout) // stride
    return np.frombuffer(proc.stdout[: n * stride], dtype=np.uint8).reshape(
        n, framing.GRID_HEIGHT, framing.GRID_WIDTH
    )


def read_clip(name: str, clip: Path) -> dict[str, Any]:
    """Every detected cut in one clip, read across its boundary."""
    fps = ab_pack.probe_fps(clip)
    duration = ab_pack.probe_duration(clip)
    cuts = ab_pack.detect_cuts(clip)
    frames = decode_all(clip)
    print(f"  {name}: {duration:.1f} s, {fps:.3f} fps, {len(frames)} frames, {len(cuts)} cuts")

    rows: list[dict[str, Any]] = []
    skipped: list[float] = []
    for index, t in enumerate(cuts):
        at = int(round(t * fps))
        try:
            reading = framing.read_boundary(frames, at)
        except ValueError:
            skipped.append(t)
            continue
        rows.append({"index": index, "t": t, **reading.as_record()})

    readings = [
        framing.Delta(
            delta=r["delta"],
            content=r["content"],
            layout=r["layout"],
            scale=r["scale"],
            shift_x=r["shift_x"],
            shift_y=r["shift_y"],
            jump_cut=r["jump_cut"],
            reason=r["reason"],
        )
        for r in rows
    ]
    lowest = sorted(rows, key=lambda r: r["delta"])[:SHOW_LOWEST]
    return {
        "clip": str(clip),
        "fps": round(fps, 3),
        "duration_sec": round(duration, 3),
        "cuts_detected": len(cuts),
        "cuts_read": len(rows),
        "cuts_skipped_at_edges": skipped,
        "summary": framing.summarize(readings),
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

    pooled = [
        row["delta"] for song in report["human"].values() for row in song["cuts"]
    ]
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
            "under_threshold": int((values < framing.JUMP_DELTA).sum()),
            "threshold": framing.JUMP_DELTA,
        }
        print("\nhuman pooled:", json.dumps(report["human_pooled"], indent=2))

    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
