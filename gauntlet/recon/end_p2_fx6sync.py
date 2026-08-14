"""Which FX6 audio stream has the room in it, and what lag does it sit at?

The FX6 MXF carries eight mono PCM streams; a:0 came back silent, so every stream is
enveloped and cross-correlated against the master mix over window t 60-92. A camera
whose record->source mapping is right peaks at lag 0. READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "end_p2_fx6sync.json"
FPS = 24000.0 / 1001.0
REC_IN = 181733
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306
SR = 48000
HOP = SR // 100  # 10 ms
FX6 = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"
A7 = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4"
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
T_START, T_DUR, PAD = 60.0, 32.0, 3.0


def pcm(path: str, start_s: float, dur: float, stream: int = 0) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}", "-i", path,
           "-map", f"0:a:{stream}", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def env(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    e = np.array([np.sqrt(np.mean(x[i * HOP:(i + 1) * HOP] ** 2)) + 1e-12 for i in range(n)])
    e = 20 * np.log10(e)
    return e - e.mean()


def best_lag(ref: np.ndarray, test: np.ndarray, pad_steps: int) -> tuple[float, float]:
    best, bl = -9e9, 0
    for lag in range(-pad_steps, pad_steps + 1):
        seg = test[pad_steps + lag: pad_steps + lag + len(ref)]
        if len(seg) != len(ref):
            continue
        c = float(np.dot(ref, seg) / (np.linalg.norm(ref) * np.linalg.norm(seg) + 1e-9))
        if c > best:
            best, bl = c, lag
    return bl * HOP / SR, best


def main() -> None:
    mix_start = (REC_IN + round(T_START * FPS) - MIX_ZERO) / FPS
    ref = env(pcm(MIX, mix_start, T_DUR))
    pad_steps = int(PAD * SR / HOP)
    rep: dict = {"ref": "master mix", "window_t": [T_START, T_START + T_DUR]}

    for name, path, zero, streams in (("A7IV", A7, A7_ZERO, [0]), ("FX6", FX6, FX6_ZERO, list(range(8)))):
        src_start = (REC_IN + round(T_START * FPS) - zero) / FPS
        rows = []
        for s in streams:
            x = pcm(path, src_start - PAD, T_DUR + 2 * PAD, s)
            if not len(x):
                continue
            rms_db = 20 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-12)
            e = env(x)
            lag, corr = best_lag(ref, e, pad_steps)
            rows.append({"stream": s, "rms_db": round(rms_db, 1), "lag_s": round(lag, 3),
                         "corr": round(corr, 3)})
            print(f"{name} a:{s}  rms {rms_db:7.1f} dB   best lag {lag:+.3f} s  corr {corr:.3f}",
                  flush=True)
        rep[name] = rows
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
