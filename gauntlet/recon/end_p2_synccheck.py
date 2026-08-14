"""Prove the ending window's per-camera sync from the cameras' own audio.

The FX6 and A7IV frame grabs at window t 83.40 disagree (kit angle shows the band
landing the final figure, wide shows them already finished), so the record->source
mapping is checked against sound rather than trusted: decode each camera's audio over
window t 70-90, take a 30 ms RMS envelope, and report the last big musical attack.
The mix puts the final note at window t 83.41; a camera whose own audio puts it
somewhere else is offset by the difference. READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "end_p2_synccheck.json"

FPS = 24000.0 / 1001.0
REC_IN = 181733
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306
SR = 48000

SOURCES = {
    "FX6": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF", FX6_ZERO),
    "A7IV": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4", A7_ZERO),
    "MIX": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav", MIX_ZERO),
}

T_START, T_END = 70.0, 92.0


def pcm(path: str, start_s: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}", "-i", path,
           "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def envelope(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hop, win = SR // 100, int(SR * 0.03)  # 10 ms hop, 30 ms window
    n = max(0, (len(x) - win) // hop)
    e = np.array([np.sqrt(np.mean(x[i * hop:i * hop + win] ** 2)) + 1e-12 for i in range(n)])
    t = np.arange(n) * hop / SR
    return t, 20 * np.log10(e)


def attacks(t: np.ndarray, db: np.ndarray) -> list[tuple[float, float, float]]:
    """(time, level_db, lift_db) for >=8 dB steps over the median of the 80-250 ms before."""
    out = []
    for i in range(25, len(db)):
        base = float(np.median(db[i - 25:i - 8]))
        if db[i] - base >= 8.0 and db[i] > np.max(db) - 26.0:
            if out and t[i] - out[-1][0] < 0.25:
                continue
            out.append((float(t[i]), float(db[i]), float(db[i] - base)))
    return out


def main() -> None:
    rep: dict = {}
    for name, (path, zero) in SOURCES.items():
        src_start = (REC_IN + round(T_START * FPS) - zero) / FPS
        x = pcm(path, src_start, T_END - T_START)
        t, db = envelope(x)
        att = attacks(t, db)
        rows = [{"window_t": round(T_START + a, 3), "db": round(b, 2), "lift": round(c, 2)}
                for a, b, c in att]
        rep[name] = {"src_start_s": round(src_start, 4), "samples": len(x), "attacks": rows}
        print(f"== {name} src_start {src_start:.3f}s  attacks:", flush=True)
        for r in rows:
            print(f"   win_t {r['window_t']:7.3f}  {r['db']:7.2f} dB  lift {r['lift']:5.2f}",
                  flush=True)
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
