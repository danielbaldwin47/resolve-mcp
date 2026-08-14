"""Sample nvidia-smi and the job record side by side, so a CPU fallback shows up as a flat GPU.

Prints one line per sample: wall clock, GPU util/mem/power, job step and progress.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

JOB = sys.argv[1] if len(sys.argv) > 1 else "separate_stems-050a1d4ae98a"
SAMPLES = int(sys.argv[2]) if len(sys.argv) > 2 else 8
EVERY = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
RECORD = Path.home() / "AppData/Local/resolve-mcp/jobs" / f"{JOB}.json"

QUERY = [
    "nvidia-smi",
    "--query-gpu=utilization.gpu,memory.used,power.draw",
    "--format=csv,noheader",
]

for i in range(SAMPLES):
    gpu = subprocess.run(QUERY, capture_output=True, text=True).stdout.strip()
    try:
        rec = json.loads(RECORD.read_text(encoding="utf-8"))
        job = f"{rec.get('state')} p={rec.get('progress')} {rec.get('step')}"
    except Exception as exc:  # noqa: BLE001
        job = f"record unreadable: {exc}"
    print(f"{time.strftime('%H:%M:%S')}  GPU[{gpu}]  {job}", flush=True)
    if i < SAMPLES - 1:
        time.sleep(EVERY)
