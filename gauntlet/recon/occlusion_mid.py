"""Adjudicate the mid-window occlusion scan and write occlusion_mid.json. READ-ONLY.

The verdicts below were reached by eye on the frames occl_mid_grabs*.py pulled; this file holds
them as data and does the time-base arithmetic, so every claim in the output carries its mix
second, its Zinc-SYNC frame, its deliverable second and its clip frame without anyone doing the
conversions by hand.

The ledger's test for a true blocking is that it MOVES — a frame either side of a flag separates
a body crossing the near field from furniture that is in every frame of the take. Two of this
window's results change what the ledger says the SCORE is worth:

  * The A7IV flag at t=63.98 peaked at 0.416 and is REAL: the sax player walks right-to-left
    through the near field and at 63.98 his out-of-focus back covers the kit and the bassist.
    The two drummer false positives in the same window peaked HIGHER (0.469, 0.472). On this
    angle the score does not separate true from false — only the frames do.
  * The FX6's single 42 s flag is a false positive with a signature the ledger has not carried
    before: the operator reframes at t~41.5, and every sample before the reframe scores
    0.48-0.69 while every sample after scores 0.000, with no obstruction in either half.

Frame arithmetic: SYNC = 175955 + round(t * 23.976), A7IV clip = SYNC - 86306,
FX6 clip = SYNC - 117576, mix = 3735.16 + t, deliverable = 166.68 + t.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
OUT = RECON / "occlusion_mid.json"
SCAN = RECON / "occl_mid_scan.json"

FPS = 24000.0 / 1001.0
SYNC_IN = 175955
MIX_IN = 3735.16
DELIV_IN = 166.68
A7_OFFSET = 86306
FX6_OFFSET = 117576
THRESHOLD = 0.35


def at(t: float, angle: str | None = None) -> dict[str, Any]:
    sync = SYNC_IN + round(t * FPS)
    out: dict[str, Any] = {
        "window_t": round(t, 2),
        "mix_t": round(MIX_IN + t, 2),
        "deliverable_t": round(DELIV_IN + t, 2),
        "sync_frame": sync,
    }
    if angle == "A7IV":
        out["clip_frame"] = sync - A7_OFFSET
    elif angle == "FX6":
        out["clip_frame"] = sync - FX6_OFFSET
    return out


def span(a: float, b: float, angle: str | None = None) -> dict[str, Any]:
    return {
        "window_t": [round(a, 2), round(b, 2)],
        "mix_seconds": [round(MIX_IN + a, 2), round(MIX_IN + b, 2)],
        "deliverable_seconds": [round(DELIV_IN + a, 2), round(DELIV_IN + b, 2)],
        "sync_frames": [SYNC_IN + round(a * FPS), SYNC_IN + round(b * FPS)],
        "clip_frames": (
            [at(a, angle)["clip_frame"], at(b, angle)["clip_frame"]] if angle else None
        ),
        "seconds": round(b - a, 2),
    }


WINDOWS: list[dict[str, Any]] = [
    {
        "angle": "A7IV",
        "flag": [63.98, 64.94],
        "tool_peak": 0.416,
        "tool_coverage": 0.0829,
        "verdict": "TRUE BLOCKING — VETO",
        "veto": [62.9, 66.6],
        "evidence": [
            "t=62.50 (clip 91147): clean four-shot — pianist far left, sax standing left of "
            "centre, bassist behind right, drummer near field right. Only the sax's horn bell "
            "shows at the bottom-right corner.",
            "t=62.98 (clip 91159, scan 0.093): the sax player is entering at the right edge, "
            "his bell already across the kit. This is the in-point of the crossing.",
            "t=63.50 (clip 91171): his head and shoulder fill the right third, out of focus; "
            "the DRUMMER is entirely covered.",
            "t=63.98 (clip 91183, scan 0.416, the flagged sample): his back covers the kit AND "
            "the bassist; only the pianist survives, at the far left.",
            "t=64.48 (clip 91195): he has swept to left of centre; the PIANIST is now covered "
            "and the kit is coming back.",
            "t=65.00 (clip 91207, scan 0.087): still standing over the pianist, back to camera.",
            "t=66.00 (clip 91231, scan 0.0): back still in the left third, pianist behind it; "
            "drummer and bassist clear.",
            "t=67.00 (clip 91255, scan 0.0): clean again — he has turned side-on at his mic and "
            "the pianist's head and shoulders are visible past him.",
            "t=70.00 (clip 91327): clean four-shot, sax playing side-on. Confirms he stays put.",
        ],
        "reading": (
            "A player crossing the near field, which is the ledger's definition of a true "
            "blocking, and it moves: right edge at 62.98, right third at 63.50, centre at "
            "63.98, left of centre at 64.48, left third until 66.x, gone by 67.00. What makes "
            "it worth a ledger line is the score: it peaked at 0.416 while the same window's "
            "two drummer FALSE positives peaked at 0.469 and 0.472. The detector under-called a "
            "real body and over-called a subject, in the same 90 seconds, on the same angle."
        ),
        "action": (
            "A7IV unusable 62.9-66.6 s into the window (mix 3798.06-3801.76, deliverable "
            "229.58-233.28, SYNC 177463-177552). This is the piece's sharpest constraint: the "
            "front change to `other` at mix 3801.40 and the song's peak at mix 3802.0 sit at "
            "the very end of it. The A7IV clears 0.36 s after the change and 0.24 s before the "
            "peak — so the return is either taken on the FX6, or taken on the A7IV no earlier "
            "than mix 3801.76 (SYNC 177552), where the shot is the sax player arriving at his "
            "mic to play it."
        ),
    },
    {
        "angle": "A7IV",
        "flag": [77.99, 78.95],
        "tool_peak": 0.472,
        "tool_coverage": 0.0966,
        "verdict": "OVERRIDDEN — false positive",
        "evidence": [
            "control t=76.99 (clip 91495, scan 0.0): clean four-shot, drummer's stick raised "
            "across the right third.",
            "t=77.99 (clip 91519, the flagged sample): the SAME frame with the stick lowered "
            "onto the snare. Nothing has entered or left the foreground.",
        ],
        "reading": (
            "The known drummer-as-subject false positive from the ending ledger: the near arm "
            "and stick are large, moving and anchored to the frame edge, which is three of the "
            "detector's four cues. They fail the fourth — the drummer is what the shot is "
            "about."
        ),
        "action": "no constraint.",
    },
    {
        "angle": "A7IV",
        "flag": [86.96, 87.92],
        "tool_peak": 0.469,
        "tool_coverage": 0.0958,
        "verdict": "OVERRIDDEN — false positive",
        "evidence": [
            "t=86.96 (clip 91734, the flagged sample): clean four-shot — sax playing centre "
            "left, pianist behind him, bassist bowing behind right, drummer near field right "
            "with the stick raised across the right third. Nothing between the lens and any "
            "subject.",
            "the sample either side reads 0.000, the isolated-single-sample shape the ledger "
            "already attributes to the drummer's arm.",
        ],
        "reading": "Same signature as 77.99. The drummer's arm again.",
        "action": "no constraint.",
    },
    {
        "angle": "FX6",
        "flag": [0.0, 41.96],
        "tool_peak": 0.693,
        "tool_coverage": 0.2351,
        "verdict": "OVERRIDDEN — false positive (new signature: mid-take reframe)",
        "evidence": [
            "t=8.97 (clip 58594, scan 0.682): clean two-shot — pianist left at the keys, bassist "
            "right, kit at the right edge, one audience head low in the bottom-right corner. "
            "Nothing in the way.",
            "t=22.98 (clip 58930, scan 0.693, the highest sample on either angle): the same "
            "framing, the same clean stage.",
            "t=41.00 (clip 59362, scan 0.482): still that framing.",
            "t=42.96 (clip 59409, scan 0.000): the shot is WIDER and panned a little right — "
            "more of the kit is in, the piano lid is smaller in frame. Same clean stage.",
            "t=60.00 (clip 59818, scan 0.000): wider still. The audience head in the "
            "bottom-right corner is MORE prominent here, at a score of 0.000, than it was at "
            "22.98 at 0.693.",
        ],
        "reading": (
            "Nothing is ever in the way; what changes at t~41.5 is the LENS. Samples run "
            "0.48-0.69 for the whole pre-reframe stretch and then fall to 0.000 within one "
            "sample (0.482 at 41.00, 0.049 at 41.96, 0.000 at 42.96) and stay there for the "
            "remaining 48 s. The detector's reference matches the post-reframe framing — the "
            "majority of the range it analysed — so the tighter first half reads as a large "
            "displaced blob, which is the dark piano lid the ledger already knows about, moved "
            "sideways by the reframe. Coverage of 0.19-0.24 is about the lid's share of frame. "
            "A static occluder would flag all 90 s; a body would flag seconds, not 42 of them, "
            "and would not stop on a frame."
        ),
        "action": (
            "no occlusion constraint. One editorial note that is not occlusion: the FX6 "
            "reframes between t=41.00 and t=42.96 (mix 3776.16-3778.12, deliverable "
            "207.68-209.64). A cut across it is fine; a shot HELD across it shows the widening."
        ),
    },
]

CONTROLS: list[dict[str, Any]] = [
    {
        "angle": "FX6",
        "at": 0.0,
        "scan_score": 0.517,
        "reading": (
            "The window opens on the clean tighter framing — piano and bass, kit at the edge."
        ),
    },
    {
        "angle": "FX6",
        "at": 30.99,
        "scan_score": 0.657,
        "reading": "Deep inside the flagged stretch and still the same clean stage.",
    },
    {
        "angle": "FX6",
        "at": 85.0,
        "scan_score": 0.0,
        "reading": "Late control in the clean half, after the reframe.",
    },
    {
        "angle": "A7IV",
        "at": 61.5,
        "scan_score": 0.086,
        "reading": "Control a second before the crossing begins.",
    },
]


def main() -> None:
    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    a7 = scan["angles"]["A7IV"]
    fx6 = scan["angles"]["FX6"]

    report = {
        "kind": "occlusion_adjudication",
        "piece": (
            "Taurus People MID / PIECE 3 (mix 3735.16-3825.16 s, deliverable 166.68-256.68 s, "
            "Zinc SYNC 175955-178113)"
        ),
        "scan": (
            "gauntlet/recon/occl_mid_scan.py -> occl_mid_scan.json (analyze_occlusion, 90 "
            f"samples per angle, flag threshold {THRESHOLD})"
        ),
        "grabs": [
            "gauntlet/recon/occl_mid_grabs.py -> occl_mid_grabs.json (flags + controls)",
            "gauntlet/recon/occl_mid_grabs2.py -> occl_mid_grabs2.json (edges of the crossing)",
            "gauntlet/recon/occl_mid_grabs3.py -> occl_mid_grabs3.json (how long he stays)",
        ],
        "method": (
            "Every window the scan flagged was judged by eye on frames inside it plus a control "
            "outside it, because the ledger's test for a true blocking is that it MOVES. A "
            "window is a VETO only when a body sits in the foreground third over a player the "
            "shot is framed on (styles/concert.md sec 4). Frame arithmetic: SYNC = 175955 + "
            "round(t * 23.976), A7IV clip = SYNC - 86306, FX6 clip = SYNC - 117576, mix = "
            "3735.16 + t, deliverable = 166.68 + t."
        ),
        "headline": (
            "One real veto, three overrides. The A7IV is genuinely blocked for ~3.7 s at "
            "t=62.9-66.6 (mix 3798.06-3801.76) while the sax player walks through the near "
            "field on his way back to the mic — and that ends 0.24 s before the song's peak, so "
            "it lands exactly on the piece's biggest moment. Its other two flags are the "
            "drummer, and the FX6's single 42 s flag is a camera reframe, not an obstruction. "
            "The real blocking scored 0.416; the two false ones scored 0.469 and 0.472."
        ),
        "ledger_updates": [
            "The score does not rank truth on the A7IV. A real body crossing the near field "
            "(0.416) scored BELOW two drummer false positives (0.469, 0.472) in the same window. "
            "Whatever the threshold, this angle needs the frames.",
            "New FX6 false-positive mode: a mid-take reframe. Every sample before the reframe "
            "flags at 0.48-0.69 and every sample after reads 0.000, with nothing in the way in "
            "either half. Distinguishable from a true blocking by duration (tens of seconds, "
            "not seconds), by the one-sample cliff at the edge, and by the two halves being the "
            "same clean stage at different focal lengths.",
            "The detector goes blind to a body once it stops moving: from t=65 to t=66.x the sax "
            "player's back stands over the pianist at 0.000. Bounding the tail of a crossing "
            "needs frames, not samples.",
        ],
        "angles": {
            "A7IV": {
                "clip": "20260617_D_A7IV_0006.MP4",
                "source_range": [a7["source_range"]["in"], a7["source_range"]["out"]],
                "baseline": a7["baseline"],
                "samples": len(a7["samples_in_piece"]),
                "samples_over_threshold": sum(
                    1 for one in a7["samples_in_piece"] if one["score"] >= THRESHOLD
                ),
                "shot": (
                    "Four-shot from stage right: pianist far left in the background, sax "
                    "standing left of centre, bassist behind right, drummer in the near field "
                    "at the right edge. The drummer, his cymbals and his sticks occupy the "
                    "right third of every frame."
                ),
                "verdict": (
                    "clear EXCEPT 62.9-66.6 s (mix 3798.06-3801.76, SYNC 177463-177552), where "
                    "the sax player crosses the near field. Two of the three flags OVERRIDDEN; "
                    "the third is real and is the veto."
                ),
                "unusable": [span(62.9, 66.6, "A7IV")],
            },
            "FX6": {
                "clip": "A015C001_2606170J.MXF",
                "source_range": [fx6["source_range"]["in"], fx6["source_range"]["out"]],
                "baseline": fx6["baseline"],
                "samples": len(fx6["samples_in_piece"]),
                "samples_over_threshold": sum(
                    1 for one in fx6["samples_in_piece"] if one["score"] >= THRESHOLD
                ),
                "shot": (
                    "Two-shot from the room, stage left: pianist at the keys on the left, "
                    "bassist centre right, kit at the right edge, one audience head low in the "
                    "bottom-right corner. Reframes wider at t~41.5."
                ),
                "verdict": (
                    "clear for the whole window — the single 42 s flag is the reframe, and the "
                    "known corner head scores 0.000 in the half where it is most prominent"
                ),
                "unusable": [],
            },
        },
        "windows": [
            {
                **one,
                "flagged": span(one["flag"][0], one["flag"][1], str(one["angle"])),
                "veto_span": (
                    span(one["veto"][0], one["veto"][1], str(one["angle"]))
                    if one.get("veto")
                    else None
                ),
                "flag": None,
                "veto": None,
            }
            for one in WINDOWS
        ],
        "controls": [{**one, "at": at(float(one["at"]), str(one["angle"]))} for one in CONTROLS],
        "for_the_builder": {
            "hard_constraint": (
                "No A7IV between SYNC 177463 and 177552 (mix 3798.06-3801.76, deliverable "
                "229.58-233.28)."
            ),
            "what_it_costs": (
                "The front change to `other` (mix 3801.40) and the song's structural peak (mix "
                "3802.0) sit inside or on the edge of the veto, so the single biggest cut point "
                "in the piece cannot be an A7IV cut taken early. Cutting to the A7IV at or "
                "after SYNC 177552 buys the sax player arriving at his mic on the peak; cutting "
                "to the FX6 anywhere buys the whole band with no constraint at all."
            ),
            "free_elsewhere": (
                "Every other second of the window is clear on both angles — 87 of 90 A7IV "
                "samples and all 90 FX6 samples survive adjudication."
            ),
        },
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report["headline"])
    print("A7IV unusable:", json.dumps(report["angles"]["A7IV"]["unusable"]))
    for one in report["windows"]:
        print(
            f"  {one['angle']:5s} {one['flagged']['window_t']} "
            f"peak={one['tool_peak']} -> {one['verdict']}"
        )


if __name__ == "__main__":
    main()
