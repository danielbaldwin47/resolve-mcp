"""Fold the round-3 ending receipts into one file. READ-ONLY over the run's outputs."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = json.loads((HERE / "taurus_ending_build_p2r3.json").read_text(encoding="utf-8"))
PIX = json.loads((HERE / "end_p2r3_pixelcheck.json").read_text(encoding="utf-8"))
CUT = json.loads(
    (HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending-r3.cut.json")
    .read_text(encoding="utf-8")
)
OUT = HERE / "taurus_ending_p2r3_receipt.json"

FPS = 24000.0 / 1001.0

corr = (BUILD["correlate"].get("result") or {})
segs = CUT["segments"]
acc = 0
shots = []
for s in segs:
    dur = s["out"] - s["in"]
    shots.append({
        "id": s["id"],
        "source": s["source"],
        "start_s": round(acc / FPS, 3),
        "end_s": round((acc + dur) / FPS, 3),
        "seconds": round(dur / FPS, 3),
        "note": s["note"],
    })
    acc += dur

doc = {
    "kind": "taurus_ending_p2_round3_receipt",
    "piece": "Taurus People - ENDING (last 90 s of the set)",
    "window": {"mix_seconds": [3976.15, 4066.15], "sync_frames": [181733, 183891], "fps": 23.976},
    "changed_from_round_2": (
        "one deletion and nothing else: round 2's 13th cut (82.71 s, to the A7IV kit) is "
        "gone, so the wide the piece returns to at 76.12 carries the near-gap, the cadence, "
        "the applause and the whole dissolve. Every other cut is frame-identical."
    ),
    "timeline": BUILD["build"].get("timeline"),
    "render": PIX["render"],
    "cut_file": str(
        HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending-r3.cut.json"
    ),
    "hard_cuts": len(segs) - 1,
    "shots": shots,
    "tail_device": {**CUT["tail"], "build_confirmed": BUILD["build"].get("tail")},
    "correlate": {
        "gated": corr.get("gated"),
        "stranded": corr.get("stranded"),
        "outside_grid": corr.get("outside_grid"),
        "transient_offsets": corr.get("transient_offsets"),
        "shot_seconds": corr.get("shot_seconds"),
        "roles": corr.get("roles"),
        "visible": corr.get("visible"),
    },
    "pixel_check": {
        "duration_s": PIX["ffprobe"]["format"]["duration"],
        "resolution": [
            PIX["ffprobe"]["streams"][0]["width"],
            PIX["ffprobe"]["streams"][0]["height"],
        ],
        "scene_cuts_010": PIX["scene_cuts_010"],
        "every_cut_detected": PIX["every_cut_detected"],
        "no_extra_scene_cuts": PIX["no_extra_scene_cuts"],
        "worst_boundary_delta_ms": PIX["worst_boundary_delta_ms"],
        "blackdetect": PIX["blackdetect"],
        "final_stretch": PIX["final_stretch"],
    },
    "tail_proof": {
        "passes": PIX["tail"]["passes"],
        "all_passed": PIX["tail"]["all_passed"],
        "dissolve_starts_s": PIX["tail"]["dissolve_starts_s"],
        "picture_ends_s": PIX["tail"]["picture_ends_s"],
        "steady_luma": PIX["tail"]["steady_luma"],
        "intermediate_samples": PIX["tail"]["intermediate_samples"],
        "intermediate_span_s": PIX["tail"]["intermediate_span_s"],
        "rms_ladder_db": PIX["tail"]["rms_ladder_db"],
        "rms_fell_db": PIX["tail"]["rms_fell_db"],
    },
    "grabs": PIX["grabs"],
    "style_claims_answered": [
        "concert.md 5b - the set's last image belongs to the ensemble (unanimous 3/3)",
        "concert.md 5b - not a licence to park: arrive at the wide late, do not wait on it",
        "concert.md 3 - into a free coda the cutting decelerates",
        "concert.md 4 - the home angle holds roughly three-quarters of the cut",
        "concert.md 1 - cuts land 17-41 ms from a transient and never on one",
    ],
}
OUT.write_text(json.dumps(doc, indent=1), encoding="utf-8")
print("wrote", OUT)
print("hard cuts", doc["hard_cuts"], "timeline", doc["timeline"])
print("shares", doc["correlate"]["roles"])
print("final stretch", json.dumps(doc["pixel_check"]["final_stretch"]))
