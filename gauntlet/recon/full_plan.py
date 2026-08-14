"""Author the WHOLE-SONG Taurus People cut from the three winning pieces + the two new spans.

The three pieces that won blind panels are the spine and their cuts are carried FRAME FOR FRAME:
the opening (taurus-people-opening.cut.json, R3), the mid trading window
(taurus-people-mid-p3r2.cut.json) and the ending with its tail device
(taurus-people-ending-r3.cut.json). Three shots are extended across a seam and nothing else in
them moves:

  * opening s15 (the release) runs past d90 to the first drum event of the breath,
  * mid s01 (the outgoing drum front) is entered EARLIER, on the +6.59 dB peak at d164.52,
  * mid s14 and ending s01 run past their window edges to the next real boundary.

Everything else here is the two unjudged spans: A (d90.00-166.68) and B (d256.68-407.66).

TIME BASE. d = deliverable seconds from the song head; mix_t = 3568.48 + d;
SYNC = 86401 + floor(mix_t * 23.976) -- FLOOR, not round, so a cut snapped to an onset lands
0-41.7 ms BEFORE it, which is the corpus's 17-41 ms "near, not on" band (styles/concert.md sec 1).
Clip frames: FX6 = SYNC - 117576, A7IV = SYNC - 86306, mix = SYNC - 86401.

SNAPPING. Every new cut moment is chosen from the ACCENT curve first (which peak, which phrase
boundary, which fill start) and only then snapped to the nearest transient in full_onsets.json --
the same onset list correlate_timeline computes for itself (sec 1: choose the moment from the
accent curve, then snap; snapping first reproduces the median while losing what it is evidence of).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROJ = HERE.parents[1] / "projects" / "mcp-tests-zinc"
OUT_CUT = PROJ / "taurus-people-full.cut.json"
OUT_PLAN = HERE / "full_plan.json"

FPS = 24000.0 / 1001.0
MIX_ZERO_FRAME = 86401
SONG_T0 = 3568.48
FX6_OFF = 117576
A7_OFF = 86306

ONSETS = json.loads((HERE / "full_onsets.json").read_text(encoding="utf-8"))["onsets_d"]

OPENING = json.loads((PROJ / "taurus-people-opening.cut.json").read_text(encoding="utf-8"))
MID = json.loads((PROJ / "taurus-people-mid-p3r2.cut.json").read_text(encoding="utf-8"))
ENDING = json.loads((PROJ / "taurus-people-ending-r3.cut.json").read_text(encoding="utf-8"))

SNAP_WINDOW = 0.35
"""How far a chosen accent may be from a transient before the cut is left unsnapped."""


def sync_at(d: float) -> int:
    return MIX_ZERO_FRAME + math.floor((SONG_T0 + d) * FPS)


def snap(d: float) -> tuple[int, float, float]:
    """(SYNC frame, snapped d, ms the cut sits BEFORE the transient)."""
    near = min(ONSETS, key=lambda o: abs(o - d))
    use = near if abs(near - d) <= SNAP_WINDOW else d
    frame = sync_at(use)
    lands = (frame - MIX_ZERO_FRAME) / FPS - SONG_T0
    return frame, use, 1000.0 * (use - lands)


def fx6(frame: int) -> int:
    return frame - FX6_OFF


def a7(frame: int) -> int:
    return frame - A7_OFF


# --------------------------------------------------------------------------------------
# The two new spans. (d_out, angle, note) -- each shot runs from the previous d_out.
# d_out is the ACCENT chosen for the cut; snapping happens above.
# --------------------------------------------------------------------------------------

SEAM_A_OUT = 96.20
"""Where opening-R3's release shot now ends. See the o15 note."""

