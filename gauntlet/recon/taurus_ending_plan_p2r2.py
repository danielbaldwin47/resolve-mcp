"""Author the piece-2 ROUND-2 cut file: the ENDING of Taurus People (last 90 s of the set).

Window: mix 3976.15-4066.15 s = Zinc SYNC 181733-183891 = 2158 frames at 23.976.
Arithmetic is round 1's, unchanged (mix zero 86401, FX6 zero 117576, A7IV zero 86306);
cuts snap against the same onset list correlate_timeline computes for itself
(gauntlet/recon/end_p2_recon.json) so the plan's offsets and the self-review's are one
number.

WHAT ROUND 1 LOST ON (3/3 panel), and what changed here
-------------------------------------------------------
1. "The tail was inexpressible."  v1 could only hard-cut to a gap, so the piece ended by
   stopping. The schema now has the `tail` device (get_cut_schema sec 8), so this round
   ends the way the five deliverables end: `dissolve_to_black` 142 frames (5.923 s) with a
   125-frame (5.213 s) audio fade under it. The numbers are Taurus's own, measured off the
   finished deliverable (gauntlet/recon/taurus_ending_events.json -> deliverable_tail):
   picture runs to rel 2154 (SYNC 183887), the dissolve reaches back 142 frames from there
   so it BEGINS at rel 2012 = SYNC 183745 = win-t 83.905 -- the deliverable's own
   dissolve_start frame, to the frame -- 0.52 s after the last note of the set (83.40) and
   0.4 s after the applause takes over (83.50). The dissolve therefore runs entirely over
   applause and never over music. The mix runs 4 frames past the picture, which is the
   deliverable's 0.167 s of black tail, also to the frame.
2. "13 near-metronomic ~6 s cuts ping-ponging two framings."  Two fixes, both measured:
   * THREE FX6 pictures, not one. end_p2_recon.py measured the FX6's reframes; the camera
     walks left 39 px of a 192-px width (20% of frame) in two clusters, 24.75-28.75
     (+22 px) and 37.25-41.08 (+14 px), and is otherwise locked. So it has three plateaus,
     and they are different pictures, checked on grabs:
       W1 (to 19.4)     sax centre, bass left, DRUMMER WHOLE at right, pianist out of frame
       W2 (28.75-37.25) piano large at left, pianist's shoulder in, drummer cropped
       W3 (41.08-85.4)  PIANIST FULLY IN at left, drummer gone from the frame
     With the A7IV's tight four-shot that is four pictures, and the FX6 is never "returned
     to": each return is a picture the piece has not shown.
   * The bigger reframe is RIDDEN, not dodged. Round 1 kept every move off screen. Shot 5
     cuts in 0.84 s before the 24.75 move starts and holds through it, so W1 becomes W2 on
     screen with no cut, under the window's biggest drum event -- styles/concert.md sec 4,
     "the moving camera is a second editor". The second cluster is the one kept off screen,
     so W3 arrives at 43.07 as a picture the viewer has not seen.
   * Shot lengths are spread on purpose: 3.2 to 13.3 s, mean above median, the corpus's own
     skew (sec 3). No two adjacent shots are within a second of each other except once.
3. Ladder constraints (sec 3, the unanimous 3/3 panel). There is no tightening ladder in
   this cut. The only acceleration is into the last climax, and it is not monotonic. Over
   the decay (75.5-82.7, rms -16 falling to -34) the shots GROW: 3.23 -> 6.72 -> 7.03.
   No shot is under 3 s, so no framing is returned to for a blink.
4. The human's known flanks, attacked directly:
   * their tail parks 21.3 s on one master framing. Ours puts three cuts inside the free
     coda (71.2-82.7): the climax at 72.85, the top of the decay at 76.08, and the near-gap
     at 82.72 -- the quietest 30 ms in the window (-40.41 dB) -- where the picture changes
     in the silence and the three-hit final figure lands on the new one.
   * their 28-34 s burst fires in the quietest pocket. The measured pocket is 27.5-31.5
     (rms -17.6 to -19.2, gauntlet/recon/r2_energy.scratch.log). Ours holds one developing
     shot across it -- zero cuts between 23.91 and 37.23.

Obstruction: gauntlet/recon/occlusion_ending.json adjudicated every flagged window in this
piece and found NO veto on either angle, so no shot here is placement-constrained.

Writes projects/mcp-tests-zinc/taurus-people-ending.cut.json.
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

HERE = Path(__file__).resolve().parent
ONSETS = HERE / "end_p2_recon.json"
OUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending.cut.json"

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
# Shot index (0-based) allowed to hold through a move: shot 5 rides 24.75-28.75 on purpose.
RIDES_MOVE = {4}

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
        "piece has NOT shown: the pianist is now fully in frame at the left and the drummer "
        "is out of it altogether. sec 4: a scale change is a picture worth more than a "
        "framing return",
    ),
    (
        49.23,
        "a7iv",
        "drum fill 49.23 (1.68 s) - arrive at its start and ride it into the +2.99 dB peak "
        "at 49.85 (sec 5). From here the A7IV is the ONLY angle with the drummer in it, "
        "which is what makes the 52.03 and 53.47 fills worth this shot",
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
        "stops being measurable at all",
    ),
    (
        72.85,
        "a7iv",
        "THE LAST CLIMAX: energy peak +6.46 dB, the biggest-prominence event anywhere in "
        "the 90 s, with onset density peaking 9.7-10.3/s over the three seconds after it. "
        "The cut goes to the only picture with the drummer in the near field, because at "
        "this instant the drums are what is happening",
    ),
    (
        76.08,
        "fx6",
        "phrase boundary 76.08 at the TOP OF THE DECAY (rms -17.4 here, falling to -34.2 by "
        "82.5). RELEASE: the accelerate-and-release pair (sec 3) - the climax was the "
        "acceleration, this is the hold after it. Over decaying audio the 3/3 panel says "
        "hold rather than shrink, so this shot GROWS on the one before it and swallows the "
        "diminuendo steps at 80.0, 81.5 and 82.5 rather than cutting on them",
    ),
    (
        82.80,
        "a7iv",
        "THE NEAR-GAP: 82.72 is the quietest 30 ms in the window (-40.41 dB, 34 dB under "
        "the last note). The picture changes IN THE SILENCE and the three-hit final figure "
        "- 82.96 / 83.17 / 83.40, the last of them the last note of the set (+15.66 dB "
        "lift, sample peak 1.106) - lands on the tightest picture the piece has, with the "
        "drummer's stick up in the near field. The band is on screen and playing for the "
        "final punch; the dissolve does not start until 83.905, 0.52 s later and 0.4 s "
        "after the applause has taken over",
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
    "win-t 83.905, which is the finished deliverable's own dissolve_start frame; audio fade "
    "125 frames (5.213 s) ending with the mix. Picture ends 4 frames before the mix does, "
    "which is the deliverable's 0.167 s black tail"
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
            # two frames further from the intended moment. Round 1 used 50 ms and came back
            # 9 late / 4 early -- the opposite of the 2:1 EARLY lean the director's own
            # recut shows (sec 6), and the same failure round 3 had at 20 ms.
            side_penalty = 0.0 if (off < 0) == prefer_early else 0.100
            score = abs(ct - t) + side_penalty
            if best is None or score < best[0]:
                best = (score, f, ct, off)
        if best is not None:
            return best[1], best[2], best[3]
    raise SystemExit(f"plan error: nothing in the 19-38 ms band within 0.6 s of {t:.2f}s")


def move_checks(bounds: list[int], angles: list[str]) -> list[str]:
    """Every FX6 shot: no start inside/next to a move, and only shot 5 holds through one."""
    problems = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
        if angles[i] != "fx6":
            continue
        start, end = a / FPS, b / FPS
        for ms, me, dx in FX6_MOVES:
            if ms - 0.60 <= start <= me + 0.60:
                problems.append(
                    f"shot {i + 1} starts at {start:.2f}s, inside/next to reframe "
                    f"{ms}-{me}"
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


def main() -> None:
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_rel"]

    cuts = []
    for i, (t, angle, note) in enumerate(PLAN):
        # ~2:1 early, the lean the director's own recut shows (sec 6). The last cut is
        # early on purpose: the picture must change BEFORE the final figure is struck.
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
        "timeline": {"name": "Taurus People Ending P2", "fps": 23.976},
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
    print("wrote", OUT)


if __name__ == "__main__":
    main()
