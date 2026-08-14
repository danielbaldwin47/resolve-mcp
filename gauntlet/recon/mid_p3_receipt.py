"""Receipts for the piece-3 mid cut: audio sync to the master mix, and the constraint checks.

1. Cross-correlates three 4 s slices of the RENDER's audio against the master mix at the
   window's own offset (mix_t = render_t + 3735.16) - a sample-level proof that the master
   mix is under the picture and in sync.
2. Re-checks the hard constraints against the built plan: the A7IV occlusion veto, the
   3 s floor on a framing return, the span, and the picture sequence. READ-ONLY.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_mid_p3r1.mp4"
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
PLAN = json.loads((HERE / "mid_p3_plan.json").read_text(encoding="utf-8"))
OUT = HERE / "mid_p3_receipt.json"
SR = 48000
MIX_T0 = 3735.16
VETO = (62.90, 66.60)
FPS = 24000.0 / 1001.0

# window-second -> which FX6 framing is on screen (from mid_p3_recon.json move runs + grabs)
FRAMINGS = [
    (0.0, 41.25, "W1 piano+bass, kit edge"),
    (41.25, 46.75, "W1->W2 reframe (on screen)"),
    (46.75, 53.92, "W2 bass+drummer, piano at edge"),
    (53.92, 55.92, "W2->W2b reframe (on screen)"),
    (55.92, 65.25, "W2b piano+bass+kit edge"),
    (65.25, 67.08, "W2b->W3 reframe (on screen, the sax revealed)"),
    (67.08, 75.92, "W3 piano+bass+sax"),
    (75.92, 78.92, "W3->W4 reframe (OFF screen)"),
    (78.92, 90.1, "W4 full quartet"),
]


def pcm(path: str, start_s: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur:.4f}", "-i", path,
           "-map", "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout,
                         dtype=np.float32).astype(np.float64)


def lag_ms(render_t: float, dur: float = 4.0, maxlag_ms: float = 120.0) -> dict:
    a = pcm(str(RENDER), render_t, dur)
    b = pcm(MIX, MIX_T0 + render_t - maxlag_ms / 1000.0, dur + 2 * maxlag_ms / 1000.0)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    corr = np.correlate(b, a, mode="full")
    lag = int(np.argmax(corr)) - (len(a) - 1)
    peak = float(corr.max() / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
    return {"render_t": render_t, "lag_ms": round((lag / SR - maxlag_ms / 1000.0) * 1000.0, 2),
            "normalised_peak": round(peak, 3)}


def framing_at(t: float) -> str:
    for lo, hi, name in FRAMINGS:
        if lo <= t < hi:
            return name
    return "?"


def main() -> None:
    segs = PLAN["segments"]
    rep: dict = {"sync": [lag_ms(t) for t in (8.0, 44.0, 82.0)]}

    veto_hits = [s for s in segs if s["angle"] == "a7iv"
                 and s["in_rel"] / FPS < VETO[1] and s["out_rel"] / FPS > VETO[0]]
    pictures = []
    for s in segs:
        t_in, t_out = s["in_rel"] / FPS, s["out_rel"] / FPS
        pic = "A7IV four-shot" if s["angle"] == "a7iv" else framing_at(t_in)
        pictures.append({"t": [round(t_in, 2), round(t_out, 2)], "seconds": s["seconds"],
                         "angle": s["angle"], "picture_at_cut_in": pic})

    returns = []
    for i, s in enumerate(segs):
        prev = [p for p in segs[:i] if p["angle"] == s["angle"]]
        if prev:
            returns.append({"i": i, "angle": s["angle"], "seconds": s["seconds"],
                            "under_3s": s["seconds"] < 3.0})

    durs = PLAN["durations"]
    monotone = all(durs[i] >= durs[i + 1] for i in range(len(durs) - 1)) or \
        all(durs[i] <= durs[i + 1] for i in range(len(durs) - 1))
    alternating_lengths = all((durs[i] > durs[i + 1]) != (durs[i + 1] > durs[i + 2])
                              for i in range(len(durs) - 2))

    rep.update({
        "span_sync": [175955, 178113],
        "span_frames": segs[-1]["sync"][1] - segs[0]["sync"][0],
        "span_seconds": round((segs[-1]["sync"][1] - segs[0]["sync"][0]) / FPS, 3),
        "span_deliverable_seconds": [166.68, 256.68],
        "veto_violations": veto_hits,
        "pictures": pictures,
        "distinct_pictures": len({p["picture_at_cut_in"] for p in pictures}),
        "framing_returns_under_3s": [r for r in returns if r["under_3s"]],
        "durations": durs,
        "monotonic_duration_run": monotone,
        "strict_long_short_alternation": alternating_lengths,
        "shot_stats": {k: PLAN[k] for k in ("median", "mean", "min", "max", "n_shots", "n_cuts")},
        "share_pct": PLAN["share_pct"],
        "early_ms": PLAN["early_ms"],
    })
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in
                      ("sync", "span_frames", "span_seconds", "veto_violations",
                       "distinct_pictures", "framing_returns_under_3s",
                       "monotonic_duration_run", "strict_long_short_alternation",
                       "shot_stats", "share_pct", "early_ms")}, indent=1), flush=True)
    for p in pictures:
        print(f"  {p['t'][0]:6.2f}-{p['t'][1]:6.2f} ({p['seconds']:5.2f}s) "
              f"{p['angle']:5} {p['picture_at_cut_in']}", flush=True)


if __name__ == "__main__":
    main()