SPAN_A: list[tuple[float, str, str]] = [
    (109.02, "a7iv_kit",
     "12.82 s, LONG. THE BREATH. Fill 96.20 (0.589) in; fills 97.86, 101.14, 102.24, 103.34, "
     "105.80, 106.62 and 108.28 all pass on the near-field kit without pulling a cut - sec 5: "
     "the detector finds a subset of the motivations, not a cue list, and a 10-15 hit fill in "
     "the quietest sustained music of the song is not an event. Out ON the breath's biggest "
     "swell (109.02, +3.99 dB - sec 5: read prominence against the passage, not the song). "
     "RECON: full_motion.json measures moving_share 0.000 on BOTH angles across d88-170 - the "
     "operated camera never moves once in 82 seconds, so there is no reframe to ride and no "
     "third picture to be had; the arc here is carried by rate and hold length alone."),
    (111.64, "fx6_wide",
     "2.62 s, THE BREATH'S ONE BURST and the shortest shot in the first half. Cut ON the "
     "breath's loudest instant, to the soloist - the FX6 here is a piano-and-bass two-shot "
     "(full_sheets/sheet_A_FX6.jpg), no drummer and no sax, which is the soloist picture for a "
     "bass solo (sec 4). Fill 109.96 inside. Sec 3: a 2-4 s shot on a picture the run has not "
     "just been on (12.82 s away) is corpus practice, and it is what stops four holds in a row "
     "from reading as one bin."),
    (121.84, "a7iv_kit",
     "10.20 s, LONG. Fill 111.64 in; peak 117.02 and THE BREATH'S BIGGEST FILL (119.36, 1.66 s, "
     "68 hits) play inside. Sec 3's ceiling test asks what develops past ~10 s: the drummer "
     "does - this shot spans the whole -24.70 -> -21.93 leg and the fill that tops it."),
    (142.52, "fx6_wide",
     "20.68 s, THE LONGEST HOLD IN THE SONG AND THE POINT OF THE PASSAGE. Fill 121.84 in. Peak "
     "124.52, fills 128.46 and 129.28, peak 129.52, fills 132.86, 135.90, 137.54, 138.38 and "
     "140.60 ALL pass with no cut. This is the song breathing: sec 3 says a sparse stretch may "
     "hold far past the medians and that the answer to thin material is fewer cuts, never the "
     "same rate carried over. RECEIPT: the human deliverable holds 20.14 s across almost exactly "
     "these seconds - his 121.37-141.52 shot (human_cuts_taurus.json), the second-longest in his "
     "whole cut. Out ON peak 142.52 (+2.68)."),
    (149.38, "a7iv_kit",
     "6.86 s. Cut ON the swell. Peak 147.52 (+2.39) and fills 145.30, 148.56 and 149.38 inside. "
     "THE QUICKENING STARTS HERE: 20.68 -> 6.86 -> 4.14 -> 3.36, and the acceleration answers a "
     "real rise - the smoothed curve climbs +4.90 dB from d151.02 to d165.02 (arc_legs), which "
     "is sec 3's condition for tightening at all."),
    (153.52, "fx6_wide",
     "4.14 s. Fill 149.38 in. The bass solo's last phrase, on the soloist, and the shot ENDS ON "
     "THE FRONT CHANGE - solo_change bass->drums at 153.52, lead signal, downbeat, the "
     "highest-scoring event in the whole span (score 1.150)."),
    (156.88, "a7iv_kit",
     "3.36 s, the shortest shot of the quickening. The kit takes the front ON the change (sec 4: "
     "follow the audience's gaze at a structure change) - and it has to, because the FX6's "
     "framing here holds no drummer at all, so the drum feature is visible on this angle only. "
     "Peak 154.02 and fill 155.46 inside."),
    (164.52, "fx6_wide",
     "7.64 s. Fill 156.88 in. THE HOLD THAT DECLINES TO DESCEND: the run has gone 6.86 / 4.14 / "
     "3.36 and instead of a fourth shorter step it steps back UP, which is the shape sec 3 asks "
     "for after a 3/3 panel called a smooth ladder 'an editor's metronome'. The piano and bass "
     "answer the new front; fill 162.78 (1.42 s, 62 hits) plays here on purpose rather than "
     "pulling a cut."),
]

