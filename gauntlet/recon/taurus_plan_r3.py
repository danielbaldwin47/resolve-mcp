"""Author the round-3 Taurus People opening cut file.

Round 2 split the panel 1-1. The two loss points were OBSTRUCTED SHOTS and the
shape of the back half. This round answers both, and keeps the one gesture both
judges praised (acceleration 31.66 / 33.84 into the 36.06 change, then release)
frame-for-frame.

Obstruction (styles/concert.md sec 4, the veto). Two windows are CONFIRMED by eye
on frames pulled from the sources (gauntlet/recon/occl_grabs_r3*.json):
  * FX6 11.97-18.94 - a black audience head/shoulder fills the lower-left
    foreground third at 13.5 s and 16.5 s. True near-field blocking. Round 2 ran
    FX6 14.98-22.72 straight through it; round 3 keeps FX6 off screen for the
    whole window (A7IV carries 10.50-20.50).
  * A7IV 41.96-42.92 - an out-of-focus head and shoulder fills the right half.
    Round 2 held A7IV 38.24-48.02 across it; round 3 keeps A7IV off screen there
    (the FX6 release shot, 36.06-44.38, covers it).
Four further FX6 windows the scan flagged are OVERRIDDEN as false positives on
the same evidence - frames at 44.5 / 47.5 / 53.5 / 57.5 / 59.5 / 68.5 / 73 / 78 /
83 / 88 s are clean piano-and-bass two-shots. What the scan is scoring there is a
small dark head at the extreme bottom-right corner (background between the lens
and an empty part of the room - concert.md sec 4's own exception) and the black
piano body in the left foreground. Noted for the occlusion tool's tuning ledger.

Back-half shape (concert.md sec 3, new since round 2). Round 2 ran five ~10 s
holds through the quiet passage and one of them - 13.6 s of static FX6 - is what
the losing judge named. Round 3 does the opposite: the quiet passage tightens
monotonically toward the drum cluster at 79.54-80.58 (9.88 / 7.26 / 5.34 / 4.92 /
4.12 / 3.64 / 3.00) and then releases into a 7.5 s hold on the soloist. Every cut
in it names a measured secondary swell or drum fill; nothing is held past 10 s.

Writes projects/mcp-tests-zinc/taurus-people-opening.cut.json.
"""

from __future__ import annotations

import json
from pathlib import Path

FPS = 24000.0 / 1001.0
REC_IN = 171959
SPAN_FRAMES = 2158  # 90.007 s
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306
T0 = 3568.4815

HERE = Path(__file__).resolve().parent
# The onset list correlate_timeline measures against, dumped by mix_onsets_r3.py and
# proved against the v2 self-review row by row. Round 2 snapped against
# taurus_window.json's onsets instead, which is a different detector run: its cuts
# read 13 / 11 / 15 ms in the self-review and needed hand nudges afterwards. Snapping
# against the measuring instrument's own list removes that whole round trip.
ONSETS = HERE / "mix_onsets_r3.json"
OUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-opening.cut.json"

# The card runs from frame 0 to here; picture starts on this frame. Unchanged from
# round 2, which both judges accepted: 56 / 23.976 = 2.3357 s against a first note
# at 2.371 s, so the reveal lands 35 ms early - one frame ahead of the entrance,
# exactly as the deliverable's own card does (2.336 s vs 2.38 s).
CARD_FRAMES = 56

# Camera-move windows (t rel, from taurus_motion.json move_runs, runs over 1 s).
# Never cut TO a camera inside one of these; ride it or wait for the settle.
FX6_MOVES = [(5.25, 7.92), (26.92, 30.42), (38.92, 40.58), (41.08, 44.25)]

# Windows a shot may not be placed across. CONFIRMED by eye, frame by frame.
BLOCKED = {"fx6": [(11.97, 18.94)], "a7iv": [(41.96, 42.92)]}

NUDGE_FRAMES: dict[int, int] = {}

