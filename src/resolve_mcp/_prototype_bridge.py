"""PROTOTYPE (#269) — throwaway. Never merges to main.

Question: can Blackmagic's native server be the only MCP server the agent sees, with this
repo's analysis stack behind ``run_script_unsafe``? The native worker is ResolvePython 3.14
and cannot import resolve_mcp (3.12 wheels), so it subprocesses this module in the venv:

    run <tool> <json kwargs | @file>   call a tools.analysis tool; first stdout line is the
                                       envelope; stdout then closes and, if a job is running,
                                       the process stays alive until it ends (thread jobs die
                                       with their process)
    job <job_id>                       peek the record (no orphan recovery write)
    load <job_id>                      store.load — shows the cross-process orphan verdict
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

LOG = Path(os.environ.get("TEMP", ".")) / "resolve-mcp-prototype-bridge.log"


def _emit(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _run(tool: str, raw: str) -> int:
    from resolve_mcp.jobs import store
    from resolve_mcp.tools import analysis

    kwargs = json.loads(Path(raw[1:]).read_text("utf-8") if raw.startswith("@") else raw)
    started = time.monotonic()
    envelope = getattr(analysis, tool)(**kwargs)
    envelope["bridge"] = {"pid": os.getpid(), "call_seconds": round(time.monotonic() - started, 2)}
    _emit(envelope)
    sys.stdout.close()
    os.close(1)  # the launcher's pipe: nothing more goes to it, so it may stop reading

    job = envelope.get("job") or {}
    job_id = job.get("job_id")
    if not job_id or job.get("state") != "running":
        return 0
    logging.info("bridge pid %s holding job %s until it ends", os.getpid(), job_id)
    while True:
        time.sleep(2)
        record = store.peek(job_id)
        if record is None or record.state != "running":
            logging.info("job %s ended: %s", job_id, record and record.state)
            return 0


def _job(job_id: str, recover: bool) -> int:
    from resolve_mcp.jobs import store

    record = store.load(job_id) if recover else store.peek(job_id)
    _emit({"ok": record is not None, "job": record.payload() if record else None})
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(
        filename=LOG, level=logging.INFO, format="%(asctime)s %(process)d %(name)s %(message)s"
    )
    logging.info("bridge argv %s exe %s", argv, sys.executable)
    if len(argv) == 3 and argv[0] == "run":
        return _run(argv[1], argv[2])
    if len(argv) == 2 and argv[0] in ("job", "load"):
        return _job(argv[1], recover=argv[0] == "load")
    _emit({"ok": False, "error": "usage: run <tool> <json|@file> | job <id> | load <id>"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
