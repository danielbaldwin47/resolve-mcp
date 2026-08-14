"""Piece-2 (Taurus ENDING) recon: the mix onset list, camera motion, and frame grabs.

Same arithmetic as the round-3 opening scripts (mix_onsets_r3.py, taurus_motion.py),
retargeted at the ending window: Zinc SYNC 181733-183891, mix zero frame 86401, so
T0 = (181733 - 86401) * 1001 / 24000. READ-ONLY (writes recon JSON + jpgs only).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "end_p2_recon.json"
GRABDIR = HERE / "end_p2_grabs"
MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")

FPS = 24000.0 / 1001.0
REC_IN = 181733
SPAN_FRAMES = 2158
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306
T0 = (REC_IN - MIX_ZERO) * 1001.0 / 24000.0

ANGLES = {
    "FX6": {
        "path": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF",
        "zero_frame": FX6_ZERO,
    },
    "A7IV": {
        "path": r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4",
        "zero_frame": A7_ZERO,
    },
}

W, H, RATE = 192, 108, 6.0
GRAB_TIMES = [12.0, 25.0, 40.0, 50.0, 62.5, 67.0, 73.5, 76.5, 79.5, 82.0, 83.4]


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


def motion(name: str, cfg: dict) -> dict:
    pad = 2.0
    src_frame = REC_IN - cfg["zero_frame"]
    arr = decode(cfg["path"], src_frame / FPS - pad, SPAN_FRAMES / FPS + 2 * pad)
    cols, rows = arr.mean(axis=1), arr.mean(axis=2)
    dx, dy = [], []
    for i in range(1, len(arr)):
        dx.append(shift_1d(cols[i - 1], cols[i]))
        dy.append(shift_1d(rows[i - 1], rows[i], maxlag=8))
    t = [round(-pad + (i + 0.5) / RATE, 3) for i in range(len(dx))]
    runs, i = [], 0
    while i < len(dx):
        if abs(dx[i]) >= 1.0 or abs(dy[i]) >= 1.0:
            j = i
            while j < len(dx) and (abs(dx[j]) >= 1.0 or abs(dy[j]) >= 1.0):
                j += 1
            if (t[j - 1] - t[i]) >= 0.30:
                runs.append({
                    "start": t[i], "end": t[j - 1], "seconds": round(t[j - 1] - t[i], 2),
                    "net_dx": round(float(sum(dx[i:j])), 1),
                    "net_dy": round(float(sum(dy[i:j])), 1),
                })
            i = j
        else:
            i += 1
    return {
        "source_frame_at_rel0": src_frame,
        "moving_share": round(float(np.mean([abs(v) >= 1.0 for v in dx])), 3),
        "move_runs": runs,
    }


def grabs(name: str, cfg: dict) -> list[dict]:
    GRABDIR.mkdir(exist_ok=True)
    out = []
    for t in GRAB_TIMES:
        src_frame = REC_IN + round(t * FPS) - cfg["zero_frame"]
        path = GRABDIR / f"{name}_{t:06.2f}.jpg"
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-ss", f"{src_frame / FPS:.4f}", "-i", cfg["path"],
            "-frames:v", "1", "-vf", "scale=960:-1", "-q:v", "3", str(path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        out.append({"t": t, "src_frame": src_frame, "sync_frame": REC_IN + round(t * FPS),
                    "path": str(path)})
    return out


def main() -> None:
    from resolve_mcp.analysis import decode as adecode
    from resolve_mcp.analysis import energy

    rep: dict = {"t0": T0, "rec_in": REC_IN, "span_frames": SPAN_FRAMES, "fps": FPS}

    onsets = [float(s) for s in energy.onsets(adecode.read(MIX))]
    rel = sorted(o - T0 for o in onsets if -1.0 <= o - T0 <= SPAN_FRAMES / FPS + 1.0)
    rep["onsets_rel"] = rel
    print("onsets in window:", len(rel), flush=True)

    for name, cfg in ANGLES.items():
        m = motion(name, cfg)
        rep[name] = m
        print(name, "moving_share", m["moving_share"], "runs", len(m["move_runs"]), flush=True)
        for r in m["move_runs"]:
            print(f"   {r['start']:7.2f} -> {r['end']:7.2f} ({r['seconds']:5.2f}s) "
                  f"dx {r['net_dx']:+6.1f} dy {r['net_dy']:+6.1f}", flush=True)
        rep[name]["grabs"] = grabs(name, cfg)

    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
