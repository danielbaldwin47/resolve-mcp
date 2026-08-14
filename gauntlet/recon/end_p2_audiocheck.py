"""Does the render carry the master mix, in sync, to the end of the black tail?

Runs the same 30 ms attack detector the tail measurement used over the render's own audio
and compares against the mix's attacks in the same window: the final note at 83.43 and the
crowd attacks at 85.31 / 86.71 should all be present, at the same times, under black.
READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_ending_p2r1.mp4"
OUT = HERE / "end_p2_audiocheck.json"
SR = 48000


def pcm(path: str, start_s: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}", "-i", path,
           "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout,
                         dtype=np.float32).astype(np.float64)


def main() -> None:
    x = pcm(str(RENDER), 78.0, 12.0)
    hop, win = SR // 100, int(SR * 0.03)
    n = (len(x) - win) // hop
    db = np.array([20 * np.log10(np.sqrt(np.mean(x[i * hop:i * hop + win] ** 2)) + 1e-12)
                   for i in range(n)])
    t = np.arange(n) * hop / SR + 78.0
    rows = []
    for i in range(25, n):
        base = float(np.median(db[i - 25:i - 8]))
        if db[i] - base >= 8.0 and db[i] > db.max() - 26.0:
            if rows and t[i] - rows[-1]["t"] < 0.25:
                continue
            rows.append({"t": round(float(t[i]), 3), "db": round(float(db[i]), 2),
                         "lift": round(float(db[i] - base), 2)})
    print("render attacks 78-90 s:", flush=True)
    for r in rows:
        print(f"   t {r['t']:7.3f}  {r['db']:7.2f} dB  lift {r['lift']:5.2f}", flush=True)
    tail = pcm(str(RENDER), 84.0, 6.0)
    rms = 20 * np.log10(float(np.sqrt(np.mean(tail ** 2))) + 1e-12)
    print(f"audio under the black tail (84-90 s): {rms:.1f} dB RMS  "
          f"peak {20 * np.log10(float(np.abs(tail).max()) + 1e-12):.1f} dB", flush=True)
    OUT.write_text(json.dumps({"attacks": rows, "tail_rms_db": round(rms, 2)}, indent=1),
                   encoding="utf-8")


if __name__ == "__main__":
    main()
