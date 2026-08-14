"""Sweep the ab_pack scene threshold over the r1 render and the pack clips.

Ground truth for the render: 13 items, so 12 angle changes + the black lift.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PTS = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")

TARGETS = {
    "render_full": ROOT / "gauntlet" / "renders" / "taurus_opening_r1.mp4",
    "pack_A": ROOT / "gauntlet" / "packs" / "taurus_opening_r1" / "A" / "clip.mp4",
    "pack_B": ROOT / "gauntlet" / "packs" / "taurus_opening_r1" / "B" / "clip.mp4",
}
# ground-truth cut seconds from the built v3 timeline items (rec_start - 86400)/23.976
TRUTH = [0.542, 4.963, 11.595, 18.811, 22.939, 32.616, 36.661, 49.049,
         53.094, 61.269, 63.772, 71.238, 76.076]


def detect(clip: Path, thr: float, scale: int) -> list[float]:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(clip), "-an",
         "-filter:v", f"scale={scale}:-2,select='gt(scene,{thr})',metadata=print",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blob = p.stdout + "\n" + p.stderr
    return sorted({round(float(m), 3) for m in PTS.findall(blob)})


def score(found: list[float]) -> tuple[int, int]:
    hits = sum(1 for t in TRUTH if any(abs(t - f) < 0.35 for f in found))
    extra = sum(1 for f in found if not any(abs(t - f) < 0.35 for t in TRUTH))
    return hits, extra


def main() -> None:
    for name, path in TARGETS.items():
        if not path.exists():
            print(name, "MISSING", path)
            continue
        for scale in (320, 640):
            for thr in (0.02, 0.04, 0.06, 0.08, 0.12, 0.18, 0.27):
                cuts = detect(path, thr, scale)
                line = f"{name:12} scale{scale} thr{thr:<5} n={len(cuts):3}"
                if name != "pack_B":
                    h, e = score(cuts)
                    line += f"  truth_hit {h}/13 extra {e}"
                print(line, flush=True)
                if name != "pack_B" and thr in (0.04, 0.06):
                    print("    ", cuts[:20], flush=True)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