SPAN_B: list[tuple[float, str, str]] = [
    # ---- LEG 8+9, d256.68-328.02: THE PLATEAU AND THE LONG BUILD. Fewest cuts of the
    # whole sax solo, longest shots: the climb has to have somewhere to accelerate from.
    (266.44, "a7iv_kit",
     "5.32 s. Phrase 261.12 (conf 0.737, downbeat) in. THE 3.66 s / 115-HIT FILL AT 261.94 - the "
     "biggest drum event in the first third of the span - starts 0.82 s inside the shot and "
     "rides it whole (sec 5: arrive around the start of the fill, ride it). Peak 262.52 (+3.06) "
     "inside. The A7IV in span B is not a kit close-up: the sax player stands at frame left "
     "PLAYING with the drummer in the near field and the bassist behind "
     "(occl_full_frames/sheet_ba7_a.jpg), so this angle holds the soloist too (sec 4: an angle's "
     "role is what its framing holds, not its name)."),
    (278.92, "fx6_wide",
     "12.48 s, LONG. Phrase 266.44 (0.657, downbeat) with fill 267.00 in. The FX6 is settled "
     "from 260.42 and holds the whole quartet in one frame. Peak 276.02 (+3.32, the leg's "
     "biggest swell) plays INSIDE the shot, and so do fills 271.78 and 275.60 and phrases "
     "272.88, 276.72 and 277.54: the plateau is one arc, not a cue list, and this is the first "
     "of the three long shots that make the fast zone at d328 legible when it arrives."),
    (287.50, "a7iv_kit",
     "8.58 s, LONG. Phrase 278.92 (0.765, downbeat) in. Fill 282.80 and peak 285.02 (+2.88) "
     "inside. Out on phrase 287.50 - conf 0.834, rest 0.193, held 8.79, the strongest boundary "
     "of the leg."),
    (302.00, "fx6_wide",
     "14.50 s, THE LONGEST SHOT OF THE SAX SOLO. Phrase 287.50 in. The 2.78 s / 96-hit fill at "
     "288.62, phrase 291.94 (conf 0.845), THE START OF THE SONG'S LONGEST BUILD at d294.52 "
     "(+2.98 dB over 33.5 s, arc_legs), phrase 295.00 and peak 301.52 all play inside ONE shot. "
     "Where a long climb begins, the cutting stops. RECEIPT: the human deliverable holds 21.02 s "
     "and 13.10 s across these same seconds (252.38-273.40 and 286.16-299.26) and does not cut "
     "at all between 303.47 and 320.86 - his three longest shots of the second half are all in "
     "this stretch (human_cuts_taurus.json)."),
    (307.00, "a7iv_kit",
     "5.00 s. Phrase 302.00 (0.825, downbeat) in; phrases 303.68 and 305.36 (held 6.0) inside. "
     "Out ON peak 307.02 (+3.04 dB) with phrase 307.00 on the same frame."),
    (316.02, "fx6_wide",
     "9.02 s. Cut ON the peak - and THE RIDE. full_motion.json puts the FX6's biggest reframe of "
     "the whole song at 308.42-313.08 (+28 px left, 4.67 s): the shot is settled 1.42 s before "
     "it starts, and the camera then walks the PIANIST into frame - the quartet wide becomes a "
     "piano/bass/sax three-shot ON SCREEN with no cut spent on it (sec 4: the moving camera is a "
     "second editor). Fill 313.06 and phrases 311.96, 312.52 and 313.62 play under the move; out "
     "ON d316.02, which carries 11.33 onsets/second - the densest drumming anywhere in the song."),
    (322.48, "a7iv_kit",
     "6.46 s. Cut on the density peak, onto the near-field kit, because at this instant the "
     "drumming IS what is happening. Phrases 316.38 and 316.92, fills 317.52 and 320.52 and the "
     "1.66 s / 70-hit fill at 321.10 all inside; out on phrase 322.48 (0.754, rest 0.29)."),
    (328.02, "fx6_wide",
     "5.54 s. Phrase 322.48 in; the build's last five seconds on the band picture."),

    # ---- LEG 10, d328.02-381.02: THE FAST ZONE. The song's densest cutting, on its most
    # event-dense music (30+ phrase boundaries above conf 0.60 in 46 seconds, onsets 8.3-9.2/s).
    (335.18, "a7iv_kit",
     "7.16 s. CUT ON THE SUMMIT of the song's longest build - energy peak 328.02, +4.01 dB "
     "prominence, -9.24 LUFS, the top of a 33.5 s climb. The summit is taken TIGHT, on the "
     "sax-and-drummer two-shot: that is a different gesture from the song's peak at d233.5 "
     "(revealed by a camera move, no cut) and from its second summit at d400 (a cut ON the peak "
     "onto the widest picture there is). The -2.53 dB fall that follows is held through inside "
     "this shot, and the fast zone starts on the far side of it."),
    (337.14, "fx6_wide",
     "1.96 s, A BURST. Phrase 335.18 (rest 0.323) in; peak 337.02 lands on the out frame with "
     "phrase 337.14 (0.796) on it. Two seconds of the band, and the rate has changed: from here "
     "to d381 the cut runs one shot per 3.8 s against one per 8.6 s across the plateau behind "
     "it. RECEIPT: the human deliverable does the same thing in the same place - after holding "
     "17.39 s to 320.86 he cuts at 322.49 and 324.20, two shots of 1.63 and 1.71 s, and then "
     "stays between 2.3 and 5.3 s all the way to 373.08."),
    (340.72, "a7iv_kit",
     "3.58 s. Phrase 337.14 in; the 1.12 s / 43-hit fill at 339.60 inside."),
    (344.52, "fx6_wide",
     "3.80 s. Phrase 340.72 (0.836, downbeat, rest 0.433) in; out ON the sag's own swell "
     "(344.52, +2.33)."),
    (347.66, "a7iv_kit",
     "3.14 s. Cut ON the swell; fills 345.72 and 346.82 inside."),
    (350.12, "fx6_wide",
     "2.46 s. Fill 347.66 in; phrase 349.30 (0.813, rest 0.073) inside."),
    (354.50, "a7iv_kit",
     "4.38 s. Phrase 350.12 (0.780) in; phrases 351.24, 352.06, 352.88 and 353.94 and fill "
     "352.60 all inside."),
    (356.18, "fx6_wide",
     "1.68 s, THE SHORTEST SHOT IN THE SONG. Cut ON peak 354.52 (+3.29, -9.78 LUFS) with phrase "
     "354.50 (0.802) on the same frame. The 1.38 s / 64-hit fill at 355.34 and phrase 355.62 "
     "(0.830, rest 0.453) both play inside 1.7 seconds of the band - a glance, not a shot, and "
     "the fast zone's second burst."),
    (360.02, "a7iv_kit",
     "3.84 s. Phrase 356.18 (0.735) in; phrases 357.00, 358.40 (held 8.4) and 359.22 inside."),
    (366.12, "fx6_wide",
     "6.10 s. CUT ON THE CRASH: peak 360.02 is -9.49 LUFS at only 6.33 onsets/second - a struck "
     "accent rather than a flurry, and the top of the d347.5-360.02 climb - so it is caught on "
     "the frame instead of arrived at late. THE SECOND RIDE: the FX6 walks right twice under the "
     "hold (361.75-363.58 and 364.25-365.75, -18 px total) and the DRUMMER comes back into the "
     "wide with no cut on it; the shot leaves 0.37 s after the move lands. This is the one shot "
     "in the fast zone that is allowed to be long, and the camera is why."),
    (369.50, "a7iv_kit",
     "3.38 s. Phrase 366.12 in."),
    (373.08, "fx6_wide",
     "3.58 s. Phrase 369.50 in - and the shot LEAVES the wide at the instant the camera starts "
     "its last walk right (373.08-377.58), so the move happens off screen (sec 4: wait out a "
     "pan, never cut into one; the ending piece's own s06 is the same move)."),
    (377.78, "a7iv_kit",
     "4.70 s. THE BIGGEST FILL IN SPAN B (375.80, 3.64 s, 118 hits) and the span's STRONGEST "
     "PHRASE BOUNDARY (377.24, conf 0.874, downbeat, rest 0.413) both play here, on the "
     "near-field kit, which is where a 118-hit fill belongs. Peak 374.52 - another low-density "
     "struck accent - lands 1.44 s in."),
    (381.02, "fx6_wide",
     "3.24 s. The wide comes back 0.20 s after its reframe settles, and it is a NEW PICTURE: the "
     "walk that ran off screen has opened it back onto the full quartet with the drummer in "
     "frame, which is the framing that carries the rest of the song. Out ON peak 381.02 - "
     "+5.04 dB, the biggest prominence anywhere in the span."),

    # ---- LEG 11, d381.02-400.04: GESTURE 2. After 53 s of the song's fastest cutting, the
    # cutting STOPS for the climb, and the cut lands on the summit.
    (387.74, "a7iv_kit",
     "6.72 s. Cut ON the +5.04 dB peak, to the near-field kit - and then the song's last real "
     "dip (-3.58 dB into d386) is HELD through: fill 382.16 and phrases 383.84 and 384.96 pass "
     "with no cut, because nothing is arriving and sec 3 says holding beats shrinking over "
     "decaying audio. The fast zone is over, and this is the shot that says so."),
    (391.12, "fx6_wide",
     "3.38 s. Phrase 387.74 in: the +3.83 dB climb to the song's second-loudest instant has "
     "started, and this is the last short shot before it."),
    (400.04, "a7iv_kit",
     "8.92 s. THE CUTTING STOPS. Phrase 391.12 (rest 0.251) in, and then the whole top of the "
     "climb plays in one shot on the sax-and-drummer two-shot: fills 392.22, 394.50 and 397.42 "
     "and phrases 391.92, 395.76 (0.815), 397.42, 397.96 (conf 0.854, downbeat - the span's "
     "second-strongest) and 399.04 (0.802) all pass without a cut. Against a local mean of 3.8 s "
     "this shot is two and a half times the rate around it, so the deceleration is the thing the "
     "viewer feels arriving. RECEIPT: the human deliverable holds 13.76 s across 385.34-399.11 "
     "and cuts 0.93 s before the peak; this cut lands on it."),
]


