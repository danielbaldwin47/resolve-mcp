"""Relaunch the Zinc master-mix separation as a DETACHED job on the CUDA separator env.

Same hand-off shape as stems_detached_launch.py. The one difference is the config override:
RESOLVE_MCP_AUDIO_SEPARATOR is pointed at a dedicated GPU venv's audio-separator.exe (torch
built against CUDA), set before get_config() ever reads the environment so the detached
worker inherits it through Config.to_env().

READ-ONLY on Resolve: scope=clip only locates the mix in the media pool and reads its file
off disk. No timeline is made current, nothing is written back.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

GPU_SEPARATOR = r"C:\Users\Daniel\.venvs\audio-separator-gpu\Scripts\audio-separator.exe"
os.environ["RESOLVE_MCP_AUDIO_SEPARATOR"] = GPU_SEPARATOR

CLIP = "Zinc Set 2 Reaper v4.wav"
BIN = "Zinc Bar/Audio"
OUT = Path(__file__).with_name("stems_gpu_launch.json")
HANDOFF_BUDGET = 300.0

report: dict[str, Any] = {
    "clip": CLIP,
    "state": "starting",
    "launcher_pid": os.getpid(),
    "separator": GPU_SEPARATOR,
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()

    from resolve_mcp.audio import stems as stems_module
    from resolve_mcp.config import get_config
    from resolve_mcp.jobs import detached, lifecycle, store
    from resolve_mcp.resolve.connection import get_connection

    config = get_config()
    report["config_separator"] = config.audio_separator
    write()

    started = stems_module.separate_stems(
        get_connection(), scope="clip", clip=CLIP, bin=BIN, split_wind=False, detach=True
    )
    job_id = str(started["job_id"])
    report.update({"job_id": job_id, "state": started.get("state"), "cached": started.get("cached")})
    report["record"] = str(config.job_dir / f"{job_id}.json")
    report["worker_log"] = str(detached.worker_log(job_id, config))
    write()

    deadline = time.monotonic() + HANDOFF_BUDGET
    record = store.load(job_id, config)
    while record.pid is None and record.state == lifecycle.RUNNING and time.monotonic() < deadline:
        time.sleep(0.2)
        record = store.load(job_id, config)

    report.update(
        {
            "state": record.state,
            "detached": record.detached,
            "worker_pid": record.pid,
            "step": record.step,
            "progress": record.progress,
            "audio": (record.plan or {}).get("audio", {}).get("path"),
            "stems_dir": str(config.stems_dir),
        }
    )
    write()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        report["state"] = "crashed"
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        write()
    print("GPU LAUNCHER DONE", OUT)
