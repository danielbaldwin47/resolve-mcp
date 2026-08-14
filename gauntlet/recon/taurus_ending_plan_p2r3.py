"""Author the piece-2 ROUND-3 cut file: the ENDING of Taurus People (last 90 s of the set).

Window: mix 3976.15-4066.15 s = Zinc SYNC 181733-183891 = 2158 frames at 23.976.
Arithmetic is rounds 1-2's, unchanged (mix zero 86401, FX6 zero 117576, A7IV zero 86306);
cuts snap against the same onset list correlate_timeline computes for itself
(gauntlet/recon/end_p2_recon.json) so the plan's offsets and the self-review's are one
number.

WHAT ROUND 2 LOST ON (3/3 panel), and the only thing that changed here
----------------------------------------------------------------------
The panel faulted ONE thing and it was unanimous: the piece cut to the A7IV kit angle
0.69 s before the final cadence and played the cadence, the applause and the whole
dissolve on it -- a cymbal in the near field and the drummer's back. That is now
styles/concert.md sec 5b, "The set's last image belongs to the ensemble", and sec 3,
"into a free or rubato coda the cutting decelerates".

So round 3 is round 2 with its LAST CUT DELETED and nothing else moved. Every shot the
panel did not fault is frame-identical: the event-motivated body, the W1->W2 ride at
23.94, the four pictures, the quiet pocket held whole 23.94-37.25, the tail numbers.

  round 2   ... 66.82 fx6 | 72.86 a7iv | 76.12 fx6 (6.59 s) | 82.71 a7iv (7.13 s to end)
  round 3   ... 66.82 fx6 | 72.86 a7iv | 76.12 fx6 -------------------- 13.72 s to end

What that buys, against the three things the round-3 brief asked for:

1. THE LAST IMAGE IS THE ENSEMBLE. The last climax (72.85, +6.46 dB prominence, the
   biggest event in the 90 s) still visits the kit, and briefly -- 3.25 s, the shortest
   shot in the cut. The piece is then back on the widest framing it has at 76.12, which
   is 6.60 s before the near-gap at 82.72 and 7.28 s before the cadence at 83.40. The
   deadline sec 5b sets -- "by the approach to the cadence the picture is already where
   it means to finish" -- is met by more than seven seconds, so nothing arriving late has
   to be answered by cutting away. Cadence, applause and all 142 frames of the dissolve
   play on the band.

   And the framing genuinely holds the band: checked on the source grabs at 76.50 and
   83.40 (gauntlet/recon/end_p2_grabs/FX6_076.50.jpg, FX6_083.40.jpg), the FX6's third
   plateau carries piano at the left, bassist, saxophonist centre and the DRUMMER at the
   right -- all four men in one frame. The A7IV holds one of them.

2. IT DECELERATES INTO THE CODA. The grid's last measurable beat is 71.19; everything
   after it is free. Inside the coda the shots now run 3.25 -> 13.72 s and there is
   exactly ONE cut between 76 s and the cadence (the 76.12 return) and NONE after it.
   The 3.25 s kit shot is the acceleration half of sec 3's accelerate-and-release pair
   and it is answering a rise, not a schedule: onset density climbs 8.3 -> 10.3/s across
   it and the rms is still -15 to -17. The decay proper starts at 75.35, and the shot
   that carries it is the longest in the piece.

3. THE HOME-ANGLE SHARE COMES BACK INTO THE CORPUS BAND. Round 2 read 62.7% on the wide,
   under the 68.5-76.3% the five corpus timelines report (sec 4). Deleting the last cut
   moves 7.13 s from the kit to the wide with no other change: 70.6% / 29.4%.

Not a park (sec 5b's second coexistence note). The last shot is 13.72 s of which 5.92 s
is under the dissolve, so 7.80 s at full opacity -- and those seconds are not waiting:
the decay steepens through them (rms -18.3 at 76.4 to -27.2 at 80.4), the trough at 80.35
is the quietest point before the gap, the last statement comes back up at 80.85, the
near-gap at 82.72 is the quietest 30 ms in the window, and the three-hit final figure
lands at 82.96 / 83.17 / 83.40. The camera also moves under the dissolve -- it walks back
right 8 px at 85.42 -- so the final image opens out toward the kit as it goes to black,
which is declared as a ride rather than dodged.

Tail: unchanged from round 2 and it is the deliverable's own. `dissolve_to_black` 142
frames (5.923 s) beginning at rel 2012 = SYNC 183745 = win-t 83.917, which is the finished
Taurus deliverable's own dissolve_start frame -- 0.52 s after the last note and 0.4 s after
the applause takes over, so the dissolve runs entirely over applause and never over music.
Audio fade 125 frames (5.213 s). The mix runs 4 frames past the picture: the deliverable's
0.167 s of black tail, to the frame.

Obstruction: gauntlet/recon/occlusion_ending.json adjudicated every flagged window in this
piece and found NO veto on either angle, so no shot here is placement-constrained.

Writes projects/mcp-tests-zinc/taurus-people-ending-r3.cut.json.
"""

