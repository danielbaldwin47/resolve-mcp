"""Can the applause route find the five tunes on this mix at all? Threshold sweep.

The default run returned one tune and zero applause (peak probability 0.2977 against a 0.3
threshold), so this asks whether the tagger is merely under-confident on a board mix or
blind to it. No Resolve calls. Writes gauntlet/recon/tunes_sweep.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
OUT = Path(__file__).with_name("tunes_sweep.json")
THRESHOLDS = (0.2, 0.12, 0.06)
TRUTH = [107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815]

report: dict[str, Any] = {"mix": MIX, "human_cut_starts_s": TRUTH, "runs": {}}


def main() -> None:
    from resolve_mcp.analysis import structure
    from resolve_mcp.jobs import runner, store

    for threshold in THRESHOLDS:
        began = time.time()
        record = structure.analyze_structure(
            audio=MIX, tunes=True, solos=False, threshold=threshold, density_per_second=0.0
        )
        job_id = record["job_id"]
        while True:
            got = runner.wait_for(job_id, timeout=30.0)
            if got.state != store.RUNNING:
                break
        payload = got.payload()
        entry: dict[str, Any] = {
            "job_id": job_id,
            "state": payload.get("state"),
            "elapsed_s": round(time.time() - began, 1),
            "error": payload.get("error"),
        }
        result = payload.get("result") or {}
        gist = result.get("tunes") or {}
        entry["gist"] = {k: v for k, v in gist.items() if k != "path"}
        path = gist.get("path")
        if path and Path(path).exists():
            body = json.loads(Path(path).read_text(encoding="utf-8"))
            entry["path"] = path
            entry["boundaries_s"] = [round(t["t"], 2) for t in body.get("tunes", [])]
        report["runs"][str(threshold)] = entry
        OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", OUT)


if __name__ == "__main__":
    main()
