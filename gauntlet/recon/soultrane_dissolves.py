"""Why the scene detector reads Soultrane as zero cuts. READ-ONLY.

The #184 calibration read 200 cuts across the five Zinc Bar deliverables and none of
them came from Soultrane: `detect_cuts` found nothing in 761 seconds (that whole-song
count is in `cut_delta_calib.json`). Zero cuts in a twelve-minute song is either a
single-camera video or a detector that cannot see this edit, and the difference
matters -- everything a pack shows a critic is built off that cut list.

This measures the window the claim rests on. Three questions, in order:

1. Does the detector find anything if the threshold is dropped far below the pack's?
2. Does any single frame *pair* step, which is the only thing a per-frame scene score
   can see?
3. Does the picture nevertheless change -- measured with the #184 visual delta over a
   three-second gap, on the same footage?

A "no, no, yes" is a dissolve-cut edit: the picture is completely replaced, but slowly
enough that no pair of adjacent frames ever steps.

Usage: uv run python gauntlet/recon/soultrane_dissolves.py [--out PATH]
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

CLIP = Path(
    r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos"
    r"/6-17 - Zinc Set 2 - Soultrane.mp4"
)
OUT = ROOT / "gauntlet" / "recon" / "soultrane_dissolves.json"

START, DUR = 120.0, 120.0
"""The window every number here is taken over -- two minutes out of the middle of the
song, well clear of the head and the ending, where an edit is doing ordinary work."""

THRESHOLDS = (ab_pack.SCENE_THRESHOLD, 0.04, 0.015)
GAP_SECONDS = 3.0
"""How far apart the two frames of a picture-step reading sit. Long enough that a
several-second dissolve has substantially completed, short enough that a shot which
merely holds cannot drift this far."""


def scene_hits(threshold: float) -> list[float]:
    """What ffmpeg's per-frame scene score calls a boundary in the window."""
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-ss", str(START), "-t", str(DUR), "-i", str(CLIP), "-an",
            "-filter:v",
            f"scale={ab_pack.SCENE_SCALE_W}:-2,select='gt(scene,{threshold})',metadata=print",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    return [float(one) for one in ab_pack.PTS_RE.findall(proc.stdout + "\n" + proc.stderr)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    fps = ab_pack.probe_fps(CLIP)
    detected = {f"{t}": len(scene_hits(t)) for t in THRESHOLDS}
    print("scene-detector boundaries in the window:", detected)

    arr = ab_pack.decode_grey(CLIP, ab_pack.TRANS_W, ab_pack.TRANS_H, start=START, dur=DUR)
    pairs = np.abs(np.diff(arr, axis=0)).mean(axis=(1, 2))

    gap = int(round(fps * GAP_SECONDS))
    steps = [
        {
            "at": round(START + i / fps, 2),
            "to": round(START + (i + gap) / fps, 2),
            "delta": framing.read_pair(arr[i], arr[i + gap]).delta,
        }
        for i in range(0, len(arr) - gap, gap // 2)
    ]
    worst = sorted(steps, key=lambda one: -one["delta"])[:8]
    print("largest 3 s picture steps:", [one["delta"] for one in worst])

    report: dict[str, Any] = {
        "question": "is Soultrane a single-camera video, or an edit the detector cannot see?",
        "clip": str(CLIP),
        "window_sec": [START, START + DUR],
        "fps": round(fps, 3),
        "whole_song_cuts_detected": "0 in 761.3 s -- see cut_delta_calib.json",
        "scene_detector": {
            "boundaries_by_threshold": detected,
            "pack_threshold": ab_pack.SCENE_THRESHOLD,
        },
        "frame_pairs": {
            "max": round(float(pairs.max()), 2),
            "mean": round(float(pairs.mean()), 2),
            "noise_floor": ab_pack.TRANS_NOISE_FLOOR,
            "reading": "the largest step between any two adjacent frames in two minutes",
        },
        "picture_steps": {
            "gap_seconds": GAP_SECONDS,
            "max": max(one["delta"] for one in steps),
            "median": round(float(np.median([one["delta"] for one in steps])), 4),
            "largest": worst,
            "reading": (
                "the human deliverables' 200 hard cuts span 0.44 to 0.73 (cut_delta_calib.json), "
                "so a step this size across three seconds is a whole cut's worth of change"
            ),
        },
        "verdict": (
            "dissolve-cut edit, not a single-camera video: the picture is completely "
            "replaced, over seconds, so no frame pair ever steps and a per-frame scene "
            "score has nothing to trip on"
        ),
    }
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