from __future__ import annotations

import json
from pathlib import Path

FPS = 24000.0 / 1001.0
REC_IN = 181733
AUDIO_FRAMES = 2158  # 90.007 s of master mix, ending on SYNC 183891
PICTURE_FRAMES = 2154  # picture ends on SYNC 183887; the last 4 frames are the black tail
DISSOLVE_FRAMES = 142  # 5.923 s -- Taurus's own, so the dissolve starts on SYNC 183745
AUDIO_FADE_FRAMES = 125  # 5.213 s, landing just short of silence at the mix's end
MIX_ZERO, FX6_ZERO, A7_ZERO = 86401, 117576, 86306

CADENCE = 83.40  # the last note of the set
NEAR_GAP = 82.72  # the quietest 30 ms in the window, -40.41 dB
CODA_FROM = 76.0  # the round-3 brief's deceleration line

HERE = Path(__file__).resolve().parent
ONSETS = HERE / "end_p2_recon.json"
OUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending-r3.cut.json"

# FX6 reframes measured over this window (end_p2_recon.py). The A7IV is genuinely locked:
# moving_share 0.005, not one run.
FX6_MOVES = [
    (19.42, 19.75, 3.0),
    (24.75, 27.08, 15.0),
    (27.75, 28.75, 7.0),
    (37.25, 38.08, 6.0),
    (39.75, 40.08, 3.0),
    (40.42, 41.08, 5.0),
    (85.42, 86.42, -8.0),
    (89.25, 89.58, -3.0),
]
# Shot index (0-based) allowed to hold through a move. Shot 5 rides 24.75-28.75 on purpose;
# the final shot rides the 85.42 walk-back-right, which happens under the dissolve.
RIDES_MOVE = {4, 12}

