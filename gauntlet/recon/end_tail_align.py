"""Pin the final hit to 10 ms, and check the mix->deliverable offset on the tail itself.

The survey measured the Taurus deliverable's tail RMS at 0.1 s (openings_survey.json). The
same measurement run on the master at the assumed offset (mix = deliverable + 3568.4815)
should line up; the lag that maximises the correlation is the offset the builder should use.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
SURVEY = Path(__file__).with_name("openings_survey.json")
PROBE = (4045.0, 4075.0)
ZERO = 3568.4815


def main() -> None:
    from resolve_mcp.analysis import decode

    with tempfile.TemporaryDirectory() as tmp:
        cut = Path(tmp) / "a.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{PROBE[0]}", "-t", f"{PROBE[1] - PROBE[0]}",
             "-i", str(MIX), "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", str(cut)],
            check=True, capture_output=True,
        )
        audio = decode.read(cut)
    x = np.asarray(audio.mono(), dtype=np.float64)
    rate = audio.sample_rate

    # 10 ms envelope around the final hit
    hop = int(0.005 * rate)
    length = int(0.010 * rate)
    print("--- 5 ms grid, 10 ms rms, 4058.8-4060.2")
    for start in range(0, len(x) - length, hop):
        t = PROBE[0] + start / rate
        if not (4058.8 <= t <= 4060.2):
            continue
        seg = x[start : start + length]
        rms = math.sqrt(float((seg**2).mean()) + 1e-20)
        print(f"{t:8.3f} {20 * math.log10(rms):7.2f} peak {float(np.abs(seg).max()):.4f}")

    # 0.1 s RMS on the master, quoted in deliverable time, beside the survey's own numbers
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))
    curve = survey["songs"]["taurus_people"]["tail_rms_0p1s"]
    deliv = {round(p["t"], 3): p["rms_db"] for p in curve}
    step = int(0.1 * rate)
    mine: dict[float, float] = {}
    for start in range(0, len(x) - step, step):
        t = PROBE[0] + start / rate
        seg = x[start : start + step]
        rms = math.sqrt(float((seg**2).mean()) + 1e-20)
        mine[round(t, 3)] = 20 * math.log10(rms)
    print("--- deliverable_t | survey rms | master rms at mix = d + 3568.4815")
    rows = []
    for d, value in sorted(deliv.items()):
        mix_t = d + ZERO
        key = min(mine, key=lambda one: abs(one - mix_t))
        if abs(key - mix_t) > 0.06 or not (PROBE[0] + 0.2 <= mix_t <= PROBE[1] - 0.2):
            continue
        rows.append((d, value, mine[key]))
        print(f"{d:9.3f} {value:8.2f} {mine[key]:8.2f}")
    print("--- lag search: correlation of the two 0.1 s curves, lag in 0.1 s steps")
    if rows:
        a = np.array([one[1] for one in rows])
        for lag in range(-8, 9):
            b = []
            for d, _, _ in rows:
                mix_t = d + ZERO + lag * 0.1
                key = min(mine, key=lambda one: abs(one - mix_t))
                b.append(mine[key])
            arr = np.array(b)
            corr = float(np.corrcoef(a, arr)[0, 1])
            gap = float(np.abs(a - arr).mean())
            print(f"  lag {lag * 0.1:+.1f}s  r={corr:+.3f}  mean|diff|={gap:.2f} dB")


if __name__ == "__main__":
    main()
