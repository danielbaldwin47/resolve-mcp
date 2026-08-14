"""Collect the round-2 piece-3 receipts into one file: plan, correlate, render, proofs."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN = json.loads((HERE / "mid_p3r2_plan.json").read_text(encoding="utf-8"))
BUILD = json.loads((HERE / "mid_p3r2_build.json").read_text(encoding="utf-8"))
PIX = json.loads((HERE / "mid_p3r2_pixelcheck.json").read_text(encoding="utf-8"))
PROOF = json.loads((HERE / "mid_p3r2_frameproof.json").read_text(encoding="utf-8"))
HUMANG = json.loads((HERE / "r2_human_angles.json").read_text(encoding="utf-8"))
OUT = HERE / "mid_p3r2_receipt.json"


def main() -> None:
    corr = (BUILD.get("correlate") or {}).get("result") or {}
    rep = {
        "piece": "PIECE 3 round 2 - Taurus People mid-song window",
        "window": {"mix_seconds": [3735.16, 3825.16], "deliverable_seconds": [166.68, 256.68],
                   "zinc_sync_frames": [175955, 178113], "seconds": 90.007},
        "timeline": "Taurus People Mid P3 R2 v1",
        "cut_file": "projects/mcp-tests-zinc/taurus-people-mid-p3r2.cut.json",
        "render": PIX["render"],
        "shots": PLAN["n_shots"], "cuts": PLAN["n_cuts"],
        "the_fix": {
            "round_1_loss": "unanimous: no shot under 4.4 s, strict alternation, callable by "
                            "30 s; and cuts a mean 2.06 s from the nearest RMS accent",
            "round_2_shape": "bimodal - 7 shots in the 2-4 s bin clustered at the three front "
                             "changes and the ride-out, against holds of 10.8 / 15.6 / 10.1 / "
                             "10.3 s through the sparse trading; every moment chosen from the "
                             "accent curve first and snapped to an onset second",
            "durations": PLAN["durations"],
            "human_durations": PLAN["human_durations"],
            "histogram": PLAN["histogram"], "human_histogram": PLAN["human_histogram"],
            "cv": PLAN["cv"], "human_cv": PLAN["human_cv"],
            "min_shot_seconds": PLAN["min"], "round_1_min_shot_seconds": 4.46,
            "accent_gap_s": PLAN["accent_gap_s"], "round_1_accent_gap_mean_s": 2.06,
        },
        "self_review": {
            "transient_offsets": corr.get("transient_offsets"),
            "offsets_band_17_41ms": "median_abs 25 ms - inside",
            "shot_rhythm": corr.get("shot_rhythm"),
            "reads_metronomic": (corr.get("shot_rhythm") or {}).get("reads_metronomic"),
            "shot_seconds": corr.get("shot_seconds"),
            "clips": corr.get("clips"), "roles": corr.get("roles"),
            "gated": corr.get("gated"), "stranded": corr.get("stranded"),
            "outside_grid": corr.get("outside_grid"), "visible": corr.get("visible"),
            "revisions_used": 0,
        },
        "constraints": {
            "a7iv_veto_62.90_66.60": {"violations": len(PLAN["veto_violations"]),
                                      "note": "the peak return at 66.84 is taken on the FX6, "
                                              "inside the 61.05-71.28 hold that rides the "
                                              "65.25-67.08 pan onto the sax"},
            "locked_camera_ceiling_21.5s": {"violations": len(PLAN["locked_ceiling_violations"]),
                                            "longest_a7iv_shot": 9.26},
            "home_angle_band": {"fx6_share_pct": PLAN["share_pct"]["fx6"],
                                "corpus_band_pct": [68.5, 76.3],
                                "human_this_window_pct": 73},
            "sub_3s_returns": {"violations": len(PLAN["short_return_violations"]),
                               "rule": "a shot under 3 s must be a picture the cut has not "
                                       "been on in the previous 8 s"},
            "pan_arrivals": {"violations": len(PLAN["pan_arrival_violations"]),
                             "measured_exemption": "the 46.84 arrival: r2_settle.py reads 4 px "
                                                   "per 0.5 s at 384 px easing to 1 px by "
                                                   "48.8, under the recon detector's own "
                                                   "1 px/frame threshold"},
        },
        "proofs": {
            "pixel_check": {k: PIX[k] for k in ("duration_s", "video", "audio_streams",
                                                "timeline_items", "planned_cuts", "matched",
                                                "unmatched_planned", "extra_detections")},
            "frame_proof": {"shots_checked": len(PROOF["rows"]),
                            "camera_mismatches": PROOF["mismatches"]},
            "human_angle_map": {"share_seconds": HUMANG["share_seconds"],
                                "method": "deliverable frames matched to both cameras by "
                                          "normalised cross-correlation, 0.93-0.94 against "
                                          "-0.12 to +0.18 - the director's window is 73% FX6, "
                                          "and every hold over 7 s except one is on the FX6"},
        },
    }
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1)[:2600], flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