# (intended cut second, angle the cut goes TO, motivation)
PLAN = [
    (
        6.59,
        "a7iv",
        "drum fill 6.59 (1.88 s, 81 hits), the first substantial fill of the piece - "
        "arrive on the near-field kit at the fill's START (sec 5), and the 9.57 fill lands "
        "inside the shot",
    ),
    (
        10.89,
        "fx6",
        "fill 10.89 - the highest-confidence fill in the window (0.630) - with a phrase "
        "boundary on it (score 1.03). Back to the whole quartet for the tune's loudest "
        "stretch: THIS framing has the drummer entire, so nothing is given up by leaving "
        "the kit angle",
    ),
    (
        20.01,
        "a7iv",
        "fill 20.01 (2.26 s) with a phrase boundary on the same frame (score 1.170, agrees "
        "with the +1.78 dB peak at 19.85) - arrive at the fill's start. The wide it leaves "
        "has just held, inside one shot, the loudest second in the entire 90 s (13.85, "
        "-9.38 LUFS), the 3.3 s / 126-hit fill at 15.63 whole, that fill's resolution at "
        "18.93, and the first half's strongest downbeat phrase (16.73, conf 0.817)",
    ),
    (
        23.91,
        "fx6",
        "energy peak 23.85 (+3.42 dB, the second-biggest prominence in the window) with a "
        "phrase boundary at 23.91. THE RIDE: the cut lands 0.84 s before the FX6 starts "
        "its 24.75-28.75 reframe, so the shot is settled when the camera moves and then "
        "WALKS LEFT off the drummer onto the piano - W1 becomes W2 on screen, no cut - "
        "under the window's biggest drum event (fill 25.01, 3.86 s, 118 hits). sec 4: the "
        "moving camera is a second editor. The shot then holds the quiet pocket "
        "(27.5-31.5, rms -17.6 to -19.2) whole, which is where a coverage pass bursts",
    ),
    (
        37.23,
        "a7iv",
        "the strongest downbeat phrase boundary of the middle of the piece (conf 0.803, "
        "score 1.153) - and it lands 0.02 s before the FX6 begins its second reframe run "
        "(37.25-41.08), so the piece leaves the wide at the instant the camera starts to "
        "move again and the move happens off screen. sec 4: wait out a pan, never cut into "
        "one",
    ),
    (
        43.07,
        "fx6",
        "fill 43.07 with a phrase boundary on it (score 1.207), 0.72 s after the +2.64 dB "
        "peak at 42.35 and 1.99 s after the camera settles. W3 arrives as a picture the "
        "piece has NOT shown: the pianist is now in frame at the left, and this is the "
        "framing that will carry the ending. sec 4: a scale change is a picture worth more "
        "than a framing return",
    ),
    (
        49.23,
        "a7iv",
        "drum fill 49.23 (1.68 s) - arrive at its start and ride it into the +2.99 dB peak "
        "at 49.85 (sec 5). The kit angle is the near-field picture of the drummer, which is "
        "what makes the 52.03 and 53.47 fills worth this shot",
    ),
    (
        53.77,
        "fx6",
        "phrase boundary 53.77 with the 53.47 fill (1.46 s) resolving into it - back to the "
        "band for the sag before the last build. The wide then holds 57.5-61.5, the "
        "thinnest run of the second half (rms -19.4 to -21.9), without a cut in it: sparse "
        "passages hold longer (sec 3)",
    ),
    (
        62.33,
        "a7iv",
        "THE STRONGEST PHRASE BOUNDARY IN THE WINDOW: conf 0.846, downbeat, 0.11 s rest, "
        "score 1.396. The kit takes the 0.461 s rest at 63.55 - the longest rest in the "
        "coda approach - and the +1.88 dB peak at 65.35, and it is already there when the "
        "66.41 fill starts",
    ),
    (
        66.97,
        "fx6",
        "SOLO CHANGE 66.97 (downbeat, timbre step inside the melodic stem) with its own "
        "phrase boundary and the 66.41 fill (2.22 s) under it. The front changes, so the "
        "angle that holds the band takes it (sec 4: follow the audience's gaze at a "
        "structure change). The last build runs inside this shot - onset density climbs "
        "6.0 -> 8.7/s - and so does the grid's LAST BEAT at 71.19, after which the pulse "
        "stops being measurable at all and the coda is free",
    ),
    (
        72.85,
        "a7iv",
        "THE LAST CLIMAX: energy peak +6.46 dB, the biggest-prominence event anywhere in "
        "the 90 s, with onset density peaking 9.7-10.3/s over the three seconds after it. "
        "The cut goes to the near-field kit because at this instant the drums are what is "
        "happening - and it is a VISIT, not a destination: 3.25 s, the shortest shot in the "
        "piece, the acceleration half of sec 3's accelerate-and-release pair. This is the "
        "last time the ending leaves the band",
    ),
    (
        76.08,
        "fx6",
        "phrase boundary 76.08 (conf 0.689, held ratio 3.0) at the TOP OF THE DECAY - rms "
        "-17.9 here, falling to -27.2 by 80.4 - and it is the LAST CUT IN THE PIECE. The "
        "release, and the return that the ending is built around: from here the widest "
        "framing the rig has (piano, bass, sax and drummer all in one frame) carries "
        "everything left. 7.80 s at full opacity, and they are working seconds - the decay "
        "steepening, the 80.35 trough, the last statement coming back at 80.85, the "
        "near-gap at 82.72 (the quietest 30 ms in the window, -40.41 dB), and the three-hit "
        "final figure at 82.96 / 83.17 / 83.40, the last of them the last note of the set "
        "(+15.66 dB lift, sample peak 1.106). Then applause, and the dissolve from 83.917. "
        "sec 5b: the cadence, the applause and the fade all belong to the ensemble - and "
        "sec 3: into a free coda the cutting decelerates, so the coda's two shots run 3.25 "
        "and 13.72 s and this one is the longest in the cut",
    ),
]

ROLE = {"fx6": ("fx6_wide", FX6_ZERO), "a7iv": ("a7iv_kit", A7_ZERO)}

OPEN_NOTE = (
    "open on the whole quartet in one frame. This is the FX6's first plateau (W1): the "
    "drummer is entire at the right and the pianist is outside the frame - the piece starts "
    "inside the tune's last full-energy stretch, so the establishing picture is the band"
)

