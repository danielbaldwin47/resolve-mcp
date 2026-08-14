"""Scene-detect an mp4 at ab_pack's calibrated threshold and print shot lengths.

Usage: python scene_probe.py <file> [duration_s] [start_s]
"""

from __future__ import annotations

import re
import subprocess
import sys

THRESHOLD = 0.10
SCALE_W = 320


def detect(path: str, dur: float | None = None, start: float = 0.0) -> list[float]:
    cmd = ["ffmpeg", "-v", "info", "-nostats"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += [
        "-i", path,
        "-vf", f"scale={SCALE_W}:-2,select='gt(scene,{THRESHOLD})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def main() -> None:
    path = sys.argv[1]
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else None
    start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    ts = detect(path, dur, start)
    print(f"FILE {path}")
    print(f"CUTS {len(ts)}")
    prev = 0.0
    for t in ts:
        print(f"  t={t:7.3f}  shot_before={t - prev:6.2f}")
        prev = t
    if dur:
        print(f"  tail={dur - prev:6.2f}")


if __name__ == "__main__":
    main()
