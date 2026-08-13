"""Measure the first 90 s of Taurus People in the master mix.

Reads the cached beats/energy JSON the analysis suite wrote, and measures the
window's onsets, band energies and phrase gaps straight off the mix with ffmpeg
+ numpy. Writes taurus_window.json next to this file. Read-only; no Resolve.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
CACHE = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
BEATS = CACHE / "Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"
ENERGY = CACHE / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"
OUT = Path(__file__).with_name("taurus_window.json")

T0 = 3568.4815  # deliverable span start (master-mix seconds)
T1 = 3658.4815
PAD = 6.0
SR = 22050
HOP = 256
FPS = 24000.0 / 1001.0


def load_curve(path: Path, key_hint: str) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    report: dict = {"window": {"start_s": T0, "end_s": T1, "fps": FPS}}

    # --- beats -------------------------------------------------------------
    bj = load_curve(BEATS, "beats")
    times = None
    for k in ("beats", "times", "seconds"):
        if k in bj:
            raw = bj[k]
            if raw and isinstance(raw[0], dict):
                times = [float(b.get("t", b.get("seconds", 0.0))) for b in raw]
            else:
                times = [float(x) for x in raw]
            break
    if times is None:
        report["beats_keys"] = list(bj)[:20]
        times = []
    bt = np.array(times)
    sel = bt[(bt >= T0 - PAD) & (bt <= T1 + PAD)]
    gaps = np.diff(sel)
    med = float(np.median(gaps)) if len(gaps) else 0.0
    steady = [
        float(g) for g in gaps if med and abs(g - med) <= 0.10 * med
    ]
    report["beats"] = {
        "count_in_window": int(len(sel)),
        "first": float(sel[0]) if len(sel) else None,
        "last": float(sel[-1]) if len(sel) else None,
        "median_gap_s": med,
        "implied_bpm": (60.0 / med) if med else None,
        "steady_share": (len(steady) / len(gaps)) if len(gaps) else 0.0,
        "times": [round(float(x), 4) for x in sel],
        "long_gaps": [
            [round(float(sel[i]), 3), round(float(g), 3)]
            for i, g in enumerate(gaps)
            if g > 1.0
        ],
    }

    # --- energy ------------------------------------------------------------
    ej = load_curve(ENERGY, "energy")
    pts = ej.get("energy") or ej.get("points") or ej.get("windows") or []
    ecurve = []
    for p in pts:
        if isinstance(p, dict):
            t = float(p.get("t", p.get("seconds", 0.0)))
            lufs = p.get("lufs", p.get("loudness"))
            ons = p.get("onsets_per_second", p.get("onsets"))
            if T0 - PAD <= t <= T1 + PAD:
                ecurve.append(
                    {
                        "t": round(t, 2),
                        "lufs": None if lufs is None else round(float(lufs), 2),
                        "ops": None if ons is None else round(float(ons), 2),
                    }
                )
    report["energy_keys"] = list(ej)[:12]
    report["energy"] = ecurve

    # --- decode window -----------------------------------------------------
    start = T0 - PAD
    dur = (T1 - T0) + 2 * PAD
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}",
        "-i", MIX, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    n_fft = 1024
    win = np.hanning(n_fft)
    frames = 1 + (len(x) - n_fft) // HOP
    idx = np.arange(n_fft)[None, :] + HOP * np.arange(frames)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    tt = start + (np.arange(frames) * HOP + n_fft / 2) / SR

    def band(lo: float, hi: float) -> np.ndarray:
        m = (freqs >= lo) & (freqs < hi)
        return np.sqrt((S[:, m] ** 2).mean(axis=1))

    low = band(30, 180)      # kick + bass
    mid = band(180, 1600)    # piano / sax body
    hi = band(4000, 10000)   # cymbals / snare wires / sax air
    full = np.sqrt((S**2).mean(axis=1))

    # spectral flux onset envelope
    logS = np.log1p(S * 100.0)
    flux = np.maximum(0.0, np.diff(logS, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # local-median normalisation
    k = 43  # ~0.5 s
    pad = np.pad(flux, (k // 2, k // 2), mode="edge")
    localmed = np.array([np.median(pad[i : i + k]) for i in range(len(flux))])
    nf = flux - localmed

    # peak picking
    thr = np.percentile(nf, 90)
    peaks = []
    minsep = int(0.09 * SR / HOP)
    last = -10**9
    for i in range(1, len(nf) - 1):
        if nf[i] > thr and nf[i] >= nf[i - 1] and nf[i] >= nf[i + 1] and i - last >= minsep:
            peaks.append(i)
            last = i
    onsets = [
        {"t": round(float(tt[i]), 4), "strength": round(float(nf[i] / (thr + 1e-9)), 2)}
        for i in peaks
        if T0 - 2 <= tt[i] <= T1 + 2
    ]
    report["onsets"] = {
        "count": len(onsets),
        "per_second": round(len(onsets) / (T1 - T0), 2),
        "list": onsets,
    }
    # strongest transients (the fourth-wall risk points)
    strong = sorted(onsets, key=lambda o: -o["strength"])[:40]
    report["strong_transients"] = sorted(strong, key=lambda o: o["t"])

    # --- band curves at 100 ms ---------------------------------------------
    def resample(v: np.ndarray, step: float = 0.1) -> list:
        grid = np.arange(T0 - PAD, T1 + PAD, step)
        return [round(float(np.interp(g, tt, v)), 6) for g in grid]

    def db(v: np.ndarray) -> np.ndarray:
        return 20 * np.log10(v + 1e-9)

    grid = [round(float(g), 2) for g in np.arange(T0 - PAD, T1 + PAD, 0.1)]
    report["bands"] = {
        "t": grid,
        "low_db": resample(db(low)),
        "mid_db": resample(db(mid)),
        "high_db": resample(db(hi)),
        "full_db": resample(db(full)),
    }

    # --- phrase gaps: dips in mid band -------------------------------------
    middb = np.array(report["bands"]["mid_db"])
    gt = np.array(grid)
    ref = np.percentile(middb[(gt >= T0) & (gt <= T1)], 75)
    dips = []
    i = 0
    while i < len(gt):
        if middb[i] < ref - 8:
            j = i
            while j < len(gt) and middb[j] < ref - 8:
                j += 1
            if (gt[j - 1] - gt[i]) >= 0.18 and T0 <= gt[i] <= T1:
                dips.append(
                    {
                        "start": round(float(gt[i]), 2),
                        "end": round(float(gt[j - 1]), 2),
                        "seconds": round(float(gt[j - 1] - gt[i]), 2),
                        "depth_db": round(float(ref - middb[i:j].min()), 1),
                    }
                )
            i = j
        else:
            i += 1
    report["mid_dips_ref_db"] = round(float(ref), 1)
    report["mid_dips"] = dips

    # --- 2 s energy summary for arc reading --------------------------------
    arc = []
    fulldb = np.array(report["bands"]["full_db"])
    hidb = np.array(report["bands"]["high_db"])
    lowdb = np.array(report["bands"]["low_db"])
    for s in np.arange(T0, T1, 2.0):
        m = (gt >= s) & (gt < s + 2.0)
        if not m.any():
            continue
        ons = [o for o in onsets if s <= o["t"] < s + 2.0]
        arc.append(
            {
                "t": round(float(s), 1),
                "full_db": round(float(fulldb[m].mean()), 1),
                "low_db": round(float(lowdb[m].mean()), 1),
                "mid_db": round(float(middb[m].mean()), 1),
                "high_db": round(float(hidb[m].mean()), 1),
                "onsets": len(ons),
            }
        )
    report["arc_2s"] = arc

    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", OUT)
    print("beats in window:", report["beats"]["count_in_window"],
          "median gap", round(report["beats"]["median_gap_s"], 4),
          "steady", round(report["beats"]["steady_share"], 3))
    print("onsets:", report["onsets"]["count"], "per_s", report["onsets"]["per_second"])
    print("mid dips:", len(dips))


if __name__ == "__main__":
    main()
