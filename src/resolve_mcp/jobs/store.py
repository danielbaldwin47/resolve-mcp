"""One JSON record per job, on disk. Four things here are decisions, not bookkeeping.

* **Disk is the only source of truth.** An in-memory registry would be faster to read
  and would vanish on restart, which is precisely the case ``list_jobs`` exists for. The
  running thread updates its record as it goes; readers always read the file.

* **A restart is detected by session, not by pid.** Every record carries the id of the
  server process that wrote it. A record still marked ``running`` whose session is not
  this one belongs to a server that is gone — its worker thread died with the process, so
  nothing will ever finish it. pids get recycled; a per-process uuid cannot be mistaken.

* **That verdict is written back.** The interrupted record is rewritten as failed under
  this session, so the reasoning happens once and the log carries one line for it rather
  than one per poll.

* **A corrupt record loses itself, not the listing.** A record half-written when the
  process died is skipped with a warning; one unreadable file must not hide every other
  job from the agent.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import JobInterruptedError, JobNotFoundError
from ..logging_config import get_logger
from ..naming import slug

log = get_logger("jobs")

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STATES = (RUNNING, COMPLETED, FAILED)

SESSION = uuid.uuid4().hex
"""This server process. Records written under any other session predate a restart."""

_sequence = itertools.count()
"""Breaks ties in "newest first": the Windows clock is coarser than two starts in a row."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class JobRecord:
    """What the agent sees through ``get_job``, and what a restart reads back."""

    job_id: str
    kind: str
    state: str
    params: dict[str, Any] = field(default_factory=dict)
    session: str = SESSION
    sequence: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None
    progress: float = 0.0
    step: str = ""
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cache_key: str | None = None
    cached: bool = False

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _job_id(kind: str) -> str:
    return f"{slug(kind, 'job')}-{uuid.uuid4().hex[:12]}"


def _path(job_id: str, config: Config) -> Path:
    return config.job_dir / f"{job_id}.json"


def new_job(
    kind: str,
    params: dict[str, Any],
    cache_key: str | None = None,
    config: Config | None = None,
) -> JobRecord:
    """Register a running job and write it before any work starts."""
    stamp = _now()
    record = JobRecord(
        job_id=_job_id(kind),
        kind=kind,
        state=RUNNING,
        params=params,
        sequence=next(_sequence),
        started_at=stamp,
        updated_at=stamp,
        cache_key=cache_key,
    )
    save(record, config)
    log.info("Job %s started (%s)", record.job_id, kind)
    return record


def save(record: JobRecord, config: Config | None = None) -> None:
    """Write the record atomically — a poll must never read a half-written file."""
    config = config or get_config()
    record.updated_at = _now()
    target = _path(record.job_id, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_suffix(".writing")
    scratch.write_text(json.dumps(record.payload(), indent=2), encoding="utf-8")
    os.replace(scratch, target)


def finish(
    record: JobRecord,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    config: Config | None = None,
) -> JobRecord:
    """Close the job out as completed or failed, and say so in the log."""
    record.state = FAILED if error is not None else COMPLETED
    record.result = result
    record.error = error
    record.finished_at = _now()
    record.progress = 1.0 if error is None else record.progress
    save(record, config)
    if error is None:
        log.info("Job %s completed", record.job_id)
    else:
        log.warning("Job %s failed: %s", record.job_id, error.get("cause"))
    return record


def load(job_id: str, config: Config | None = None) -> JobRecord:
    """Read one record, converting a restart-orphaned job into an honest failure."""
    config = config or get_config()
    record = _read(_path(job_id, config))
    if record is None:
        raise JobNotFoundError(job_id)
    return _recovered(record, config)


def load_all(state: str | None = None, config: Config | None = None) -> list[JobRecord]:
    """Every job this cache directory knows about, newest first."""
    config = config or get_config()
    records = [_read(path) for path in sorted(config.job_dir.glob("*.json"))]
    found = [_recovered(one, config) for one in records if one is not None]
    found.sort(key=lambda one: (one.started_at, one.sequence), reverse=True)
    if state is None:
        return found
    return [one for one in found if one.state == state]


def _read(path: Path) -> JobRecord | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Skipping an unreadable job record: %s", path)
        return None
    if not isinstance(raw, dict) or "job_id" not in raw:
        log.warning("Skipping a job record with no id: %s", path)
        return None
    known = {name: raw[name] for name in JobRecord.__dataclass_fields__ if name in raw}
    return JobRecord(**known)


def _recovered(record: JobRecord, config: Config) -> JobRecord:
    """A job still running under a dead session cannot finish. Say so, once."""
    if record.state != RUNNING or record.session == SESSION:
        return record
    log.info("Job %s was interrupted by a server restart", record.job_id)
    interrupted = JobInterruptedError(
        cause=f"The server restarted while {record.kind} was running, so the job died with it.",
        detail={"job_id": record.job_id, "progress": record.progress, "step": record.step},
    )
    record.session = SESSION
    return finish(record, error=interrupted.payload(), config=config)
