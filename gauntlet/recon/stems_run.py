"""Start stem separation on the Zinc master mix and stay alive until it lands.

READ-ONLY on Resolve: scope=clip only locates the mix in the media pool and reads its file
off disk (acquire._locate_clip). No timeline is made current, nothing is written back.

Writes gauntlet/recon/stems_run.json as it goes, so a caller can poll the file.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

CLIP = "Zinc Set 2 Reaper v4.wav"
BIN = "Zinc Bar/Audio"  # five pool copies share the name; all point at the same file
OUT = Path(__file__).with_name("stems_run.json")

report: dict[str, Any] = {"clip": CLIP, "state": "starting", "errors": []}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    report["interpreter"] = "passed"
    write()

    from resolve_mcp.audio import stems as stems_module
    from resolve_mcp.jobs import lifecycle, runner
    from resolve_mcp.resolve.connection import get_connection

    began = time.time()
    record = stems_module.separate_stems(
        get_connection(), scope="clip", clip=CLIP, bin=BIN, split_wind=False
    )
    job_id = record.get("job_id")
    report.update({"job_id": job_id, "state": record.get("state"), "cached": record.get("cached")})
    write()

    while True:
        got = runner.wait_for(job_id, timeout=30.0)
        if got.state != lifecycle.RUNNING:
            break
        report["step"] = got.step
        report["progress"] = got.progress
        report["elapsed_s"] = round(time.time() - began, 1)
        write()

    payload = got.payload()
    report.update(
        {
            "state": payload.get("state"),
            "cached": payload.get("cached"),
            "elapsed_s": round(time.time() - began, 1),
            "result": payload.get("result"),
            "error": payload.get("error"),
        }
    )
    write()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        report["state"] = "crashed"
        report["errors"].append(
            {"type": type(exc).__name__, "message": str(exc),
             "traceback": traceback.format_exc(limit=8)}
        )
        write()
    print("DONE", OUT)
