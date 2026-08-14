"""Camera motion on both angles across the two UNJUDGED Taurus spans. READ-ONLY.

Same instrument as taurus_motion.py: decode small and grey, estimate the global frame-to-frame
shift by 1-D cross-correlation of row/column projections, and reduce to contiguous MOVE RUNS.
The style layer needs two things off this: where a camera move is (a no-cut zone until it
lands) and where a reframe lands (a picture change that can be ridden instead of cut).

Spans, in deliverable seconds: A 88-170, B 254-410.
Frame mapping: SYNC = 171959 + round(d * 23.976); FX6 clip = SYNC - 117576; A7IV = SYNC - 86306.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

OUT = Path(__file__).with_name("full_motion.json")
FPS = 24000.0 / 1001.0
DELIV_ZERO_SYNC = 171959

SPANS = [{"label": "A", "d0": 88.0, "d1": 170.0}, {"label": "B", "d0": 254.0, "d1": 410.0}]

FX6_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"  # noqa: E501
A7_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4"  # noqa: E501

ANGLES = {
    "FX6": {"path": FX6_PATH, "offset": 117576},
    "A7IV": {"path": A7_PATH, "offset": 86306},
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


def runs_from(t: list[float], dx: list[float], gap: float = 0.5) -> list[dict[str, Any]]:
    """Contiguous stretches where |dx| >= 1 px per 1/6 s at 192 wide, bridged over short gaps."""
    hot = [i for i, v in enumerate(dx) if abs(v) >= 1.0]
    out: list[dict[str, Any]] = []
    if not hot:
        return out
    start = prev = hot[0]
    for i in hot[1:]:
        if t[i] - t[prev] > gap:
            out.append({"d_from": t[start], "d_to": t[prev]})
            start = i
        prev = i
    out.append({"d_from": t[start], "d_to": t[prev]})
    merged = []
    for r in out:
        span = r["d_to"] - r["d_from"]
        if span < 0.4:
            continue
        idx = [i for i, tt in enumerate(t) if r["d_from"] <= tt <= r["d_to"]]
        travel = sum(dx[i] for i in idx)
        merged.append(
            {
                "d_from": round(r["d_from"], 2),
                "d_to": round(r["d_to"], 2),
                "seconds": round(span, 2),
                "net_px": round(travel, 1),
                "peak_px_per_step": round(max(abs(dx[i]) for i in idx), 1),
                "direction": "right" if travel < 0 else "left",
            }
        )
    return merged


def main() -> None:
    report: dict[str, Any] = {"grid_seconds": 1.0 / RATE, "spans": SPANS, "angles": {}}
    for span in SPANS:
        d0, d1 = float(span["d0"]), float(span["d1"])
        for name, cfg in ANGLES.items():
            src_frame = DELIV_ZERO_SYNC + round(d0 * FPS) - int(cfg["offset"])
            start_s = src_frame / FPS
            arr = decode(str(cfg["path"]), start_s, d1 - d0)
            cols = arr.mean(axis=1)
            rows = arr.mean(axis=2)
            dx, dy, dframe = [], [], []
            for i in range(1, len(arr)):
                dx.append(shift_1d(cols[i - 1], cols[i]))
                dy.append(shift_1d(rows[i - 1], rows[i], maxlag=8))
                dframe.append(float(np.abs(arr[i] - arr[i - 1]).mean()))
            t = [round(d0 + (i + 0.5) / RATE, 3) for i in range(len(dx))]
            key = f"{span['label']}:{name}"
            report["angles"][key] = {
                "clip_frame_at_d0": src_frame,
                "moving_share": round(float(np.mean([abs(v) >= 1.0 for v in dx])), 3),
                "runs": runs_from(t, dx),
            }
            print(f"== {key}  clip@d0={src_frame}  moving_share="
                  f"{report['angles'][key]['moving_share']}", flush=True)
            for r in report["angles"][key]["runs"]:
                print(f"   move d{r['d_from']:7.2f} -> {r['d_to']:7.2f} ({r['seconds']:5.2f}s) "
                      f"net {r['net_px']:+7.1f} px {r['direction']:5} peak "
                      f"{r['peak_px_per_step']}", flush=True)
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