# (intended cut second, angle the cut goes TO, motivation)
PLAN = [
    (
        10.50,
        "a7iv",
        "downbeat phrase boundary 10.50 (the 9.92 boundary+fill pair resolves into "
        "it) - and the last clean exit before the FX6's audience-head blocking at "
        "11.97; the kit angle takes the whole vetoed window",
    ),
    (
        20.50,
        "fx6",
        "the strongest phrase boundary of the head's first build (conf 0.82, "
        "downbeat) and the first frame after the FX6 blocking clears at 18.94 - the "
        "wide returns on a boundary, not on the veto's edge",
    ),
    (
        25.02,
        "a7iv",
        "energy peak +3.1 dB on the head's second build; the FX6 is about to reframe "
        "(26.92-30.42) so the locked angle carries the move and the wide comes back "
        "on a picture that changed while it was away (sec 4, sec 5b)",
    ),
    (
        31.66,
        "fx6",
        "downbeat phrase boundary 31.66, first clean frame after the FX6 reframe "
        "lands at 30.42 and after the window's biggest energy peak (30.02, "
        "+5.46 dB) - ACCELERATION 1 into the section change (sec 3)",
    ),
    (
        33.84,
        "a7iv",
        "downbeat phrase boundary 33.84 - ACCELERATION 2; the pair both judges "
        "praised in round 2, kept frame-for-frame",
    ),
    (
        36.06,
        "fx6",
        "THE CHANGE: solo change on a downbeat (timbre, -7.14 dB) with a drum fill on "
        "the same frame - the sax head ends and the room drops 13 dB",
    ),
    (
        44.38,
        "a7iv",
        "drum fill 44.38, on the frame the FX6's reframe settles (41.08-44.25). The "
        "shot before it is the RELEASE and it is the moving camera doing the editing "
        "(sec 4): one 8.3 s shot rides the sax off, the bass taking the lead at 40.50 "
        "and the camera settling onto the bassist - no cut needed, and it is what "
        "keeps A7IV's confirmed blocking at 41.96-42.92 off the screen",
    ),
    (
        54.26,
        "fx6",
        "drum fill (0.583, the strongest of the passage's first half) with the "
        "+1.86 dB secondary swell 0.24 s ahead of it. The A7IV shot it ends held "
        "9.9 s because the picture developed - the drummer switches to brushes and "
        "the sax walks in and parks his horn in frame (sec 3's ceiling test)",
    ),
    (
        61.52,
        "a7iv",
        "the biggest secondary swell of the whole quiet passage (+3.3 dB) with its "
        "drum fill at 61.62 - the swell is made of brushwork, so go and look at it. "
        "TIGHTENING starts here: 5.34 / 4.92 / 4.12 / 3.64 / 3.00 (sec 3)",
    ),
    (
        66.86,
        "fx6",
        "drum fill 66.86, second of the 66.02/66.86 pair - back to the two players "
        "carrying the passage",
    ),
    (
        71.78,
        "a7iv",
        "the only back-half event to survive cluster suppression (fill, score 0.833) "
        "with the +2.84 dB swell 0.24 s behind it",
    ),
    (
        75.90,
        "fx6",
        "drum fill 75.90 - the shortening run continues; the bass answers",
    ),
    (
        79.54,
        "a7iv",
        "the drum cluster 79.54 / 80.16 / 80.58, the strongest drum event of the last "
        "fifteen seconds, taken on the drums - the tightest shot of the piece sits on "
        "the thing that is happening",
    ),
    (
        82.54,
        "fx6",
        "drum fill 82.54, the cluster's resolution - RELEASE: 7.5 s on the soloist "
        "after the shortening run (sec 3: accelerate in, release after)",
    ),
]

ROLE = {
    "fx6": ("fx6_wide", FX6_ZERO),
    "a7iv": ("a7iv_kit", A7_ZERO),
}

OPEN_NOTE = (
    "the card clears one frame before the first note (2.371 s) and the widest picture "
    "of the room lands on the entrance; the shot then waits out the operator's "
    "5.25-7.92 reframe instead of being cut into it (sec 5b, sec 4)"
)


def snap(t: float, onsets: list[float], prefer_early: bool) -> tuple[int, float, float]:
    """Return (frame index rel to window start, cut seconds, signed offset to onset)."""
    best = None
    for f in range(int((t - 0.30) * FPS), int((t + 0.30) * FPS) + 1):
        ct = f / FPS
        o = min(onsets, key=lambda x: abs(x - ct))
        off = ct - o  # negative = early
        if not (0.015 <= abs(off) <= 0.042):
            continue
        side_penalty = 0.0 if (off < 0) == prefer_early else 0.020
        score = abs(ct - t) + side_penalty
        if best is None or score < best[0]:
            best = (score, f, ct, off)
    if best is None:  # nothing in range: keep the intended frame
        f = round(t * FPS)
        ct = f / FPS
        o = min(onsets, key=lambda x: abs(x - ct))
        return f, ct, ct - o
    return best[1], best[2], best[3]


