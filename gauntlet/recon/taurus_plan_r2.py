"""Author the round-2 Taurus People opening cut file.

Round 1 lost on staging: 13 frames (0.54 s) of black at the head, which
styles/concert.md sec 5b calls a glitch rather than a device. Round 2 replaces it
with the measured convention for this exact tune - a full-frame title card held
through the dead air, clearing one frame before the entrance.

Every cut below names the measured event that motivates it (G9: "every cut
carries a nameable motivation, and 'time elapsed' is not one"). Events come from
gauntlet/recon/taurus_events.json; camera-move windows from taurus_motion.json.
Cut frames are then snapped so each shot start sits 17-41 ms from the nearest
measured transient without landing on one (sec 1), searching only +/-0.30 s so a
cut cannot wander off the event that motivates it.

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
WINDOW = HERE / "taurus_window.json"
OUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-opening.cut.json"

# The card runs from frame 0 to here; picture starts on this frame.
# 56 / 23.976 = 2.3357 s. The first note is at 2.371 s (measured off the mix at
# 10 ms resolution, and it agrees with openings_survey's 2.38 s read off the
# deliverable), so the reveal lands 35 ms early - inside the same 17-41 ms band
# every other cut in this file uses, and one frame ahead of the entrance exactly
# as the deliverable's own card does (2.336 s vs 2.38 s).
CARD_FRAMES = 56

# Camera-move windows (t rel, from taurus_motion.json move_runs). Never cut TO a
# camera inside one of these; ride it or wait for the settle.
FX6_MOVES = [(5.25, 7.92), (26.92, 30.42), (38.92, 40.58), (41.08, 44.25)]

# Frame nudges applied after snapping, from the v1 self-review. correlate_timeline
# measures against its own onset list, not taurus_window's, and read the first three
# cuts at 13 / 11 / 15 ms - close enough to the transient to risk landing on one
# (sec 1: "near, not on"). One frame each moves them to ~29 ms and pulls the
# timeline's median offset from 20 ms to the middle of the measured 17-41 ms band.
# Side does not matter (sec 1: "there is no direction rule"). Key = index into PLAN.
NUDGE_FRAMES = {0: +1, 1: +1, 2: +1}

# (intended cut second, angle the cut goes TO, motivation)
PLAN = [
    (
        8.24,
        "a7iv",
        "phrase boundary 8.24, the first one after the operator's 5.25-7.92 reframe "
        "lands - the wide waited the pan out rather than being cut into (sec 4)",
    ),
    (
        14.98,
        "fx6",
        "shortlisted downbeat phrase boundary (bar 3951) with the 14.42 drum fill "
        "resolving into it; the wide has been settled 7.1 s",
    ),
    (
        22.72,
        "a7iv",
        "the top-scored event in the window (1.348, downbeat, bar 3958) - the head's "
        "strongest phrase turn, taken to the kit",
    ),
    (
        31.66,
        "fx6",
        "phrase boundary 31.66; the drum cam has just carried the FX6's 26.9-30.4 "
        "reframe and the window's biggest energy peak (30.02 s, +5.46 dB), so the "
        "wide comes back on a picture that changed while it was away - a scale "
        "change, not a framing return (sec 4)",
    ),
    (
        33.84,
        "a7iv",
        "phrase boundary 33.84 - ACCELERATION into the section change (sec 3)",
    ),
    (
        36.06,
        "fx6",
        "THE CHANGE: solo change on a downbeat (timbre, -7.14 dB) with a drum fill on "
        "the same frame - the sax head ends and the room drops 13 dB",
    ),
    (
        38.24,
        "a7iv",
        "phrase boundary 38.24, one beat before the FX6 leaves to follow the sax off; "
        "the locked cam takes the RELEASE and holds the walk-off and the bass's "
        "entrance unbroken (sec 3: accelerate in, release into stillness after)",
    ),
    (
        48.02,
        "fx6",
        "first energy swell of the quiet passage (+2.2 dB); the FX6 settled 3.8 s ago "
        "on a picture the sax has left - piano and bass alone. The reveal of the new "
        "passage's own frame",
    ),
    (
        61.52,
        "a7iv",
        "the biggest energy swell in the whole quiet passage (+3.3 dB) with its drum "
        "fill at 61.62 - the swell is made of brushwork, so go and look at it",
    ),
    (
        71.78,
        "fx6",
        "shortlisted drum fill (0.633, the only back-half event to survive cluster "
        "suppression) plus the 72.02 swell (+2.84 dB); back to the two players "
        "carrying the passage",
    ),
    (
        80.16,
        "a7iv",
        "the fill cluster at 79.54 / 80.16 / 80.58 - the strongest drum event of the "
        "last fifteen seconds; the closing hold",
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


def main() -> None:
    win = json.loads(WINDOW.read_text(encoding="utf-8"))
    onsets = [o["t"] - T0 for o in win["onsets"]["list"]]

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
        "timeline": {"name": "Taurus People Opening R2", "fps": 23.976},
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
    print(f"shots {n} median {med:.2f} mean {sum(durs) / n:.2f} max {max(durs):.2f} min {min(durs):.2f}")
    print(f"fx6 share {fx / (SPAN_FRAMES / FPS) * 100:.1f}%  cuts/min {n / (SPAN_FRAMES / FPS) * 60:.1f}")
    print("offsets ms:", [round(c["offset_ms"]) for c in cuts])
    print("card frames", CARD_FRAMES, f"= {CARD_FRAMES / FPS:.4f} s; entrance 2.371 s")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