TAIL_NOTE = (
    "tail: dissolve_to_black 142 frames (5.923 s) beginning at rel 2012 = SYNC 183745 = "
    "win-t 83.917, which is the finished deliverable's own dissolve_start frame; audio fade "
    "125 frames (5.213 s) ending with the mix. Picture ends 4 frames before the mix does, "
    "which is the deliverable's 0.167 s black tail. The FX6 walks back right 8 px at 85.42 "
    "under the dissolve, so the last image opens out toward the kit as it goes to black"
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
            # Band is 17-41 ms; a frame of margin is kept inside it so a millisecond of
            # disagreement with correlate's own clock cannot push a cut outside it.
            if not (0.019 <= abs(off) <= 0.038):
                continue
            # 100 ms (2.4 frames): a frame on the wanted side wins unless it is more than
            # two frames further from the intended moment.
            side_penalty = 0.0 if (off < 0) == prefer_early else 0.100
            score = abs(ct - t) + side_penalty
            if best is None or score < best[0]:
                best = (score, f, ct, off)
        if best is not None:
            return best[1], best[2], best[3]
    raise SystemExit(f"plan error: nothing in the 19-38 ms band within 0.6 s of {t:.2f}s")


def move_checks(bounds: list[int], angles: list[str]) -> list[str]:
    """Every FX6 shot: no start inside/next to a move, and only declared shots ride one."""
    problems = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        if angles[i] != "fx6":
            continue
        start, end = a / FPS, b / FPS
        for ms, me, dx in FX6_MOVES:
            if ms - 0.60 <= start <= me + 0.60:
                problems.append(
                    f"shot {i + 1} starts at {start:.2f}s, inside/next to reframe {ms}-{me}"
                )
            # A 3 px nudge across a 192 px width is 1.5% of frame and reads as nothing;
            # only a reframe big enough to be a new picture has to be declared.
            if abs(dx) < 5.0:
                continue
            if start < ms and me < end and i not in RIDES_MOVE:
                problems.append(
                    f"shot {i + 1} ({start:.2f}-{end:.2f}s) holds through reframe "
                    f"{ms}-{me} but is not declared as a ride"
                )
    return problems


def ending_checks(bounds: list[int], angles: list[str], cuts: list[dict]) -> list[str]:
    """The three things round 2 lost on, as gates the plan cannot pass without."""
    problems = []
    last_start = bounds[-2] / FPS
    # 1. the ensemble carries the ending: last shot is the wide, and it is in place well
    #    before the near-gap, the cadence and the dissolve.
    if angles[-1] != "fx6":
        problems.append(f"the last shot is {angles[-1]}, not the ensemble wide (sec 5b)")
    for label, moment in (
        ("near-gap", NEAR_GAP),
        ("cadence", CADENCE),
        ("dissolve start", (PICTURE_FRAMES - DISSOLVE_FRAMES) / FPS),
    ):
        if last_start > moment:
            problems.append(
                f"the final shot starts at {last_start:.2f}s, after the {label} "
                f"({moment:.2f}s) - the picture must already be where it finishes"
            )
    # 2. deceleration: at most one cut in [76, cadence], none after the cadence, and the
    #    final shot is the longest of the coda.
    late = [c["seconds"] for c in cuts if CODA_FROM <= c["seconds"] <= CADENCE]
    if len(late) > 1:
        problems.append(f"{len(late)} cuts between {CODA_FROM}s and the cadence: {late}")
    after = [c["seconds"] for c in cuts if c["seconds"] > CADENCE]
    if after:
        problems.append(f"cuts after the cadence: {after}")
    durs = [(b - a) / FPS for a, b in zip(bounds, bounds[1:], strict=False)]
    if durs[-1] < max(durs[-3:]):
        problems.append("the final shot is not the longest of the last three (sec 3 coda)")
    if durs[-1] != max(durs):
        problems.append("the final shot is not the longest shot in the piece")
    # 3. home-angle share inside the corpus band (sec 4: 68.5-76.3% over five timelines).
    share = sum(d for d, ang in zip(durs, angles, strict=False) if ang == "fx6") / sum(durs)
    if not (0.665 <= share <= 0.78):
        problems.append(f"home-angle share {share:.1%} is outside the corpus band")
    return problems


def main() -> None:
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_rel"]

    cuts = []
    for i, (t, angle, note) in enumerate(PLAN):
        prefer_early = i % 3 != 2
        f, ct, off = snap(t, onsets, prefer_early)
        cuts.append(
            {"frame": f, "seconds": ct, "offset_ms": off * 1000, "angle": angle, "note": note}
        )

    bounds = [0] + [c["frame"] for c in cuts] + [PICTURE_FRAMES]
    angles = ["fx6"] + [c["angle"] for c in cuts]
    notes = [OPEN_NOTE] + [c["note"] for c in cuts]

    # Hard constraints this round is built to satisfy.
    problems = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        secs = (b - a) / FPS
        if secs < 3.0:
            problems.append(f"shot {i + 1} is {secs:.2f}s - under the 3 s framing-return floor")
    if bounds[-1] - bounds[-2] <= DISSOLVE_FRAMES:
        problems.append(
            f"last shot is {bounds[-1] - bounds[-2]} frames, not longer than the "
            f"{DISSOLVE_FRAMES}-frame dissolve (E12)"
        )
    # The three hits of the final figure must be inside the last shot at full opacity.
    for hit in (82.96, 83.17, 83.40):
        if not (bounds[-2] / FPS <= hit < (PICTURE_FRAMES - DISSOLVE_FRAMES) / FPS):
            problems.append(f"final-figure hit {hit}s is not inside the last shot pre-dissolve")
    problems += move_checks(bounds, angles)
    problems += ending_checks(bounds, angles, cuts)
    if problems:
        for p in problems:
            print("PLAN ERROR:", p)
        raise SystemExit(1)

    segments = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        alias, zero = ROLE[angles[i]]
        note = notes[i]
        if i == len(bounds) - 2:
            note = f"{note}. {TAIL_NOTE}"
        segments.append(
            {
                "id": f"s{i + 1:02d}",
                "source": alias,
                "in": REC_IN + a - zero,
                "out": REC_IN + b - zero,
                "note": note,
            }
        )

    doc = {
        "schema": 1,
        "timeline": {"name": "Taurus People Ending P2 R3", "fps": 23.976},
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
            "out": REC_IN + AUDIO_FRAMES - MIX_ZERO,
        },
        "tail": {
            "type": "dissolve_to_black",
            "duration_frames": DISSOLVE_FRAMES,
            "audio_fade_frames": AUDIO_FADE_FRAMES,
        },
        "segments": segments,
    }
    OUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    picture = sum(s["out"] - s["in"] for s in segments)
    print(f"shots {len(segments)}  hard cuts {len(cuts)}  picture frames {picture} "
          f"(expected {PICTURE_FRAMES})  audio frames {AUDIO_FRAMES}")
    print(f"dissolve starts rel {PICTURE_FRAMES - DISSOLVE_FRAMES} = SYNC "
          f"{REC_IN + PICTURE_FRAMES - DISSOLVE_FRAMES} = win-t "
          f"{(PICTURE_FRAMES - DISSOLVE_FRAMES) / FPS:.3f}s")
    print(f"audio fade starts rel {AUDIO_FRAMES - AUDIO_FADE_FRAMES} = win-t "
          f"{(AUDIO_FRAMES - AUDIO_FADE_FRAMES) / FPS:.3f}s")
    print()
    print(f"{'id':5} {'angle':5} {'picture':9} {'start_s':>8} {'end_s':>8} {'dur_s':>6} "
          f"{'cut_off_ms':>10}")
    share = {"fx6": 0.0, "a7iv": 0.0}
    durs = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        off = cuts[i - 1]["offset_ms"] if i else 0.0
        start, end = a / FPS, b / FPS
        if angles[i] == "a7iv":
            pic = "T"
        elif end <= 24.0:
            pic = "W1"
        elif start < 28.75 < end:
            pic = "W1>W2"
        elif end <= 37.25:
            pic = "W2"
        else:
            pic = "W3"
        print(f"{segments[i]['id']:5} {angles[i]:5} {pic:9} {start:8.3f} {end:8.3f} "
              f"{end - start:6.2f} {off:+10.1f}")
        share[angles[i]] += end - start
        durs.append(round(end - start, 2))
    total = share["fx6"] + share["a7iv"]
    durs_sorted = sorted(durs)
    mid = len(durs) // 2
    median = (
        durs_sorted[mid] if len(durs) % 2 else (durs_sorted[mid - 1] + durs_sorted[mid]) / 2
    )
    print()
    print(f"fx6 {share['fx6']:.2f}s ({100 * share['fx6'] / total:.1f}%)  "
          f"a7iv {share['a7iv']:.2f}s ({100 * share['a7iv'] / total:.1f}%)")
    print(f"shot seconds {durs}")
    print(f"min {min(durs)}  median {median:.2f}  mean {sum(durs) / len(durs):.2f}  "
          f"max {max(durs)}  spread {max(durs) / min(durs):.1f}x")
    early = sum(1 for c in cuts if c["offset_ms"] < 0)
    print(f"transient offsets: {[round(c['offset_ms'], 1) for c in cuts]}")
    print(f"early {early} / late {len(cuts) - early}")
    coda = [(round(a / FPS, 2), round((b - a) / FPS, 2), angles[i])
            for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False))
            if b / FPS > 71.19]
    print(f"coda shots (after the last beat at 71.19): {coda}")
    print(f"last cut {cuts[-1]['seconds']:.2f}s -> ensemble; cadence {CADENCE}s, "
          f"near-gap {NEAR_GAP}s, dissolve {(PICTURE_FRAMES - DISSOLVE_FRAMES) / FPS:.2f}s "
          f"all inside it")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