def move_clash(angle: str, second: float) -> str:
    if angle != "fx6":
        return ""
    for a, b in FX6_MOVES:
        if a - 0.15 <= second <= b + 0.30:
            return f"cut to FX6 at {second:.2f}s sits inside/next to move {a}-{b}"
    return ""


def block_clash(angle: str, start: float, end: float) -> str:
    for a, b in BLOCKED[angle]:
        if start < b and end > a:
            return f"{angle} shot {start:.2f}-{end:.2f} crosses confirmed blocking {a}-{b}"
    return ""


def main() -> None:
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_rel"]

    cuts = []
    for i, (t, angle, note) in enumerate(PLAN):
        prefer_early = i % 3 != 2  # ~2:1 early, matching the director's own recut
        f, ct, off = snap(t, onsets, prefer_early)
        f += NUDGE_FRAMES.get(i, 0)
        ct = f / FPS
        off = ct - min(onsets, key=lambda x: abs(x - ct))
        clash = move_clash(angle, ct)
        if clash:
            raise SystemExit(f"plan error: {clash}")
        cuts.append(
            {"frame": f, "seconds": ct, "offset_ms": off * 1000, "angle": angle, "note": note}
        )

    bounds = [CARD_FRAMES] + [c["frame"] for c in cuts] + [SPAN_FRAMES]
    for a, b in zip(bounds, bounds[1:], strict=False):
        if b - a < 24:
            raise SystemExit(f"segment too short: {a}->{b}")

    angles = ["fx6"] + [c["angle"] for c in cuts]
    notes = [OPEN_NOTE] + [c["note"] for c in cuts]

    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        clash = block_clash(angles[i], a / FPS, b / FPS)
        if clash:
            raise SystemExit(f"plan error: {clash}")

    segments: list[dict] = [
        {
            "id": "g001",
            "gap": CARD_FRAMES,
            "note": (
                "full-frame title card over black through the room's dead air, clearing "
                "one frame before the entrance - the measured convention for this tune "
                "(styles/concert.md sec 5b). Text+ supplied by the titles pass"
            ),
        }
    ]
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        alias, zero = ROLE[angles[i]]
        segments.append(
            {
                "id": f"s{i + 1:02d}",
                "source": alias,
                "in": REC_IN + a - zero,
                "out": REC_IN + b - zero,
                "note": notes[i],
            }
        )

    doc = {
        "schema": 1,
        "timeline": {"name": "Taurus People Opening R3", "fps": 23.976},
        "sources": {
            "fx6_wide": {
                "clip": "A015C001_2606170J.MXF",
                "bin": "Zinc Bar/Footage/FX6/Set 2",
                "sync_offset": FX6_ZERO,
            },
            "a7iv_kit": {
                "clip": "20260617_D_A7IV_0006.MP4",
                "bin": "Zinc Bar/Footage/A7IV/Set 2",
                "sync_offset": A7_ZERO,
            },
            "master_mix": {
                "clip": "Zinc Set 2 Reaper v4.wav",
                "bin": "Zinc Bar/Audio",
                "sync_offset": MIX_ZERO,
            },
        },
        "audio": {
            "source": "master_mix",
            "in": REC_IN - MIX_ZERO,
            "out": REC_IN + SPAN_FRAMES - MIX_ZERO,
        },
        "segments": segments,
    }
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    total = sum(s.get("gap", 0) or (s["out"] - s["in"]) for s in segments)
    print("picture items", len(segments) - 1, "total frames", total, "expected", SPAN_FRAMES)
    print(f"{'id':5} {'angle':5} {'start_s':>8} {'dur_s':>6} {'offset_ms':>9}")
    durs = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        off = "" if i == 0 else f"{cuts[i - 1]['offset_ms']:+.0f}"
        durs.append((b - a) / FPS)
        print(f"s{i + 1:02d}   {angles[i]:5} {a / FPS:8.3f} {(b - a) / FPS:6.2f} {off:>9}")
    ds = sorted(durs)
    n = len(durs)
    med = ds[n // 2] if n % 2 else (ds[n // 2 - 1] + ds[n // 2]) / 2
    fx = sum(d for d, a in zip(durs, angles, strict=False) if a == "fx6")
    a7 = sum(d for d, a in zip(durs, angles, strict=False) if a == "a7iv")
    print(f"shots {n} median {med:.2f} mean {sum(durs) / n:.2f} max {max(durs):.2f} min {min(durs):.2f}")
    print(f"share fx6 {fx:.1f}s a7iv {a7:.1f}s")


if __name__ == "__main__":
    main()
