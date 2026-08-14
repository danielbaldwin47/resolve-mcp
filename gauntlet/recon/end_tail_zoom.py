"""Scratch zoom on the Taurus ending: 0.2 s rms / flatness / centroid, 4040-4090 s.

Spectral flatness is the discriminator that matters here — applause is broadband noise
(flatness high, no pitch), a held chord or a decaying cymbal is tonal (flatness low).
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
PROBE = (4030.0, 4100.0)


def main() -> None:
    from resolve_mcp.analysis import decode

    with tempfile.TemporaryDirectory() as tmp:
        cut = Path(tmp) / "zoom.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{PROBE[0]}", "-t", f"{PROBE[1] - PROBE[0]}",
             "-i", str(MIX), "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le", str(cut)],
            check=True, capture_output=True,
        )
        audio = decode.read(cut)
    x = np.asarray(audio.mono(), dtype=np.float64)
    rate = audio.sample_rate

    step = int(0.1 * rate)
    length = int(0.2 * rate)
    win = np.hanning(length)
    freqs = np.fft.rfftfreq(length, 1.0 / rate)
    print("t      rms_db  peak_db  flat   centroid  hf   lo(<250)")
    for start in range(0, len(x) - length, step):
        seg = x[start : start + length]
        t = PROBE[0] + start / rate
        if not (4045.0 <= t <= 4090.0):
            continue
        rms = math.sqrt(float((seg**2).mean()) + 1e-20)
        peak = float(np.abs(seg).max())
        spec = np.abs(np.fft.rfft(seg * win)) + 1e-12
        power = spec**2
        flat = float(np.exp(np.log(power).mean()) / power.mean())
        centroid = float((freqs * power).sum() / power.sum())
        hf = float(power[freqs >= 4000].sum() / power.sum())
        lo = float(power[freqs < 250].sum() / power.sum())
        print(
            f"{t:7.2f} {20 * math.log10(rms):7.2f} {20 * math.log10(peak + 1e-12):7.2f} "
            f"{flat:.4f} {centroid:8.0f} {hf:.3f} {lo:.3f}"
        )


if __name__ == "__main__":
    main()
