"""Locate each human final cut inside the Zinc master mix by audio cross-correlation.

Nothing here touches Resolve. Both sides are decoded to 2 kHz mono with ffmpeg and matched
with a normalised FFT cross-correlation: the deliverables were rendered from this same mix,
so the match is near-exact and the answer is the mix time each cut starts at.

Two probes per cut — one near its head, one near its tail — so an internal trim inside a
deliverable shows up as a disagreement rather than passing as a clean match.

Writes gauntlet/recon/align_cuts.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
CUTS_DIR = Path(r"S:\Deliverables\Ryan Devlin\6-17-26 Zinc Bar\Full Videos")
OUT = Path(__file__).with_name("align_cuts.json")
SR = 2000
PROBE_SECONDS = 45.0
HEAD_SKIP = 5.0  # a deliverable may open on a fade; start the head probe past it
TAIL_BACK = 60.0  # how far before the end the tail probe starts

DURATIONS = {
    "6-17 - Zinc Set 2 - Hardest Part.mp4": 534.165333,
    "6-17 - Zinc Set 2 - Maitland Boulevard.mp4": 1173.184,
    "6-17 - Zinc Set 2 - Sambra.mp4": 784.298667,
    "6-17 - Zinc Set 2 - Soultrane.mp4": 761.28,
    "6-17 - Zinc Set 2 - Taurus People.mp4": 497.664,
}


def decode(path: str | Path, start: float | None = None, seconds: float | None = None):
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-vn", "-map", "a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype=np.float32).astype(np.float64)


def locate(haystack: np.ndarray, needle: np.ndarray) -> tuple[float, float]:
    """Best offset of needle in haystack (seconds) and its normalised correlation score."""
    needle = needle - needle.mean()
    n = len(haystack)
    m = len(needle)
    size = 1 << (n + m).bit_length()
    fh = np.fft.rfft(haystack, size)
    fn = np.fft.rfft(needle[::-1], size)
    corr = np.fft.irfft(fh * fn, size)[m - 1 : n]

    # Normalise by the sliding energy of the haystack window and the needle's own norm.
    cumsum = np.concatenate(([0.0], np.cumsum(haystack)))
    cumsq = np.concatenate(([0.0], np.cumsum(haystack.astype(np.float64) ** 2)))
    total = cumsq[m:] - cumsq[:-m]
    mean = (cumsum[m:] - cumsum[:-m]) / m
    window_norm = np.sqrt(np.maximum(total - m * mean**2, 1e-12))
    scores = corr / (window_norm * np.sqrt((needle**2).sum()))
    best = int(np.argmax(scores))
    return best / SR, float(scores[best])


def main() -> None:
    report: dict[str, Any] = {"mix": MIX, "sample_rate": SR, "cuts": {}, "errors": []}
    mix = decode(MIX)
    report["mix_seconds"] = round(len(mix) / SR, 3)

    for name, duration in DURATIONS.items():
        path = CUTS_DIR / name
        entry: dict[str, Any] = {"file": str(path), "duration_s": duration}
        try:
            head = decode(path, HEAD_SKIP, PROBE_SECONDS)
            head_at, head_score = locate(mix, head)
            entry["head_probe"] = {
                "cut_offset_s": HEAD_SKIP,
                "mix_t_s": round(head_at, 4),
                "score": round(head_score, 4),
                "implied_start_s": round(head_at - HEAD_SKIP, 4),
            }

            tail_offset = max(duration - TAIL_BACK, HEAD_SKIP + PROBE_SECONDS)
            tail = decode(path, tail_offset, PROBE_SECONDS)
            tail_at, tail_score = locate(mix, tail)
            entry["tail_probe"] = {
                "cut_offset_s": round(tail_offset, 3),
                "mix_t_s": round(tail_at, 4),
                "score": round(tail_score, 4),
                "implied_start_s": round(tail_at - tail_offset, 4),
            }
            entry["drift_s"] = round(
                entry["tail_probe"]["implied_start_s"] - entry["head_probe"]["implied_start_s"], 4
            )
            entry["start_s"] = entry["head_probe"]["implied_start_s"]
            entry["end_s"] = round(entry["start_s"] + duration, 4)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            report["errors"].append(entry["error"])
        report["cuts"][name] = entry
        OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("done", name, entry.get("start_s"), file=sys.stderr)

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", OUT)


if __name__ == "__main__":
    main()
