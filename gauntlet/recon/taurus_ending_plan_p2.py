"""Author the piece-2 cut file: the ENDING of Taurus People (last 90 s of the set).

Window: mix 3976.15-4066.15 s = Zinc SYNC 181733-183891 = 2158 frames at 23.976.
Arithmetic is round 3's, retargeted (mix zero 86401, FX6 zero 117576, A7IV zero 86306);
cuts are snapped against the same onset list correlate_timeline computes for itself
(gauntlet/recon/end_p2_recon.json, written by end_p2_recon.py) so the plan's offsets and
the self-review's offsets are one number.

What the window is (measured, gauntlet/recon/taurus_ending_events.json + end_p2_recon.py):
  * 0-72.8 s   the tune's last full-energy stretch, front = the melodic stem since long
               before the window; loudest second at 13.85 (-9.38 LUFS).
  * 72.85      the last climax and the biggest single event anywhere in the window
               (+6.46 dB prominence); onset density peaks 9.7-10.3/s just after it.
  * 74.9-80.4  the decay: -13.4 dB falling to -25.1 dB. Style: over decaying audio,
               HOLD - shortening there is the metronome the 3/3 panel named.
  * 80.9-83.3  the free coda; the beat grid's 37.6 s hole starts at 71.2 so nothing here
               is grid-measurable, and the phrase detector reports nothing after 79.6.
  * 82.91 / 83.18 / 83.43  the three-hit final figure. 83.43 is the last note of the set
               (lift +15.7 dB, sample peak 1.106). Applause attacks from 85.31.

Tail treatment (styles/concert.md 5b + gauntlet/recon/openings_survey.json):
the five deliverables all leave the picture before the file ends, with the band still
playing in the last picture frame and no applause tail; two hard-cut to black 6.5 and
7.9 s from the end and sit on black, three dissolve out over ~6-10 s. Taurus itself
dissolves 5.923 s from 83.905 and reaches black 0.167 s before the file ends. The cut
schema builds butt-joined V1 segments and literal-black gaps - it has no dissolve - so
this piece takes the family's other device at Taurus's own instant: black arrives on the
final hit, 6.55 s of it, with the master mix running underneath (which is what makes the
gap real - validate W8). Picture therefore leaves at 83.45, one frame after the last
note is struck and 0.5 s before the deliverable's dissolve begins, so the band is still
playing in the last frame; the occlusion pass's own control frame at 84.0 records them
finished - sax lowered, bassist off the strings - which is the frame this cut declines
to show.

Obstruction: gauntlet/recon/occlusion_ending.json adjudicated every flagged window in
this piece and found NO veto on either angle, so no shot here is placement-constrained.

Writes projects/mcp-tests-zinc/taurus-people-ending.cut.json.
"""

from __future__ import annotations

import json
from pathlib import Path

FPS = 24000.0 / 1001.0
REC_IN = 181733
SPAN_FRAMES = 2158  # 90.007 s, ending on 183891
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306

HERE = Path(__file__).resolve().parent
ONSETS = HERE / "end_p2_recon.json"
OUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending.cut.json"

# FX6 reframes measured over this window (end_p2_recon.py, |dx|>=1 px per 1/6 s at 192
# wide). The A7IV is genuinely locked: moving_share 0.005, not one run.
FX6_MOVES = [(19.42, 19.75), (24.75, 27.08), (27.75, 28.75), (37.25, 38.08),
             (39.75, 40.08), (40.42, 41.08), (85.42, 86.42)]

