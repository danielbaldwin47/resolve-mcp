"""Revise the R1 whole-song Taurus cut into R2: the gearbox, and only the gearbox.

R1 lost 1-2 on one convergent point -- one-speed cutting -- while the gestures, the tail,
the titles and the occlusion discipline all held. So this is a *boundary* edit, not a re-cut:
the R1 cut list is the spine, and every change is either a boundary removed (the quiet gears
get sparser) or a boundary added on a named event (the loud gears get driven). Angles follow
by strict alternation from the first shot, exactly as R1 ran them, so a removal or an addition
never has to be paired by hand.

The three carried occlusion vetoes are re-checked against the result rather than assumed:
this edit flips which camera holds several windows, and a veto is only respected if the
flip is measured against it.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "projects" / "mcp-tests-zinc" / "taurus-people-full.cut.json"
DST = ROOT / "projects" / "mcp-tests-zinc" / "taurus-people-full-r2.cut.json"
ONSETS = ROOT / "gauntlet" / "recon" / "full_onsets.json"
OUT = ROOT / "gauntlet" / "recon" / "p4r2_revise.json"

FPS = 24000 / 1001.0
SYNC0 = 171959  # the song's first frame on 'Zinc SYNC'

# Boundaries the R2 gearbox removes, by d. Quiet sections hold longer: the floor releases
# into one long locked frame, and the breath's last three shots become one.
REMOVE_D: list[tuple[float, str]] = [
    (75.83, "floor: the 3.8/2.9 s tail chain was the fastest cutting in the quietest music"),
    (79.66, "floor: same chain"),
    (82.54, "floor: same chain -- the release becomes a single 17 s locked frame"),
    (149.36, "breath: the 4.0/3.5 s pair under the sparsest bass writing"),
    (153.36, "breath: same pair -- the kit holds through the handover instead"),
    (366.12, "fast: pays for the 361.65 accent so the fast zone keeps its count and gains a hold"),
]

# Boundaries the R2 gearbox adds, by the onset each snaps to. Every one is a named event in
# full_events.json -- a fill start, a downbeat phrase boundary, or a struck energy peak.
ADD: list[tuple[float, str, str]] = [
    (78.9440, "fill_start d78.92", "floor: the release lands on the fill rather than on a clock"),
    (268.9707, "phrase d268.98", "plateau: a 2.5 s jab so the post-arrival hold cannot park"),
    (291.9253, "phrase* d291.94", "plateau: breaks R1's 14.6 s park before the build"),
    (320.5013, "fill_start d320.52", "build: first of the accent pair tightening into the summit"),
    (324.2133, "fill_start d324.12 (its next struck hit -- the start itself has no onset)",
     "build: second of the pair"),
    (361.5467, "phrase* d361.66 (the hit before it)",
     "fast: a 1.6 s accent, and the shot after it becomes a 7.8 s hold"),
    (397.8987, "phrase d397.96 (ranked #1)", "summit: tightening onto the summit's arrival"),
    (403.6160, "fill_start d403.66",
     "summit: breaks R1's 14.4 s shot -- the flatline the judges named"),
    (407.4667, "phrase d407.42", "summit: same shot, second break"),
    (433.2373, "phrase* d433.24",
     "summit: a 1.6 s accent INSIDE a loud-tercile run -- R2's first pass put this burst at "
     "436-441, which measures mid, so the accents moved to where the music actually peaks"),
    (437.6640, "fill_start d437.66", "summit: burst"),
    (441.5573, "phrase* d441.58", "summit: burst closes"),
    (459.6907, "fill_start + phrase d459.70",
     "summit: the second loud-tercile accent, 1.4 s inside the sustained finale"),
    (463.4347, "phrase* d463.46", "summit: breaks the last long shot before the ending"),
]

SECTIONS: list[tuple[str, float, float]] = [
    ("head", 0.00, 36.06), ("floor", 36.06, 96.02), ("breath", 96.02, 153.52),
    ("trade", 153.52, 232.92), ("plateau", 232.92, 294.52), ("build", 294.52, 328.02),
    ("fast", 328.02, 381.02), ("summit", 381.02, 474.64), ("ending", 474.64, 497.664),
]
HUMAN = {
    "head": 9.98, "floor": 8.01, "breath": 7.30, "trade": 9.07, "plateau": 7.79,
    "build": 8.96, "fast": 14.72, "summit": 10.90, "ending": 2.61,
}
# angle, veto span in d -- the angle must NOT be on screen across it.
VETOES: list[tuple[str, float, float]] = [
    ("fx6_wide", 11.97, 18.94),
    ("a7iv_kit", 41.96, 42.92),
    ("a7iv_kit", 229.58, 233.28),
]


def d_of(frame: int) -> float:
    return (frame - SYNC0) / FPS


def frame_of(onset_d: float) -> int:
    """The last frame at or before the transient -- the cut lands early, never on top of it."""
    return SYNC0 + int(onset_d * FPS)


def main() -> None:
    cut = json.loads(SRC.read_text(encoding="utf-8"))
    offsets = {k: v["sync_offset"] for k, v in cut["sources"].items()}
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_d"]

    gap = cut["segments"][0]
    assert "gap" in gap, "expected the title card gap first"
    shots = cut["segments"][1:]
    first_angle = shots[0]["source"]

    # R1's boundaries in SYNC frames: each shot's head, plus the last shot's tail.
    bounds = [s["in"] + offsets[s["source"]] for s in shots]
    end = shots[-1]["out"] + offsets[shots[-1]["source"]]

    removed: list[dict[str, Any]] = []
    for d, why in REMOVE_D:
        near = min(bounds, key=lambda f: abs(d_of(f) - d))
        assert abs(d_of(near) - d) < 0.06, f"no R1 boundary at d={d} (nearest {d_of(near):.3f})"
        bounds.remove(near)
        removed.append({"d": round(d_of(near), 3), "frame": near, "why": why})

    added: list[dict[str, Any]] = []
    for onset, event, why in ADD:
        snapped = min(onsets, key=lambda o: abs(o - onset))
        assert abs(snapped - onset) < 0.05, f"no onset near {onset}"
        frame = frame_of(snapped)
        assert frame not in bounds, f"boundary already at {frame}"
        bounds.append(frame)
        added.append(
            {
                "d": round(d_of(frame), 3), "frame": frame, "event": event, "why": why,
                "onset_d": round(snapped, 4),
                "ms_before_transient": round((snapped - d_of(frame)) * 1000, 1),
            }
        )
    bounds.sort()

    # Rebuild the shot list: strict alternation from R1's first angle.
    angles = [a for a in ("fx6_wide", "a7iv_kit")]
    if angles[0] != first_angle:
        angles.reverse()
    out_segments: list[dict[str, Any]] = [gap]
    notes = {s["in"] + offsets[s["source"]]: s.get("note", "") for s in shots}
    add_why = {a["frame"]: a["why"] for a in added}
    r1_len = {
        s["in"] + offsets[s["source"]]: (s["out"] - s["in"]) / FPS for s in shots
    }
    r1_angle = {s["in"] + offsets[s["source"]]: s["source"] for s in shots}
    rows: list[dict[str, Any]] = []
    for i, head in enumerate(bounds):
        tail = bounds[i + 1] if i + 1 < len(bounds) else end
        angle = angles[i % 2]
        note = notes.get(head) or add_why.get(head, "")
        if head in add_why and notes.get(head):
            note = f"{notes[head]} | R2: {add_why[head]}"
        elif head in add_why:
            note = f"R2 gearbox: {add_why[head]}"
        # An inherited note states R1's duration and R1's angle; both can have moved under
        # the gearbox, so say so rather than leaving a note that reads false.
        seconds = (tail - head) / FPS
        drift = []
        if head in r1_len and abs(r1_len[head] - seconds) > 0.05:
            drift.append(f"was {r1_len[head]:.2f} s")
        if head in r1_angle and r1_angle[head] != angle:
            drift.append(f"was {r1_angle[head]}")
        if drift:
            note = f"[R2 {seconds:.2f} s on {angle}; {', '.join(drift)} in R1] {note}"
        out_segments.append(
            {
                "id": f"s{i + 1:03d}", "source": angle,
                "in": head - offsets[angle], "out": tail - offsets[angle], "note": note,
            }
        )
        rows.append(
            {"d": d_of(head), "sec": (tail - head) / FPS, "angle": angle, "frame": head}
        )

    assert out_segments[-1]["source"] == "fx6_wide", "the ensemble wide must close the song"
    assert abs(rows[-1]["sec"] - 13.72) < 0.05, "the 13.7 s tail shot changed"

    # Vetoes: a flipped boundary must not have walked a blocked angle onto screen.
    breaches = []
    for angle, lo, hi in VETOES:
        for r in rows:
            if r["angle"] == angle and r["d"] < hi and r["d"] + r["sec"] > lo:
                breaches.append({"angle": angle, "veto": [lo, hi], "shot_d": round(r["d"], 2)})
    assert not breaches, f"occlusion veto breached: {breaches}"

    cut["timeline"]["name"] = "Taurus People Full P4 R2"
    cut["segments"] = out_segments
    DST.write_text(json.dumps(cut, indent=1) + "\n", encoding="utf-8")

    # --- the gear table -----------------------------------------------------------------
    total = sum(r["sec"] for r in rows) + gap["gap"] / FPS
    lengths = [r["sec"] for r in rows]
    song_cpm = len(rows) / total * 60
    print(f"R2 shots={len(rows)} cuts={len(rows) - 1} total={total:.3f}s cpm={song_cpm:.2f}")
    print(
        f"mean={statistics.fmean(lengths):.2f} median={statistics.median(lengths):.2f} "
        f"cv={statistics.pstdev(lengths) / statistics.fmean(lengths):.3f} "
        f"sub2s={sum(1 for x in lengths if x < 2.0)}"
    )
    print(f"{'section':9}{'shots':>6}{'cpm':>7}{'human':>7}{'ratio':>7}{'mean':>7}{'med':>7}{'cv':>7}{'max':>7}")
    withins = []
    table = []
    for name, lo, hi in SECTIONS:
        ss = [r for r in rows if lo <= r["d"] < hi]
        ln = [r["sec"] for r in ss]
        cv = statistics.pstdev(ln) / statistics.fmean(ln)
        withins.append(cv)
        cpm = len(ss) / (hi - lo) * 60
        table.append({"section": name, "shots": len(ss), "cpm": round(cpm, 2),
                      "human_cpm": HUMAN[name], "cv": round(cv, 3),
                      "max_shot": round(max(ln), 2)})
        print(
            f"{name:9}{len(ss):6d}{cpm:7.2f}{HUMAN[name]:7.2f}{cpm / HUMAN[name]:7.2f}"
            f"{statistics.fmean(ln):7.2f}{statistics.median(ln):7.2f}{cv:7.3f}{max(ln):7.2f}"
        )
    quiet = [r for r in rows if r["d"] < 232.92]
    loud = [r for r in rows if 232.92 <= r["d"] < 474.64]
    qcpm, lcpm = len(quiet) / 232.92 * 60, len(loud) / 241.72 * 60
    drive = [r for r in rows if 330.0 <= r["d"] < 480.0]
    print(f"quiet band {qcpm:.2f} cpm (gear {qcpm / song_cpm:.2f})  "
          f"loud band {lcpm:.2f} cpm (gear {lcpm / song_cpm:.2f})  ratio {lcpm / qcpm:.2f}")
    print(f"d330-480: {len(drive)} shots, {len(drive) / 150 * 60:.2f} cpm (human 12.0)")
    print(f"mean within-section CV {statistics.fmean(withins):.3f} (R1 0.488, human 0.692)")
    print(f"sub-2s: {[(round(r['d'], 2), round(r['sec'], 2)) for r in rows if r['sec'] < 2.0]}")

    OUT.write_text(
        json.dumps(
            {"removed": removed, "added": added, "sections": table,
             "shots": len(rows), "cuts": len(rows) - 1, "song_cpm": round(song_cpm, 2),
             "shot_cv": round(statistics.pstdev(lengths) / statistics.fmean(lengths), 3),
             "quiet_cpm": round(qcpm, 2), "loud_cpm": round(lcpm, 2),
             "band_ratio": round(lcpm / qcpm, 2),
             "drive_cpm": round(len(drive) / 150 * 60, 2),
             "within_section_cv": round(statistics.fmean(withins), 3)},
            indent=1,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {DST}")


if __name__ == "__main__":
    main()
