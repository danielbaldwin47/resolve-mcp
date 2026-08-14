"""Is the FX6 settled at the 46.84 arrival? Fine-grained motion around the reframe's end.

The recon's move runs are sampled at 6 Hz, so "the pan ends at 46.75" carries +-0.17 s and
the round-2 plan's own guard flags an arrival 0.09 s after it. This measures the same span at
24 Hz and prints per-frame horizontal shift, so the arrival is settled by measurement rather
than by rounding. READ-ONLY.
"""

from __future__ import annotations

import subprocess

import numpy as np

FX6 = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"  # noqa: E501
FPS = 24000.0 / 1001.0
REC_IN, FX6_ZERO = 175955, 117576
W, H = 384, 216
SPANS = [("46.84 arrival", 45.8, 3.2), ("74.58 arrival", 73.8, 2.4)]


def decode(start_s: float, dur: float) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}", "-i", FX6,
        "-vf", f"fps={FPS},scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[: n * W * H], dtype=np.uint8).reshape(n, H, W).astype(np.float64)


def shift(a: np.ndarray, b: np.ndarray, maxlag: int = 16) -> int:
    a, b = a - a.mean(), b - b.mean()
    best, lag_at = -1e18, 0
    for lag in range(-maxlag, maxlag + 1):
        x, y = (a[-lag:], b[:len(b) + lag]) if lag < 0 else (
            (a[:len(a) - lag], b[lag:]) if lag > 0 else (a, b))
        d = float((x * y).sum() / (np.sqrt((x * x).sum() * (y * y).sum()) + 1e-9))
        if d > best:
            best, lag_at = d, lag
    return lag_at


def main() -> None:
    for label, t0, dur in SPANS:
        src = (REC_IN + round(t0 * FPS) - FX6_ZERO) / FPS
        arr = decode(src, dur)
        cols = arr.mean(axis=1)
        step = max(1, round(0.5 * FPS))  # per-frame motion is sub-pixel here, so read 0.5 s apart
        out = []
        for i in range(step, len(arr), step):
            out.append((round(t0 + i / FPS, 2), shift(cols[i - step], cols[i])))
        print(label, "0.5s-step dx:", " ".join(f"{t}:{d:+d}" for t, d in out), flush=True)


if __name__ == "__main__":
    main()
