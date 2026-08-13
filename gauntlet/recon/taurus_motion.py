"""Measure camera motion on both angles across the Taurus opening window.

Decodes each source clip's window small and grey, then estimates the global
frame-to-frame shift by 1-D cross-correlation of row/column projections. Output
is a pan/tilt velocity curve per angle: the base style layer's "a camera move is
a no-cut zone until it lands" needs a number, and this is it. READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

OUT = Path(__file__).with_name("taurus_motion.json")
FPS = 24000.0 / 1001.0
REC_IN = 171959
SPAN_S = 90.0
PAD = 3.0

ANGLES = {
    "FX6": {
        "path": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF",
        "zero_frame": 117576,
    },
    "A7IV": {
        "path": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4",
        "zero_frame": 86306,
    },
}

W, H, RATE = 192, 108, 6.0


def decode(path: str, start_s: float, dur: float) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}",
        "-i", path, "-vf", f"fps={RATE},scale={W}:{H}", "-pix_fmt", "gray",
        "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float64)


def shift_1d(a: np.ndarray, b: np.ndarray, maxlag: int = 12) -> float:
    a = a - a.mean()
    b = b - b.mean()
    best, bestlag = -1e18, 0
    for lag in range(-maxlag, maxlag + 1):
        if lag < 0:
            x, y = a[-lag:], b[: len(b) + lag]
        elif lag > 0:
            x, y = a[: len(a) - lag], b[lag:]
        else:
            x, y = a, b
        d = float((x * y).sum() / (np.sqrt((x * x).sum() * (y * y).sum()) + 1e-9))
        if d > best:
            best, bestlag = d, lag
    return float(bestlag)


def main() -> None:
    report: dict = {"grid_seconds": 1.0 / RATE, "span_s": SPAN_S}
    for name, cfg in ANGLES.items():
        src_frame = REC_IN - cfg["zero_frame"]
        start_s = src_frame * 1001.0 / 24000.0 - PAD
        arr = decode(cfg["path"], start_s, SPAN_S + 2 * PAD)
        cols = arr.mean(axis=1)  # (n, W) horizontal profile
        rows = arr.mean(axis=2)  # (n, H) vertical profile
        dx, dy, dframe = [], [], []
        for i in range(1, len(arr)):
            dx.append(shift_1d(cols[i - 1], cols[i]))
            dy.append(shift_1d(rows[i - 1], rows[i], maxlag=8))
            dframe.append(float(np.abs(arr[i] - arr[i - 1]).mean()))
        t = [round(-PAD + (i + 0.5) / RATE, 3) for i in range(len(dx))]
        report[name] = {
            "source_frame_at_rel0": src_frame,
            "t_rel": t,
            "dx_px": dx,
            "dy_px": dy,
            "absdiff": [round(v, 2) for v in dframe],
            "moving_share": round(
                float(np.mean([abs(v) >= 1.0 for v in dx])), 3
            ),
        }
        # contiguous move runs: |dx| >= 1 px per 1/6 s at 192 wide
        runs = []
        i = 0
        while i < len(dx):
            if abs(dx[i]) >= 1.0 or abs(dy[i]) >= 1.0:
                j = i
                while j < len(dx) and (abs(dx[j]) >= 1.0 or abs(dy[j]) >= 1.0):
                    j += 1
                # tolerate 1-sample gaps
                if (t[j - 1] - t[i]) >= 0.30:
                    runs.append(
                        {
                            "start": t[i],
                            "end": t[j - 1],
                            "seconds": round(t[j - 1] - t[i], 2),
                            "net_dx": round(float(sum(dx[i:j])), 1),
                            "net_dy": round(float(sum(dy[i:j])), 1),
                        }
                    )
                i = j
            else:
                i += 1
        report[name]["move_runs"] = runs
        print(name, "moving_share", report[name]["moving_share"], "runs", len(runs))
        for r in runs:
            print(f"   {r['start']:7.2f} -> {r['end']:7.2f} ({r['seconds']:5.2f}s) dx {r['net_dx']:6.1f} dy {r['net_dy']:5.1f}")

    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