def build() -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = []

    # ---- opening, verbatim except s15's out ------------------------------------------
    cut_a_open, snap_a_open, ms_a_open = snap(SEAM_A_OUT)
    # Ids share one namespace across the whole file and all three pieces number from s01, so
    # every carried segment is re-prefixed by the piece it came from: o / m / e.
    for seg in OPENING["segments"]:
        one = dict(seg)
        one["id"] = "o" + seg["id"].lstrip("sg")
        if one["id"] == "o15":
            one["out"] = fx6(cut_a_open)
            one["note"] = (
                "13.66 s, THE RELEASE - opening R3's s15, EXTENDED across the piece boundary. In "
                "the 90 s piece it stopped at the window edge after 7.47 s; in the song it runs "
                "on through the head of the breath to fill 96.20, with fill 91.16 (0.547, 29 "
                "hits) and peak 95.52 (+2.95) inside it. The release after the opening's "
                "5.34/4.92/4.12/3.64/3.00 tightening run is therefore a shot rather than a "
                "deadline, and it is the first of the two long holds that make d90-142 read as "
                "the song exhaling. RECEIPT: the human deliverable runs a single 18.60 s shot "
                "across 75.70-94.30, straight through the same boundary. Original note: "
                + str(seg.get("note", ""))
            )
        segments.append(one)

    # ---- SPAN A ----------------------------------------------------------------------
    prev = cut_a_open
    for i, (d_out, angle, note) in enumerate(SPAN_A, start=1):
        frame, used, ms = snap(d_out)
        conv = fx6 if angle == "fx6_wide" else a7
        segments.append(
            {
                "id": f"a{i:02d}",
                "source": angle,
                "in": conv(prev),
                "out": conv(frame),
                "note": note,
            }
        )
        plan.append(
            {
                "id": f"a{i:02d}",
                "angle": angle,
                "d_in": round((prev - MIX_ZERO_FRAME) / FPS - SONG_T0, 3),
                "d_out": round((frame - MIX_ZERO_FRAME) / FPS - SONG_T0, 3),
                "seconds": round((frame - prev) / FPS, 2),
                "target": d_out,
                "snapped_to_onset": round(used, 4),
                "ms_before_transient": round(ms, 1),
            }
        )
        prev = frame

    # ---- mid s01 entered early, then s02..s13 verbatim, s14 extended -----------------
    mid_by_id = {s["id"]: s for s in MID["segments"]}
    s01 = dict(mid_by_id["s01"])
    s01["id"] = "m01"
    s01["in"] = a7(prev)
    s01["note"] = (
        "4.19 s. CUT ON THE +6.59 dB PEAK at d164.52 - the biggest single prominence between the "
        "head and the return, and only 5.33 onsets/second, so it is a struck band accent and it "
        "is caught ON the frame rather than 4.8 s late. This is mid-P3R2's s01 entered EARLIER: "
        "in the 90 s piece it began cold at the window edge as a 2.04 s shot; in the song it "
        "arrives on the peak and the drums' closing statement (fills 165.34, 167.56 and 168.70) "
        "finishes inside it. Original note: " + str(mid_by_id["s01"].get("note", ""))
    )
    segments.append(s01)
    for sid in ("s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09", "s10", "s11", "s12",
                "s13"):
        segments.append(dict(mid_by_id[sid], id="m" + sid[1:]))

    cut_b_open, snap_b_open, ms_b_open = snap(261.12)
    s14 = dict(mid_by_id["s14"])
    s14["id"] = "m14"
    s14["out"] = fx6(cut_b_open)
    s14["note"] = (
        "10.27 s, LONG - mid-P3R2's s14, EXTENDED across the piece boundary. The four-player wide "
        "now also carries the plateau's opening peak (256.02, +4.17 dB at 10.0 onsets/second) and "
        "the 2.76 s / 96-hit fill at 256.12, and it RIDES the FX6's 258.08-260.42 reframe so the "
        "picture settles inside the hold with no cut in the move. Out on phrase 261.12 (conf "
        "0.737, downbeat), 0.70 s after the camera lands. Original note: "
        + str(mid_by_id["s14"].get("note", ""))
    )
    segments.append(s14)

    # ---- SPAN B ----------------------------------------------------------------------
    prev = cut_b_open
    for i, (d_out, angle, note) in enumerate(SPAN_B, start=1):
        frame, used, ms = snap(d_out)
        conv = fx6 if angle == "fx6_wide" else a7
        segments.append(
            {
                "id": f"b{i:02d}",
                "source": angle,
                "in": conv(prev),
                "out": conv(frame),
                "note": note,
            }
        )
        plan.append(
            {
                "id": f"b{i:02d}",
                "angle": angle,
                "d_in": round((prev - MIX_ZERO_FRAME) / FPS - SONG_T0, 3),
                "d_out": round((frame - MIX_ZERO_FRAME) / FPS - SONG_T0, 3),
                "seconds": round((frame - prev) / FPS, 2),
                "target": d_out,
                "snapped_to_onset": round(used, 4),
                "ms_before_transient": round(ms, 1),
            }
        )
        prev = frame

    # ---- ending: s01 entered early (GESTURE 2's release), s02..s13 + tail verbatim ----
    end_by_id = {s["id"]: s for s in ENDING["segments"]}
    e01 = dict(end_by_id["s01"])
    e01["id"] = "e01"
    e01["in"] = fx6(prev)
    e01["note"] = (
        "14.35 s, THE LONGEST PICTURE IN THE SONG OUTSIDE THE CODA. GESTURE 2 LANDS: the cut sits "
        "ON energy peak 400.02 (-8.78 LUFS, the song's second-loudest instant after the return at "
        "d233.5) with phrase 400.04 (downbeat) and a fill on the same frame, and it lands on the "
        "WIDEST picture the rig has - the whole quartet - which is the opposite of the tight "
        "landing the d328 summit got and of the no-cut camera reveal the d233 return got. Then "
        "the release: the energy falls -2.17 dB over the next seventeen seconds and this one shot "
        "carries all of it. This is ending-R3's s01 entered earlier; its own note follows. "
        + str(end_by_id["s01"].get("note", ""))
    )
    segments.append(e01)
    for sid in ("s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09", "s10", "s11", "s12",
                "s13"):
        segments.append(dict(end_by_id[sid], id="e" + sid[1:]))

    doc = {
        "schema": 1,
        "timeline": {"name": "Taurus People Full P4 R1", "fps": 23.976},
        "sources": OPENING["sources"],
        "audio": {"source": "master_mix", "in": 85558, "out": 97490},
        "tail": ENDING["tail"],
        "segments": segments,
    }
    return {"cut": doc, "plan": plan}


