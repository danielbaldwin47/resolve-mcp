"""Author the Taurus People opening cut file from the measured window.

Cut points are chosen at measured phrase entrances (mid-band rest ends) and at
the section change, then snapped so each shot start sits 17-41 ms from the
nearest measured transient without landing on one (styles/concert.md sec 1).
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

BLACK_FRAMES = 13  # 0.54 s breath of black over the room's silence

# Frame nudges applied after snapping, from the v1 self-review: correlate_timeline
# measures against its own onsets, and four cuts read differently there - two sat
# ON a transient (+1 ms, -4 ms: the fourth-wall risk base.md names) and two sat
# 63-64 ms out. Each nudge is one frame, away from the transient or back into the
# 17-41 ms band. Key = index into PLAN.
NUDGE_FRAMES = {4: -1, 5: +2, 7: -1, 8: +1, 10: -1, 11: +1}

# (intended cut second, angle the cut goes TO, note)
PLAN = [
    (4.88, "a7iv", "off the wide a beat before it starts reframing; sax phrase ends"),
    (11.58, "fx6", "phrase entrance after the 11.3-11.6 s rest; wide has landed and settled"),
    (18.81, "a7iv", "on the sax's answering phrase - the drums are the other half of it"),
    (22.87, "fx6", "phrase entrance after the 22.6-22.9 s rest"),
    (32.70, "a7iv", "wide has held through its own reframe; leave it 2.3 s after it landed"),
    (36.56, "fx6", "back to the wide for the sax's last phrase of the head - it is about to move"),
    (49.00, "a7iv", "the bass solo has settled; the drummer's brushes are the other voice"),
    (53.00, "fx6", "phrase entrance; back to the bass"),
    (61.20, "a7iv", "phrase entrance - a glance at the interaction"),
    (63.90, "fx6", "short glance over, straight back to the soloist"),
    (71.40, "a7iv", "phrase entrance"),
    (76.00, "fx6", "phrase entrance; the closing hold of the opening"),
]

ALTERNATES = {"s04", "s06", "s10"}

ROLE = {
    "fx6": ("fx6_wide", FX6_ZERO),
    "a7iv": ("a7iv_kit", A7_ZERO),
}


def snap(t: float, onsets: list[float], used: list[float], prefer_early: bool) -> tuple[int, float, float]:
    """Return (frame index rel to window start, cut seconds, signed offset to nearest onset)."""
    best = None
    for f in range(int((t - 0.60) * FPS), int((t + 0.60) * FPS) + 1):
        ct = f / FPS
        if not onsets:
            continue
        o = min(onsets, key=lambda x: abs(x - ct))
        off = ct - o  # negative = early
        mag = abs(off)
        if not (0.015 <= mag <= 0.042):
            continue
        side_penalty = 0.0 if (off < 0) == prefer_early else 0.020
        score = abs(ct - t) + side_penalty
        if best is None or score < best[0]:
            best = (score, f, ct, off)
    if best is None:  # nothing in range: keep the intended frame
        f = round(t * FPS)
        ct = f / FPS
        o = min(onsets, key=lambda x: abs(x - ct)) if onsets else ct
        return f, ct, ct - o
    return best[1], best[2], best[3]


def main() -> None:
    win = json.loads(WINDOW.read_text(encoding="utf-8"))
    onsets = [o["t"] - T0 for o in win["onsets"]["list"]]

    cuts = []
    for i, (t, angle, note) in enumerate(PLAN):
        prefer_early = i % 3 != 2  # ~2:1 early, matching the director's own recut
        f, ct, off = snap(t, onsets, [], prefer_early)
        f += NUDGE_FRAMES.get(i, 0)
        cuts.append({"frame": f, "seconds": f / FPS, "offset_ms": off * 1000, "angle": angle, "note": note})

    # segment boundaries in window frames
    bounds = [BLACK_FRAMES] + [c["frame"] for c in cuts] + [SPAN_FRAMES]
    for a, b in zip(bounds, bounds[1:], strict=False):
        if b - a < 24:
            raise SystemExit(f"segment too short: {a}->{b}")

    angles = ["fx6"] + [c["angle"] for c in cuts]
    notes = ["black lifts on the settled wide 1.9 s before the band hits - the room, then the band"] + [
        c["note"] for c in cuts
    ]

    segments: list[dict] = [
        {"id": "g001", "gap": BLACK_FRAMES, "note": "a breath of black over the room's silence before the tune"}
    ]
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        angle = angles[i]
        alias, zero = ROLE[angle]
        sid = f"s{i + 1:02d}"
        rec_in = REC_IN + a
        seg = {
            "id": sid,
            "source": alias,
            "in": rec_in - zero,
            "out": REC_IN + b - zero,
            "note": notes[i],
        }
        if sid in ALTERNATES:
            other = "a7iv" if angle == "fx6" else "fx6"
            oalias, ozero = ROLE[other]
            seg["alternates"] = [
                {"source": oalias, "in": rec_in - ozero, "out": REC_IN + b - ozero}
            ]
        segments.append(seg)

    doc = {
        "schema": 1,
        "timeline": {"name": "Taurus People Opening", "fps": 23.976},
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
    print("segments", len(segments) - 1, "total frames", total, "expected", SPAN_FRAMES)
    print(f"{'id':5} {'angle':5} {'start_s':>8} {'dur_s':>6} {'offset_ms':>9}")
    prev = None
    durs = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        off = "" if i == 0 else f"{cuts[i - 1]['offset_ms']:+.0f}"
        durs.append((b - a) / FPS)
        print(f"s{i + 1:02d}   {angles[i]:5} {a / FPS:8.2f} {(b - a) / FPS:6.2f} {off:>9}")
        prev = b
    durs_sorted = sorted(durs)
    n = len(durs)
    med = durs_sorted[n // 2] if n % 2 else (durs_sorted[n // 2 - 1] + durs_sorted[n // 2]) / 2
    fx = sum(d for d, a in zip(durs, angles, strict=False) if a == "fx6")
    print(f"shots {n} median {med:.2f} mean {sum(durs) / n:.2f} max {max(durs):.2f} min {min(durs):.2f}")
    print(f"fx6 share {fx / (SPAN_FRAMES / FPS) * 100:.1f}%  cuts/min {(n) / (SPAN_FRAMES / FPS) * 60:.1f}")
    print("offsets ms:", [round(c["offset_ms"]) for c in cuts])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