# (intended cut second, angle the cut goes TO or "black", motivation)
PLAN = [
    (
        6.59,
        "a7iv",
        "drum fill 6.59 (1.88 s, 81 hits) - the first real fill of the piece; arrive on "
        "the drummer around its start (sec 5) after the wide has established the quartet",
    ),
    (
        12.05,
        "fx6",
        "the fill at 12.05 (1.38 s) and a downbeat phrase boundary on the same frame "
        "(conf 0.702, score 1.25) - leave the kit as the fill resolves, and the wide "
        "takes the loudest second in the whole window (13.85, -9.38 LUFS)",
    ),
    (
        24.47,
        "a7iv",
        "downbeat phrase boundary 24.47 with the window's biggest drum event 0.54 s "
        "behind it (fill 25.01, 3.86 s, 118 hits) - the kit angle holds the fill, and it "
        "is what keeps the FX6's 24.75-28.75 reframe off the screen (sec 4: the moving "
        "camera is a second editor; do not cut into its move)",
    ),
    (
        29.99,
        "fx6",
        "fill 29.99 (2.26 s) with a downbeat phrase boundary on the same frame (0.702). "
        "The wide comes back 1.2 s after its reframe settles and it is a DIFFERENT "
        "PICTURE - the pianist, who is the front here, was outside the old framing and "
        "is inside this one. That reframe is the piece's third picture (sec 4)",
    ),
    (
        37.23,
        "a7iv",
        "the strongest downbeat phrase boundary of the middle of the piece (0.803, score "
        "1.15); it also carries the FX6's second reframe run (37.25-41.08) off screen",
    ),
    (
        42.35,
        "fx6",
        "energy peak +2.64 dB at 42.35 with a phrase boundary 0.12 s ahead of it - and "
        "1.27 s after the FX6 settles, so the wide returns on an event rather than on "
        "the edge of a move",
    ),
    (
        49.23,
        "a7iv",
        "drum fill 49.23 (1.68 s): arrive at the fill's start and ride it into the "
        "+2.99 dB energy peak at 49.85 (sec 5)",
    ),
    (
        53.77,
        "fx6",
        "phrase boundary 53.77 with the 53.47 fill resolving into it - back to the band "
        "for the last build",
    ),
    (
        62.93,
        "a7iv",
        "the strongest phrase boundary in the entire window (62.33: conf 0.846, "
        "downbeat, rest 0.11 s, held ratio 5.2, score 1.396) - and the cut is placed at "
        "the far side of its rest, 37 ms before the music resumes at 62.93, which is the "
        "corpus's own mechanism rather than an offset chased for its own sake (sec 1: "
        "the phrase is the placement unit). The onset list is empty for 0.66 s here, so "
        "this is the quietest frame in the neighbourhood to change picture on; the 66.41 "
        "fill lands inside the shot",
    ),
    (
        66.97,
        "fx6",
        "SOLO CHANGE 66.97 (downbeat, timbre step inside the melodic stem) with its own "
        "phrase boundary and the 66.41 fill just behind - the front changes, so the "
        "angle that holds the whole band takes it (sec 4: follow the audience's gaze at "
        "a structure change)",
    ),
    (
        72.85,
        "a7iv",
        "THE LAST CLIMAX: energy peak +6.46 dB, the biggest-prominence event anywhere in "
        "the 90 s, and onset density peaks at 9.7-10.3/s over the three seconds after it "
        "- the drums are what is happening, so the cut goes to the drums",
    ),
    (
        76.08,
        "fx6",
        "phrase boundary 76.08 at the top of the decay (-13.4 dB here falling to "
        "-25.1 dB by 80.35). RELEASE: one long wide holds the ritard, the free coda and "
        "the three-hit final figure. Over decaying audio the style layer's 3/3 panel "
        "says hold rather than shrink, so this shot takes 7.4 s and no cut is spent on "
        "the coda's thinning pulse",
    ),
    (
        83.45,
        "black",
        "BLACK ON THE LAST NOTE: the final figure lands 82.91 / 83.18 / 83.43 and 83.43 "
        "is the last note of the set (+15.7 dB lift, sample peak 1.106). The picture "
        "leaves one frame after it is struck - band still playing in the last frame, no "
        "release, no applause tail (sec 5b) - and 6.55 s of literal black runs under the "
        "master mix to the end of the window, which is where the deliverable's own "
        "5.9 s dissolve-to-black sits",
    ),
]

ROLE = {"fx6": ("fx6_wide", FX6_ZERO), "a7iv": ("a7iv_kit", A7_ZERO)}

OPEN_NOTE = (
    "open on the wide, the whole quartet in one frame: the piece starts inside the tune's "
    "last full-energy stretch, so the establishing picture is the band, not a detail"
)


