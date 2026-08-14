"""Does the render show the camera each segment says it does? Match every shot to both angles.

Same instrument as r2_human_angles.py pointed at our own render: for each of the 14 segments,
grab the render frame at the shot's midpoint and the two camera frames at the same Zinc SYNC
frame, and confirm the intended camera wins the normalised cross-correlation. This catches a
source mix-up or a sync slip that the scene-detect pixel check cannot see.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PLAN = json.loads((HERE / "mid_p3r2_plan.json").read_text(encoding="utf-8"))
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_mid_p3r2.mp4"
OUT = HERE / "mid_p3r2_frameproof.json"

FPS = 24000.0 / 1001.0
SYNC_T0 = 175955
CAMS = {
    "fx6": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF", 117576),  # noqa: E501
    "a7iv": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4", 86306),  # noqa: E501
}
W, H = 64, 36


def frame(path: str, t: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
           "-vf", f"scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw[: W * H], dtype=np.uint8).reshape(H, W).astype(np.float64)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))


def main() -> None:
    rows, bad = [], 0
    for i, seg in enumerate(PLAN["segments"], start=1):
        mid = (seg["in_t"] + seg["out_t"]) / 2.0
        sync = SYNC_T0 + round(mid * FPS)
        ref = frame(str(RENDER), mid)
        scores = {n: ncc(ref, frame(p, (sync - z) / FPS)) for n, (p, z) in CAMS.items()}
        pick = max(scores, key=lambda k: scores[k])
        ok = pick == seg["angle"]
        bad += 0 if ok else 1
        rows.append({"id": f"s{i:02d}", "t": round(mid, 2), "intended": seg["angle"],
                     "matched": pick, "ok": ok,
                     "scores": {k: round(v, 3) for k, v in scores.items()}})
        print(f"  s{i:02d} t={mid:6.2f} intended {seg['angle']:>5} matched {pick:>5} "
              f"fx6 {scores['fx6']:+.3f} a7iv {scores['a7iv']:+.3f} {'OK' if ok else 'MISMATCH'}",
              flush=True)
    OUT.write_text(json.dumps({"rows": rows, "mismatches": bad}, indent=1), encoding="utf-8")
    print("mismatches:", bad, flush=True)


if __name__ == "__main__":
    main()
