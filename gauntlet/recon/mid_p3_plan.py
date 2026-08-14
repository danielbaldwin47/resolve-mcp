"""Piece-3 (Taurus MID) plan: snap each motivated cut to the mix's own onsets, 17-41 ms early.

Reads mid_p3_recon.json (onsets, camera move runs) and taurus_mid_events.json (events),
picks a cut frame per intended motivation, and prints the ladder with its durations,
angle shares and constraint checks. Writes mid_p3_plan.json. READ-ONLY.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECON = json.loads((HERE / "mid_p3_recon.json").read_text(encoding="utf-8"))
OUT = HERE / "mid_p3_plan.json"

FPS = 24000.0 / 1001.0
REC_IN, REC_OUT = 175955, 178113
SPAN = REC_OUT - REC_IN  # 2158 frames
EARLY_LO, EARLY_HI = 0.017, 0.041  # the corpus's near-not-on band, in seconds

# The A7IV is blocked while the sax player crosses its near field (occlusion_mid.json).
VETO = (62.90, 66.60)

# (target window-second, angle after the cut, motivation)
CUTS = [
    (19.34, "fx6", "leave the kit as the 19.34 build leg starts"),
    (24.80, "a7iv", "fill 24.80 (1.72 s, 71 hits) - arrive at its start"),
    (37.62, "fx6", "the piano's phrase ENDS here and rests 12.35 s - arrive to catch it"),
    (46.84, "a7iv", "energy peak +6.18 dB, top of the 42.34-47.84 build"),
    (51.88, "fx6", "the decay ends and the sparse plateau 51.84-60.34 opens"),
    (57.94, "a7iv", "phrase boundary 57.94 with a fill on it - arrive EARLY so the "
                    "59.02 fill (1.36 s, 62 hits) lands inside the shot, no jab"),
    (62.45, "fx6", "leave the A7IV before the sax crosses its near field (veto 62.90)"),
    (73.36, "a7iv", "fill 73.36 (2.90 s, 109 hits) - the biggest fill in the window"),
    (79.08, "fx6", "phrase boundary 79.08; the 75.92-78.92 reframe landed off screen"),
    (85.26, "a7iv", "fill 85.26 into the ride-out's densest drumming (10.0 onsets/s)"),
]
FIRST_ANGLE = "fx6"
# 12.84 is handled as the first cut below; kept separate so the list above stays motivations.
CUTS.insert(0, (12.84, "a7iv", "energy peak +5.31 dB with fill 12.86 (conf 0.628) on it"))


def snap(target: float, onsets: list[float]) -> tuple[int, float, float]:
    """Return (window-relative cut frame, onset used, early offset in seconds)."""
    best = None
    for o in onsets:
        if abs(o - target) > 0.60:
            continue
        for frame in (int(o * FPS), int(o * FPS) - 1):
            off = o - frame / FPS
            if not (EARLY_LO <= off <= EARLY_HI):
                continue
            score = (abs(o - target), abs(off - 0.029))
            if best is None or score < best[0]:
                best = (score, frame, o, off)
    if best is None:  # no onset in band: fall back to the frame under the target
        frame = int(target * FPS)
        return frame, target, target - frame / FPS
    return best[1], best[2], best[3]


def main() -> None:
    onsets = [o for o in RECON["onsets_rel"] if -0.5 <= o <= SPAN / FPS + 0.5]
    rows, prev, angle = [], 0, FIRST_ANGLE
    for target, nxt, why in CUTS:
        frame, onset, off = snap(target, onsets)
        rows.append({
            "target": target, "onset": round(onset, 3), "cut_frame_rel": frame,
            "cut_t": round(frame / FPS, 3), "early_ms": round(off * 1000, 1),
            "sync_frame": REC_IN + frame, "from": angle, "to": nxt,
            "shot_seconds": round(frame / FPS - prev / FPS, 2), "why": why,
        })
        prev, angle = frame, nxt
    rows.append({"target": 90.0, "cut_frame_rel": SPAN, "cut_t": round(SPAN / FPS, 3),
                 "sync_frame": REC_OUT, "from": angle, "to": "END",
                 "shot_seconds": round(SPAN / FPS - prev / FPS, 2), "why": "window close"})

    segs, start, ang = [], 0, FIRST_ANGLE
    for r in rows:
        segs.append({"angle": ang, "in_rel": start, "out_rel": r["cut_frame_rel"],
                     "seconds": round((r["cut_frame_rel"] - start) / FPS, 2),
                     "sync": [REC_IN + start, REC_IN + r["cut_frame_rel"]]})
        start, ang = r["cut_frame_rel"], r["to"]

    durs = [s["seconds"] for s in segs]
    share = {a: round(sum(s["seconds"] for s in segs if s["angle"] == a), 2)
             for a in ("fx6", "a7iv")}
    offs = [r["early_ms"] for r in rows if "early_ms" in r]
    bad_veto = [s for s in segs if s["angle"] == "a7iv"
                and s["in_rel"] / FPS < VETO[1] and s["out_rel"] / FPS > VETO[0]]
    rep = {
        "cuts": rows, "segments": segs, "durations": durs,
        "n_shots": len(segs), "n_cuts": len(segs) - 1,
        "median": round(sorted(durs)[len(durs) // 2], 2),
        "mean": round(sum(durs) / len(durs), 2), "min": min(durs), "max": max(durs),
        "share_seconds": share,
        "share_pct": {k: round(100 * v / (SPAN / FPS), 1) for k, v in share.items()},
        "early_ms": {"min": min(offs), "max": max(offs),
                     "median": sorted(offs)[len(offs) // 2]},
        "veto_violations": bad_veto,
        "total_seconds": round(sum(durs), 3),
    }
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for r in rows:
        print(f"  t={r['cut_t']:7.3f} rel={r['cut_frame_rel']:5d} sync={r['sync_frame']} "
              f"early={r.get('early_ms', 0):5.1f}ms {r['from']:>5}->{r['to']:<5} "
              f"shot={r['shot_seconds']:6.2f}s  {r['why']}", flush=True)
    print("durations:", durs, flush=True)
    print("shots", rep["n_shots"], "cuts", rep["n_cuts"], "median", rep["median"],
          "mean", rep["mean"], "min", rep["min"], "max", rep["max"], flush=True)
    print("share", rep["share_pct"], "early_ms", rep["early_ms"], flush=True)
    print("veto violations:", bad_veto, "total", rep["total_seconds"], flush=True)


if __name__ == "__main__":
    main()
