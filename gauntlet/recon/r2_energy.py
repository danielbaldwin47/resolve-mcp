"""Round-2 recon: the whole 90 s window's loudness + onset-density shape, 0.5 s columns.

The events file only carries the last 40 s of the energy curve; the body plan needs the
first 50 s too (where it builds, where it thins). READ-ONLY.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
FPS = 24000.0 / 1001.0
REC_IN, MIX_ZERO, SPAN = 181733, 86401, 2158
T0 = (REC_IN - MIX_ZERO) * 1001.0 / 24000.0


def main() -> None:
    from resolve_mcp.analysis import decode as adecode

    sig = adecode.read(MIX)
    y, sr = sig.mono(), sig.sample_rate
    print("decoded", sig.channels, "ch", sig.frames, "frames", sr, "Hz",
          round(sig.duration_seconds, 2), "s")
    onsets = json.loads((HERE / "end_p2_recon.json").read_text(encoding="utf-8"))["onsets_rel"]
    onsets = np.array(onsets)

    rows = []
    step = 0.5
    span_s = SPAN / FPS
    t = 0.0
    while t < span_s:
        a, b = int((T0 + t) * sr), int((T0 + t + step) * sr)
        seg = y[a:b]
        rms = 20 * np.log10(float(np.sqrt((seg**2).mean())) + 1e-12)
        pk = 20 * np.log10(float(np.abs(seg).max()) + 1e-12)
        n = int(((onsets >= t) & (onsets < t + step)).sum())
        rows.append((round(t, 2), round(rms, 2), round(pk, 2), n))
        t += step

    print(f"{'win_t':>6} {'frame':>7} {'rms':>7} {'peak':>7} {'onsets/s':>8}  bar")
    for t, rms, pk, n in rows:
        f = REC_IN + round(t * FPS)
        bar = "#" * max(0, int((rms + 45) / 1.2))
        print(f"{t:6.1f} {f:7d} {rms:7.2f} {pk:7.2f} {n * 2:8d}  {bar}")

    print()
    print("onsets in window:", len(onsets))
    # 3 s smoothed rms, for the build/decay reading
    print()
    print("3 s smoothed rms, 1 s steps")
    arr = np.array([r[1] for r in rows])
    for i in range(0, len(rows) - 5, 2):
        print(f"  win_t {rows[i][0]:6.1f}  rms3 {arr[i : i + 6].mean():7.2f}")


if __name__ == "__main__":
    main()
