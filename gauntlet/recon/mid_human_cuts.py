"""The human's own cutting rhythm across the PIECE 3 window. READ-ONLY.

ffmpeg scene detect at ab_pack's calibrated threshold 0.10 over the deliverable, then the cuts
that land inside deliverable 166.68-256.68 s (mix 3735.16-3825.16, Zinc SYNC 175955-178113).

Two things make this more than a cut list. First, the detect runs over a padded span and the
window is cut out afterwards, so the shot that STRADDLES the window open is measured at its real
length instead of being clipped to the window edge — a shot that started 8 s before the window
is an 8 s shot, not a 0 s one. Second, every cut is carried in all three time bases, so a cut of
the human's can be compared against an event in taurus_mid_events.json without arithmetic.

Threshold note: human_cuts_taurus.json ran the same file at 0.27 and found 72 cuts across the
song. 0.10 is the calibrated number (GAPS.md: the pack refuses to seal when detected cuts fall
in the void), and it finds more — the two are not interchangeable, which is why this writes its
own document rather than filtering the old one.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from statistics import mean, median
from typing import Any

OUT = Path(__file__).with_name("mid_human_cuts.json")
DELIVERABLE = Path(
    r"S:\Deliverables\Ryan Devlin\6-17-26 Zinc Bar\Full Videos"
    r"\6-17 - Zinc Set 2 - Taurus People.mp4"
)

THRESHOLD = 0.10
SCALE_W = 320
SONG_START = 3568.48
WINDOW_D = (166.68, 256.68)
PAD = 40.0
"""Detect this far either side of the window, so the straddling shots have real lengths."""

FPS = 23.976
FRAME_ZERO = 86401


def detect(path: Path, start: float, dur: float) -> list[float]:
    cmd = [
        "ffmpeg", "-v", "info", "-nostats",
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(path),
        "-vf", f"scale={SCALE_W}:-2,select='gt(scene,{THRESHOLD})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    # pts_time is relative to the -ss seek, so it is offset back onto deliverable time.
    return [round(start + float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def timed(d: float) -> dict[str, Any]:
    mix = d + SONG_START
    return {"d": round(d, 3), "t": round(mix, 3), "frame": FRAME_ZERO + round(mix * FPS)}


def main() -> None:
    scan_start = max(0.0, WINDOW_D[0] - PAD)
    scan_end = WINDOW_D[1] + PAD
    cuts = detect(DELIVERABLE, scan_start, scan_end - scan_start)

    inside = [c for c in cuts if WINDOW_D[0] <= c <= WINDOW_D[1]]
    before = [c for c in cuts if c < WINDOW_D[0]]
    after = [c for c in cuts if c > WINDOW_D[1]]

    # Shots: from the last cut at or before the window open to the first cut after it.
    opens = [before[-1]] if before else [scan_start]
    closes = [after[0]] if after else [scan_end]
    edges = opens + inside + closes
    shots: list[dict[str, Any]] = []
    for i in range(len(edges) - 1):
        begins, ends = edges[i], edges[i + 1]
        shots.append(
            {
                "from": timed(begins),
                "to": timed(ends),
                "seconds": round(ends - begins, 3),
                "straddles_window_open": begins < WINDOW_D[0],
                "straddles_window_close": ends > WINDOW_D[1],
            }
        )
    lengths = [s["seconds"] for s in shots]
    whole = [s for s in shots if not (s["straddles_window_open"] or s["straddles_window_close"])]
    whole_lengths = [s["seconds"] for s in whole]

    gaps = [round(b - a, 3) for a, b in zip(inside, inside[1:], strict=False)]

    # What the human cut ON. The piece is about cut timing, so a cut list without the events it
    # lands near says only how often he cut, not to what.
    events_path = OUT.with_name("taurus_mid_events.json")
    events: list[dict[str, Any]] = []
    if events_path.exists():
        doc = json.loads(events_path.read_text(encoding="utf-8"))
        for field in ("solo_changes", "drum_fills", "phrase_boundaries"):
            events.extend(doc[field])
        events.extend({**one, "kind": "energy_peak"} for one in doc["energy"]["peaks"])
    alignment: list[dict[str, Any]] = []
    for cut in inside:
        mix = cut + SONG_START
        near = sorted(events, key=lambda one: abs(one["t"] - mix))[:1]
        best = near[0] if near else None
        alignment.append(
            {
                "cut": timed(cut),
                "nearest_event": (
                    {
                        "kind": best["kind"],
                        "t": best["t"],
                        "d": best["d"],
                        "frame": best["frame"],
                        "confidence": best.get("confidence"),
                        "offset_s": round(mix - best["t"], 3),
                    }
                    if best
                    else None
                ),
                "events_within_0_5s": sum(1 for one in events if abs(one["t"] - mix) <= 0.5),
            }
        )
    offsets = [abs(one["nearest_event"]["offset_s"]) for one in alignment if one["nearest_event"]]
    report = {
        "kind": "mid_human_cuts",
        "piece": "PIECE 3 — mid-song window of Taurus People",
        "source": str(DELIVERABLE),
        "method": (
            f"ffmpeg scale={SCALE_W}:-2, select gt(scene,{THRESHOLD}), metadata=print over "
            f"deliverable {scan_start:.2f}-{scan_end:.2f} s (the window plus {PAD:.0f} s of pad "
            "either side); cuts then clipped to the window, shots kept at their real lengths."
        ),
        "threshold": THRESHOLD,
        "window": {
            "deliverable_seconds": list(WINDOW_D),
            "mix_seconds": [round(WINDOW_D[0] + SONG_START, 2), round(WINDOW_D[1] + SONG_START, 2)],
            "frames": [timed(WINDOW_D[0])["frame"], timed(WINDOW_D[1])["frame"]],
            "seconds": round(WINDOW_D[1] - WINDOW_D[0], 2),
        },
        "counts": {
            "cuts_in_window": len(inside),
            "shots_touching_window": len(shots),
            "shots_wholly_inside": len(whole),
            "cuts_per_minute": round(len(inside) / ((WINDOW_D[1] - WINDOW_D[0]) / 60.0), 2),
        },
        "shot_length": {
            "mean": round(mean(lengths), 3),
            "median": round(median(lengths), 3),
            "min": round(min(lengths), 3),
            "max": round(max(lengths), 3),
            "mean_wholly_inside": round(mean(whole_lengths), 3) if whole_lengths else None,
            "median_wholly_inside": round(median(whole_lengths), 3) if whole_lengths else None,
        },
        "shot_length_histogram": {
            "<2s": sum(1 for x in lengths if x < 2),
            "2-4s": sum(1 for x in lengths if 2 <= x < 4),
            "4-8s": sum(1 for x in lengths if 4 <= x < 8),
            "8-15s": sum(1 for x in lengths if 8 <= x < 15),
            "15-30s": sum(1 for x in lengths if 15 <= x < 30),
            ">30s": sum(1 for x in lengths if x >= 30),
        },
        "gaps_between_cuts": gaps,
        "alignment_to_events": {
            "events_source": "gauntlet/recon/taurus_mid_events.json",
            "median_abs_offset_s": round(median(offsets), 3) if offsets else None,
            "within_0_25s": sum(1 for x in offsets if x <= 0.25),
            "within_0_5s": sum(1 for x in offsets if x <= 0.5),
            "within_1s": sum(1 for x in offsets if x <= 1.0),
            "cuts": alignment,
        },
        "cuts": [timed(c) for c in inside],
        "shots": shots,
        "context": {
            "last_cut_before_window": timed(before[-1]) if before else None,
            "first_cut_after_window": timed(after[0]) if after else None,
            "cuts_in_padded_scan": len(cuts),
            "padded_scan_deliverable_seconds": [round(scan_start, 2), round(scan_end, 2)],
        },
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(report["shot_length"], indent=2))
    print(json.dumps(report["shot_length_histogram"], indent=2))
    for shot in shots:
        flag = ""
        if shot["straddles_window_open"]:
            flag = "<<open"
        elif shot["straddles_window_close"]:
            flag = ">>close"
        print(
            f"  {shot['from']['d']:8.2f} -> {shot['to']['d']:8.2f}  "
            f"{shot['seconds']:6.2f} s  {flag}"
        )
    summary = {k: v for k, v in report["alignment_to_events"].items() if k != "cuts"}
    print(json.dumps(summary, indent=2))
    for one in alignment:
        near = one["nearest_event"]
        print(
            f"  cut d={one['cut']['d']:7.2f} mix={one['cut']['t']:8.2f}  "
            f"{near['kind']:16s} off={near['offset_s']:+6.2f} s  conf={near['confidence']}"
        )


if __name__ == "__main__":
    main()
