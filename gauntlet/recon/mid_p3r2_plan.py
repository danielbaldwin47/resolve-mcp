"""Piece-3 round-2 plan: bimodal shot spread, cuts chosen from the accent curve first.

Round 1 lost on rhythm, not on placement: 4.4 s floor, one length bin, strict alternation,
and cuts a mean 2.06 s from the nearest RMS accent. So this plan picks each moment from the
accent/structure curve (r2_accents.json + taurus_mid_events.json) and only then snaps it to
the mix's own onsets 17-41 ms early, and it reports the checks round 1 could not fail:
the length histogram against the human's own cut of this window, the one-bin share and cv
that `correlate`'s shot_rhythm reads, the A7IV veto, and the pan-safety of every FX6 arrival.

READ-ONLY (writes mid_p3r2_plan.json).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

# The bins this histogram is compared against are correlate's own, imported so that
# this plan and the shot_rhythm reading it is checked with cannot drift apart.
from resolve_mcp.analysis.correlate import RHYTHM_BINS

HERE = Path(__file__).resolve().parent
RECON = json.loads((HERE / "mid_p3_recon.json").read_text(encoding="utf-8"))
ACC = json.loads((HERE / "r2_accents.json").read_text(encoding="utf-8"))["accents"]
HUM = json.loads((HERE / "mid_human_cuts.json").read_text(encoding="utf-8"))
OUT = HERE / "mid_p3r2_plan.json"

FPS = 24000.0 / 1001.0
REC_IN, REC_OUT = 175955, 178113
SPAN = REC_OUT - REC_IN  # 2158 frames = 90.007 s
EARLY_LO, EARLY_HI = 0.017, 0.041

VETO = (62.90, 66.60)  # A7IV blocked: the sax crosses its near field (occlusion_mid.json)
# FX6 camera moves (mid_p3_recon.json). An arrival must be settled, so no cut inside a run
# and none within 0.35 s of one ending; a run inside a held shot is a ride, which is wanted.
MOVES = [(r["start"], r["end"]) for r in RECON["FX6"]["move_runs"] if r["seconds"] >= 0.5]
# One measured exemption. The 41.25-46.75 walk's fast phase is over by 45.4; r2_settle.py
# reads the arrival at 24 Hz / 384 px and finds 4 px per 0.5 s easing to 1 px by 48.8 - about
# 2% of frame width per second at the recon's own 192 px scale, under the 1 px/frame threshold
# its move-run detector uses. So this arrival is a settle, not a pan being cut into.
PAN_EXEMPT = [46.84]

FIRST_ANGLE = "a7iv"
CUTS: list[tuple[float, str, str]] = [
    (2.02, "fx6",
     "SOLO CHANGE drums->bass on a downbeat, with the 2.02 fill resolving into it. The piece "
     "opens on the outgoing front (the kit) for 2.0 s and hands over on the change itself: "
     "the band picture W1 has the bassist centre and the pianist at the keys, so the new "
     "front is already on screen when it arrives (sec 4, follow the audience's gaze)"),
    (12.86, "a7iv",
     "ENERGY PEAK 12.84 (+5.31 dB, the biggest event of the first third) carrying the "
     "window's highest-confidence fill (12.86, conf 0.628) and the second-biggest RMS accent "
     "of the first half (12.95, +9.0 dB over its floor). Arrives on the kit 1.94 s BEFORE the "
     "front changes bass->drums at 14.80 - the burst at the transition starts here"),
    (15.15, "fx6",
     "ACCENT 15.15 (+4.9 dB): the bass and piano answer the drummer's entrance. This is the "
     "trade being cut as an exchange rather than sat through - sec 4, the exchange is the "
     "content, so cut between them"),
    (18.55, "a7iv",
     "arrive 0.43 s AHEAD of fill 18.98 (31 hits) so the fill lands inside the shot, and the "
     "19.34-30.84 build leg (+2.99 dB) starts inside it too: the drummer begins to climb and "
     "the near-field kit is the picture that shows it (sec 5, arrive around the fill's start)"),
    (22.05, "fx6",
     "ACCENT 22.05: the trade resolves and the piece settles onto the picture that holds all "
     "three players - the LONG mode begins"),
    (37.62, "a7iv",
     "PHRASE BOUNDARY 37.62 (conf 0.764): the piano's phrase ends and it rests 12.352 s, the "
     "longest rest in the window. With the piano out the music is bass and drums, which is "
     "exactly what the A7IV four-shot holds (drummer near field, bassist beyond the kit)"),
    (46.87, "fx6",
     "ENERGY PEAK 46.84 (+6.18 dB, the duo's climax) - and the cut reveals a NEW picture: "
     "while we were on the kit the FX6 walked left (41.25-46.75) off the piano onto a "
     "bass-and-drums two-shot, so the reframe arrives as a picture instead of as a move the "
     "viewer sat through (sec 4, a scale change is worth more than a framing return)"),
    (57.25, "a7iv",
     "ACCENT 57.25 (+13.9 dB, the second-biggest in the window) on the phrase boundaries "
     "57.38/57.94 with fill 57.94 on them; the drum solo's last big statement, fill 59.02 "
     "(1.36 s, 62 hits), lands inside the shot. This is the acceleration INTO the section "
     "change - 10.4 s, then 3.8 s (sec 3)"),
    (61.05, "fx6",
     "ACCENT 61.05 (+9.1 dB). Leaves the A7IV 1.85 s before the sax player crosses its near "
     "field (veto 62.90-66.60) and settles on W2b 4.2 s before the camera's 65.25-67.08 pan, "
     "which it then RIDES: the camera finds the sax at his mic, and the front change "
     "drums->other (66.24), the phrase boundary 66.68 and the song's structural peak 66.84 "
     "(-6.9 LUFS, +12.91 dB) all land inside this one shot. Release into stillness (sec 3)"),
    (71.35, "a7iv",
     "ACCENT 71.35 (+13.2 dB, the biggest in the back half) with fill 70.86 resolving into "
     "it; the window's biggest fill, 73.36 (2.90 s, 109 hits), plays inside the shot on the "
     "near-field kit"),
    (74.65, "fx6",
     "ACCENT 74.65 (+9.0 dB): back to the sax at his mic, and the shot rides the 75.92-77.25 "
     "and 78.42-78.92 reframes out to W4 - the widest framing in the window and the only "
     "picture that holds all four players. The ensemble ride-out arrives as the picture "
     "opens, with no cut on it (sec 4, the moving camera is a second editor)"),
    (80.76, "a7iv",
     "PHRASE BOUNDARY 80.76 (conf 0.732) with fill 80.76 (1.68 s, 67 hits) on it, into the "
     "densest drumming anywhere in the window (onset density 9.06 -> 9.33/s); energy peak "
     "83.34 plays inside the shot"),
    (84.40, "fx6",
     "PHRASE BOUNDARY 84.40 (conf 0.663): the close returns to the four-player wide EARLY, so "
     "fill 85.26 and the accents at 85.05/85.85 land inside the shot rather than being jabbed "
     "at, and the window's last peak (89.34, +4.17 dB, 10.0 onsets/s) and closing fill 89.44 "
     "(2.76 s, 96 hits) play on the ensemble picture"),
]


def snap(target: float, onsets: list[float]) -> tuple[int, float, float]:
    """(window-relative cut frame, onset used, early offset in seconds). Same as round 1."""
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
    if best is None:
        frame = int(target * FPS)
        return frame, target, target - frame / FPS
    return best[1], best[2], best[3]


def accent_gap(t: float) -> float:
    return round(min(abs(a["t"] - t) for a in ACC), 2)


def bins(lengths: list[float]) -> dict[str, int]:
    counted = dict.fromkeys((label for label, _ in RHYTHM_BINS), 0)
    for length in lengths:
        for label, edge in RHYTHM_BINS:
            if length < edge:
                counted[label] += 1
                break
    return counted


def main() -> None:
    onsets = [o for o in RECON["onsets_rel"] if -0.5 <= o <= SPAN / FPS + 0.5]
    rows, prev, angle = [], 0, FIRST_ANGLE
    for target, nxt, why in CUTS:
        frame, onset, off = snap(target, onsets)
        rows.append({
            "target": target, "onset": round(onset, 3), "cut_frame_rel": frame,
            "cut_t": round(frame / FPS, 3), "early_ms": round(off * 1000, 1),
            "sync_frame": REC_IN + frame, "from": angle, "to": nxt,
            "shot_seconds": round((frame - prev) / FPS, 2),
            "accent_gap_s": accent_gap(frame / FPS), "why": why,
        })
        prev, angle = frame, nxt

    segs, start, ang = [], 0, FIRST_ANGLE
    for r in rows + [{"cut_frame_rel": SPAN, "to": "END"}]:
        segs.append({"angle": ang, "in_rel": start, "out_rel": r["cut_frame_rel"],
                     "in_t": round(start / FPS, 3), "out_t": round(r["cut_frame_rel"] / FPS, 3),
                     "seconds": round((r["cut_frame_rel"] - start) / FPS, 2),
                     "sync": [REC_IN + start, REC_IN + r["cut_frame_rel"]]})
        start, ang = r["cut_frame_rel"], r["to"]

    durs = [s["seconds"] for s in segs]
    hist = bins(durs)
    fullest = max(hist, key=lambda k: hist[k])
    mean = statistics.fmean(durs)
    cv = statistics.pstdev(durs) / mean
    share = {a: round(sum(s["seconds"] for s in segs if s["angle"] == a), 2)
             for a in ("fx6", "a7iv")}
    offs = [r["early_ms"] for r in rows]
    gaps = [r["accent_gap_s"] for r in rows]

    # constraint checks
    veto_hits = [s for s in segs if s["angle"] == "a7iv"
                 and s["in_t"] < VETO[1] and s["out_t"] > VETO[0]]
    ceiling = [s for s in segs if s["angle"] == "a7iv" and s["seconds"] > 21.5]
    pan_hits = [s for s in segs if s["angle"] == "fx6"
                and any(a - 0.05 <= s["in_t"] <= b + 0.35 for a, b in MOVES)
                and not any(abs(s["in_t"] - x) < 0.20 for x in PAN_EXEMPT)]
    # a shot under 3 s must be a picture the cut has not been on in the previous 8 s
    short_bad = []
    for i, s in enumerate(segs):
        if s["seconds"] >= 3.0:
            continue
        prior = [p for p in segs[:i] if p["angle"] == s["angle"]]
        if prior and s["in_t"] - prior[-1]["out_t"] < 8.0:
            short_bad.append(s)

    hum = [sh["seconds"] for sh in HUM["shots"]]
    rep = {
        "cuts": rows, "segments": segs, "durations": durs,
        "n_shots": len(segs), "n_cuts": len(rows),
        "histogram": hist, "one_bin": round(hist[fullest] / len(durs), 3), "fullest_bin": fullest,
        "cv": round(cv, 3), "median": round(statistics.median(durs), 2),
        "mean": round(mean, 2), "min": min(durs), "max": max(durs),
        "human_histogram": bins(hum), "human_cv": round(statistics.pstdev(hum)
                                                        / statistics.fmean(hum), 3),
        "human_durations": hum, "human_share_pct": {"FX6": 73, "A7IV": 27},
        "share_seconds": share,
        "share_pct": {k: round(100 * v / (SPAN / FPS), 1) for k, v in share.items()},
        "early_ms": {"min": min(offs), "max": max(offs), "median": statistics.median(offs)},
        "accent_gap_s": {"mean": round(statistics.fmean(gaps), 3),
                         "median": round(statistics.median(gaps), 3), "max": max(gaps),
                         "within_0_5": sum(1 for g in gaps if g <= 0.5)},
        "veto_violations": veto_hits, "locked_ceiling_violations": ceiling,
        "pan_arrival_violations": pan_hits, "short_return_violations": short_bad,
        "total_seconds": round(sum(durs), 3),
    }
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")

    for r in rows:
        print(f"  t={r['cut_t']:7.3f} rel={r['cut_frame_rel']:5d} sync={r['sync_frame']} "
              f"early={r['early_ms']:5.1f}ms accent{r['accent_gap_s']:5.2f}s "
              f"{r['from']:>5}->{r['to']:<5} shot={r['shot_seconds']:6.2f}s", flush=True)
    print("durations:", durs, flush=True)
    print("human    :", hum, flush=True)
    print("hist", hist, "one_bin", rep["one_bin"], "cv", rep["cv"], flush=True)
    print("human hist", rep["human_histogram"], "human cv", rep["human_cv"], flush=True)
    print("shots", rep["n_shots"], "median", rep["median"], "mean", rep["mean"],
          "min", rep["min"], "max", rep["max"], flush=True)
    print("share", rep["share_pct"], "early_ms", rep["early_ms"], flush=True)
    print("accent_gap", rep["accent_gap_s"], flush=True)
    print("VIOLATIONS veto", len(veto_hits), "ceiling", len(ceiling), "pan", len(pan_hits),
          "short-return", len(short_bad), flush=True)
    for s in pan_hits + short_bad + veto_hits + ceiling:
        print("   !", json.dumps(s), flush=True)
    print("total", rep["total_seconds"], flush=True)


if __name__ == "__main__":
    main()
