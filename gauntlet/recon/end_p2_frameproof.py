"""Prove the render's pixels are the authored source frames (G3's lesson).

For a sample of window times, decode the render's frame and the frame the cut file says
should be there, straight off the camera original, and report the mean absolute
difference. A match is a few grey levels (the render is graded-neutral but re-encoded);
a wrong angle or a wrong frame is tens. READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_ending_p2r1.mp4"
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending.cut.json"
OUT = HERE / "end_p2_frameproof.json"

FPS = 24000.0 / 1001.0
REC_IN = 181733
PATHS = {
    "fx6_wide": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF",
    "a7iv_kit": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4",
}
SAMPLES = [3.0, 9.0, 20.0, 27.0, 33.0, 39.0, 46.0, 52.0, 58.0, 64.0, 70.0, 74.5, 80.0]
W, H = 192, 108


def frame(path: str, t_s: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t_s:.4f}", "-i", path, "-frames:v", "1",
           "-vf", f"scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout[: W * H]
    return np.frombuffer(raw, dtype=np.uint8).reshape(H, W).astype(np.float64)


def main() -> None:
    doc = json.loads(CUT.read_text(encoding="utf-8"))
    zero = {a: s["sync_offset"] for a, s in doc["sources"].items() if a in PATHS}
    spans, acc = [], 0
    for s in doc["segments"]:
        length = s.get("gap") or (s["out"] - s["in"])
        spans.append((acc, acc + length, s))
        acc += length

    rows = []
    for t in SAMPLES:
        f = round(t * FPS)
        seg = next(s for a, b, s in spans if a <= f < b)
        if "gap" in seg:
            continue
        start = next(a for a, b, s in spans if s is seg)
        src_frame = seg["in"] + (f - start)
        want = frame(PATHS[seg["source"]], src_frame / FPS)
        got = frame(str(RENDER), f / FPS)
        # the same instant on the other angle, as a control
        other = "a7iv_kit" if seg["source"] == "fx6_wide" else "fx6_wide"
        ctrl = frame(PATHS[other], (REC_IN + f - zero[other]) / FPS)
        rows.append({
            "t": t, "id": seg["id"], "source": seg["source"], "src_frame": src_frame,
            "diff_authored": round(float(np.abs(want - got).mean()), 2),
            "diff_other_angle": round(float(np.abs(ctrl - got).mean()), 2),
        })
        print(f"t {t:6.1f}  {seg['id']:4} {seg['source']:9} src {src_frame:6d}   "
              f"authored {rows[-1]['diff_authored']:6.2f}   "
              f"other angle {rows[-1]['diff_other_angle']:6.2f}", flush=True)
    OUT.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    worst = max(r["diff_authored"] for r in rows)
    closest_wrong = min(r["diff_other_angle"] for r in rows)
    print(f"worst authored-frame difference {worst:.2f}; "
          f"closest wrong-angle difference {closest_wrong:.2f}", flush=True)


if __name__ == "__main__":
    main()
