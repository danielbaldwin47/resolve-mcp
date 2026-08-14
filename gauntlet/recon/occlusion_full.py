"""Adjudicate the occlusion scan over the two unjudged Taurus spans. READ-ONLY.

The verdicts below were reached by eye on the frames occl_full_grabs.py pulled; this file holds
them as data and does the time-base arithmetic, so every claim carries its deliverable second,
its Zinc-SYNC frame and its clip frame without anyone converting by hand.

THE LEDGER'S TEST, applied: a window is a VETO only when a body covers a player the shot is
FRAMED ON, and only when it MOVES between the frames either side. The score does not decide -
occlusion_verdict_r3.json records an FX6 true blocking at peak 1.0 and false positives at
0.58-0.68, and occlusion_mid.json records an A7IV TRUE crossing that peaked at 0.416 while two
false positives on the same angle in the same 90 s peaked at 0.469 and 0.472.

RESULT: 16 flagged windows, 16 overrides, NO VETO anywhere in either new span. Both signatures
were already in the ledger:

  * FX6, span A (8 windows, peaks 0.400-0.572 over a 0.100 baseline): the black piano lid fills
    the left foreground of this framing and a small dark audience head sits low in the extreme
    bottom-right corner, between the lens and an empty part of the room. styles/concert.md sec 4
    names that exact case as background rather than obstruction, and occlusion_verdict_r3.json
    already overrode four FX6 windows on it, including a 23 s one.
  * A7IV, span B (8 windows, peaks 0.443-0.462 over a 0.000 baseline): the detector is scoring
    the shot's own near-field subject - the drummer. occlusion_verdict_r3.json and
    occlusion_mid.json both carry this signature, in the same 0.44-0.48 band.

Frame arithmetic: SYNC = 171959 + round(d * 23.976); FX6 clip = SYNC - 117576;
A7IV clip = SYNC - 86306.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
OUT = RECON / "occlusion_full.json"
SCAN = json.loads((RECON / "occl_full_scan.json").read_text(encoding="utf-8"))

FPS = 24000.0 / 1001.0
DELIV_ZERO_SYNC = 171959
OFFSETS = {"FX6": 117576, "A7IV": 86306}


def at(d: float, angle: str) -> dict[str, Any]:
    sync = DELIV_ZERO_SYNC + round(d * FPS)
    return {"d": round(d, 2), "sync_frame": sync, "clip_frame": sync - OFFSETS[angle]}


VERDICTS: list[dict[str, Any]] = [
    {
        "span": "A",
        "angle": "FX6",
        "windows_d": [
            [91.96, 94.92], [96.97, 104.93], [106.98, 110.94], [114.98, 115.94],
            [117.99, 118.95], [120.99, 124.95], [128.00, 147.93], [149.98, 164.95],
        ],
        "peaks": [0.400, 0.523, 0.484, 0.556, 0.425, 0.414, 0.570, 0.572],
        "baseline": 0.100,
        "verdict": "OVERRIDDEN - false positives, all eight",
        "frames_read": [93.4, 100.5, 108.5, 115.4, 118.4, 122.5, 130.0, 138.0,
                        145.0, 152.0, 158.0, 163.0],
        "evidence": [
            "Twelve frames spread across the two biggest windows (the 19.9 s one at 128.00 and "
            "the 15.0 s one at 149.98) and every short one: the picture is an unchanging "
            "piano-and-bass two-shot - pianist at frame left with his back to camera, bassist "
            "centre right, kit at the extreme right edge - and it is IDENTICAL in flagged and "
            "unflagged seconds (occl_full_frames/sheet_afx6_a.jpg, _b.jpg, _c.jpg).",
            "Nothing crosses. full_motion.json independently measures moving_share 0.000 on this "
            "angle across d88-170, so neither the camera nor anything in the near field moves at "
            "all for 82 seconds - which is the opposite of the ledger's test for a true blocking.",
            "What is being scored is the black piano lid across the left foreground plus a small "
            "dark head low in the bottom-right corner, in front of an empty part of the room.",
        ],
        "action": "no constraint applied; the FX6 carries 48.8 s of span A.",
    },
    {
        "span": "A",
        "angle": "A7IV",
        "windows_d": [],
        "peaks": [],
        "baseline": 0.0251,
        "verdict": "CLEAN - the scan flagged nothing",
        "frames_read": [],
        "evidence": ["No windows returned over 82 seconds."],
        "action": "no constraint applied.",
    },
    {
        "span": "B",
        "angle": "A7IV",
        "windows_d": [
            [264.65, 265.61], [312.65, 313.61], [334.67, 335.63], [347.65, 350.61],
            [387.64, 388.60], [392.65, 393.61], [397.65, 400.62], [404.66, 405.62],
        ],
        "peaks": [0.443, 0.461, 0.462, 0.446, 0.456, 0.446, 0.455, 0.459],
        "baseline": 0.0,
        "verdict": "OVERRIDDEN - false positives, all eight",
        "frames_read": [265.1, 312.0, 313.1, 314.5, 335.1, 348.0, 349.5, 350.5,
                        388.1, 393.1, 398.2, 399.5, 400.2, 401.5, 405.1, 406.5],
        "evidence": [
            "Sixteen frames, four per sheet so movement would show: every one is the same clean "
            "sax-and-drummer two-shot - the sax player standing at frame left PLAYING, the "
            "drummer in the near field at the right, the bassist behind between them "
            "(occl_full_frames/sheet_ba7_a.jpg through _d.jpg). Nobody crosses, nothing is "
            "covered, and the frames either side of each flag are indistinguishable from it.",
            "The flagged band (0.443-0.462) sits inside the ledger's known false-positive band "
            "for this angle - the drummer, who is the shot's own foreground subject, scored "
            "0.469 and 0.472 as confirmed false positives in occlusion_mid.json, where the ONE "
            "true crossing peaked LOWER at 0.416.",
        ],
        "action": (
            "no constraint applied. This matters most at 397.65-400.62, which is the run-in to "
            "the song's second-loudest instant (d400.02): the A7IV carries d391.10-399.94 there, "
            "and had the flag been real the whole gesture would have had to move."
        ),
    },
    {
        "span": "B",
        "angle": "FX6",
        "windows_d": [],
        "peaks": [],
        "baseline": 0.0865,
        "verdict": "CLEAN - the scan flagged nothing",
        "frames_read": [],
        "evidence": ["No windows returned over 151 seconds."],
        "action": "no constraint applied.",
    },
]

CARRIED: list[dict[str, Any]] = [
    {
        "source": "occlusion_verdict_r3.json",
        "angle": "FX6",
        "veto_d": [11.97, 18.94],
        "note": "true near-field blocking inside the opening piece; the opening's cut list, "
                "carried here frame for frame, keeps the FX6 off screen 10.64-20.48.",
    },
    {
        "source": "occlusion_verdict_r3.json",
        "angle": "A7IV",
        "veto_d": [41.96, 42.92],
        "note": "true near-field blocking inside the opening piece; the opening's own release "
                "shot covers it.",
    },
    {
        "source": "occlusion_mid.json",
        "angle": "A7IV",
        "veto_d": [229.58, 233.28],
        "note": "the sax player crossing the near field; the mid piece's s10 leaves the A7IV "
                "1.96 s before it and the song's peak at d233.52 is taken on the FX6. Carried "
                "here unchanged.",
    },
]


def main() -> None:
    report = {
        "kind": "occlusion_adjudication_full",
        "song": "Taurus People",
        "spans": {"A": [90.00, 166.68], "B": [256.68, 407.66]},
        "scan": "occl_full_scan.json",
        "grabs": "occl_full_grabs.py -> occl_full_frames/",
        "flagged_windows": sum(len(v["windows_d"]) for v in VERDICTS),
        "vetoes_in_new_spans": 0,
        "verdicts": [
            {
                **v,
                "windows": [
                    {"in": at(a, v["angle"]), "out": at(b, v["angle"]), "peak": p}
                    for (a, b), p in zip(v["windows_d"], v["peaks"], strict=True)
                ],
            }
            for v in VERDICTS
        ],
        "carried_vetoes_from_the_won_pieces": CARRIED,
        "scan_baselines": {
            key: entry.get("baseline") for key, entry in SCAN["results"].items()
        },
    }
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("flagged:", report["flagged_windows"], "vetoes:", report["vetoes_in_new_spans"])
    for v in VERDICTS:
        print(f"  {v['span']}:{v['angle']:5} {len(v['windows_d'])} windows -> {v['verdict']}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