def snap(t: float, onsets: list[float], prefer_early: bool) -> tuple[int, float, float]:
    """Return (frame rel to window start, cut seconds, signed offset to nearest onset).

    17-41 ms from a transient and never on one: the corpus signature (sec 1).
    """
    for reach in (0.30, 0.60):  # widen once rather than fall through to an unsnapped frame
        best = None
        for f in range(int((t - reach) * FPS), int((t + reach) * FPS) + 1):
            ct = f / FPS
            o = min(onsets, key=lambda x: abs(x - ct))
            off = ct - o  # negative = early
            # Band is 17-41 ms; the plan keeps a frame of margin inside it so a
            # millisecond of disagreement with correlate's own clock cannot push a cut
            # outside it.
            if not (0.019 <= abs(off) <= 0.038):
                continue
            # 50 ms: a frame on the wanted side wins unless it is more than a frame
            # further from the intended moment. Round 3 used 20 ms and got a 3:1 late
            # lean, the opposite of the 2:1 early lean the director's recut shows (sec 6).
            side_penalty = 0.0 if (off < 0) == prefer_early else 0.050
            score = abs(ct - t) + side_penalty
            if best is None or score < best[0]:
                best = (score, f, ct, off)
        if best is not None:
            return best[1], best[2], best[3]
    raise SystemExit(f"plan error: nothing in the 19-38 ms band within 0.6 s of {t:.2f}s")


def move_clash(angle: str, second: float) -> str:
    if angle != "fx6":
        return ""
    for a, b in FX6_MOVES:
        if a - 0.15 <= second <= b + 0.30:
            return f"cut to FX6 at {second:.2f}s sits inside/next to reframe {a}-{b}"
    return ""


def main() -> None:
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_rel"]

    cuts = []
    for i, (t, angle, note) in enumerate(PLAN):
        # ~2:1 early, the lean the director's own recut shows; the last cut is late on
        # purpose so the final hit is seen being struck before the picture goes.
        prefer_early = (i % 3 != 2) and angle != "black"
        f, ct, off = snap(t, onsets, prefer_early)
        clash = move_clash(angle, ct)
        if clash:
            raise SystemExit(f"plan error: {clash}")
        cuts.append({"frame": f, "seconds": ct, "offset_ms": off * 1000,
                     "angle": angle, "note": note})

    bounds = [0] + [c["frame"] for c in cuts] + [SPAN_FRAMES]
    for a, b in zip(bounds, bounds[1:], strict=False):
        if b - a < 24:
            raise SystemExit(f"segment too short: {a}->{b}")

    angles = ["fx6"] + [c["angle"] for c in cuts]
    notes = [OPEN_NOTE] + [c["note"] for c in cuts]

    segments: list[dict] = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        if angles[i] == "black":
            segments.append({"id": f"g{i + 1:02d}", "gap": b - a, "note": notes[i]})
            continue
        alias, zero = ROLE[angles[i]]
        segments.append({
            "id": f"s{i + 1:02d}",
            "source": alias,
            "in": REC_IN + a - zero,
            "out": REC_IN + b - zero,
            "note": notes[i],
        })

    doc = {
        "schema": 1,
        "timeline": {"name": "Taurus People Ending P2", "fps": 23.976},
        "sources": {
            "fx6_wide": {"clip": "A015C001_2606170J.MXF",
                         "bin": "Zinc Bar/Footage/FX6/Set 2", "sync_offset": FX6_ZERO},
            "a7iv_kit": {"clip": "20260617_D_A7IV_0006.MP4",
                         "bin": "Zinc Bar/Footage/A7IV/Set 2", "sync_offset": A7_ZERO},
            "master_mix": {"clip": "Zinc Set 2 Reaper v4.wav", "bin": "Zinc Bar/Audio",
                           "sync_offset": MIX_ZERO},
        },
        "audio": {"source": "master_mix", "in": REC_IN - MIX_ZERO,
                  "out": REC_IN + SPAN_FRAMES - MIX_ZERO},
        "segments": segments,
    }
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    total = sum(s.get("gap", 0) or (s["out"] - s["in"]) for s in segments)
    print("items", len(segments), "picture", len([s for s in segments if "source" in s]),
          "total frames", total, "expected", SPAN_FRAMES)
    print(f"{'id':5} {'angle':5} {'start_s':>8} {'dur_s':>6} {'offset_ms':>9}")
    share = {"fx6": 0.0, "a7iv": 0.0, "black": 0.0}
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        off = cuts[i - 1]["offset_ms"] if i else 0.0
        print(f"{segments[i]['id']:5} {angles[i]:5} {a / FPS:8.3f} {(b - a) / FPS:6.2f} "
              f"{off:+9.1f}")
        share[angles[i]] += (b - a) / FPS
    pic = share["fx6"] + share["a7iv"]
    print(f"picture {pic:.2f}s  fx6 {share['fx6']:.2f}s ({100 * share['fx6'] / pic:.1f}%)  "
          f"a7iv {share['a7iv']:.2f}s ({100 * share['a7iv'] / pic:.1f}%)  "
          f"black {share['black']:.2f}s")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