def main() -> None:
    made = build()
    doc = made["cut"]

    total = 0
    for seg in doc["segments"]:
        total += int(seg["gap"]) if "gap" in seg else int(seg["out"]) - int(seg["in"])
    want = doc["audio"]["out"] - doc["audio"]["in"]
    print(f"segments {len(doc['segments'])}  frames {total}  audio {want}  "
          f"delta {total - want}", flush=True)
    # The picture is 4 frames SHORT of the mix on purpose: that is ending-R3's own tail, and
    # the deliverable's own 0.167 s of black after the dissolve lands (styles/concert.md 5b).
    assert total == want - 4, "picture must end 4 frames before the mix, as ending-R3 does"

    # role share + shot histogram over the WHOLE song
    shots = [
        (s["source"], (int(s["out"]) - int(s["in"])) / FPS)
        for s in doc["segments"]
        if "gap" not in s
    ]
    by = {}
    for src, sec in shots:
        by[src] = by.get(src, 0.0) + sec
    picture = sum(by.values())
    print("SHOTS", len(shots), "picture_seconds", round(picture, 2))
    for src, sec in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  {src:10} {sec:8.2f} s  {100 * sec / picture:5.1f}%")
    lens = sorted(sec for _, sec in shots)
    mid_i = len(lens) // 2
    print(f"  median {lens[mid_i]:.2f}  mean {picture / len(lens):.2f}  "
          f"min {lens[0]:.2f}  max {lens[-1]:.2f}")
    bins = {"<3": 0, "3-4": 0, "4-6": 0, "6-8": 0, "8-11": 0, ">11": 0}
    for one in lens:
        key = ("<3" if one < 3 else "3-4" if one < 4 else "4-6" if one < 6
               else "6-8" if one < 8 else "8-11" if one < 11 else ">11")
        bins[key] += 1
    print("  bins", bins)

    offs = [abs(p["ms_before_transient"]) for p in made["plan"]]
    print(f"  new cuts {len(offs)}  transient offset median "
          f"{sorted(offs)[len(offs) // 2]:.1f} ms  max {max(offs):.1f} ms")

    OUT_CUT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    OUT_PLAN.write_text(json.dumps({"plan": made["plan"]}, indent=1), encoding="utf-8")
    print("wrote", OUT_CUT, flush=True)
    for p in made["plan"]:
        print(f"  {p['id']} {p['angle']:10} d{p['d_in']:8.2f}->{p['d_out']:8.2f} "
              f"{p['seconds']:6.2f}s  snap {p['ms_before_transient']:+6.1f} ms", flush=True)


if __name__ == "__main__":
    main()
