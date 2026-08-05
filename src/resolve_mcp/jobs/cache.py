"""Hash-keyed results, so analysis is paid for once per media state.

Two kinds of identity live here, and the difference is the whole design:

* **Source media is fingerprinted, not hashed.** A concert master is tens of gigabytes and
  sits on the director's disk unchanged for months; reading all of it to prove that takes
  minutes, every run. Path, size and mtime answer "is this the same file" well enough to
  key an acquisition, and the acquisition is cheap to redo if the guess is ever wrong.

* **Acquired audio is hashed for real.** The WAV this server wrote is the substrate every
  analysis job keys off (#22: "workers key off content hash of cached audio + params
  hash"), it is a manageable size, and a false cache hit there would silently attribute
  one concert's beats to another.

A hit is only a hit if the artifacts are still on disk. The cache directory is a user's
local app data — they are allowed to delete things in it, and the agent must get a rerun
rather than a path to a file that is gone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..logging_config import get_logger

log = get_logger("jobs")

CHUNK = 1 << 20


def fingerprint(path: Path | str) -> dict[str, Any]:
    """Cheap identity for a source file the server did not write."""
    resolved = Path(path)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def content_hash(path: Path | str) -> str:
    """sha256 of the file's bytes, read a megabyte at a time."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(
    kind: str,
    inputs: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> str:
    """One key over what was run, on what, with which settings."""
    canonical = json.dumps(
        {"kind": kind, "inputs": list(inputs), "params": params},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry_path(key: str, config: Config) -> Path:
    return config.result_dir / f"{key}.json"


def lookup(key: str, config: Config | None = None) -> dict[str, Any] | None:
    """The remembered result, or ``None`` — including when its artifacts went missing."""
    config = config or get_config()
    path = _entry_path(key, config)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Discarding an unreadable cache entry: %s", path)
        _discard(path)
        return None
    missing = [one for one in entry.get("artifacts", []) if not Path(one).exists()]
    if missing:
        log.info("Cache entry %s lost its artifacts (%s); rerunning", key[:12], missing[0])
        _discard(path)
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None


def _discard(path: Path) -> None:
    """Drop a cache entry that cannot be trusted. A miss must never raise on the way out.

    The delete can fail for the same reason the read did — the cache directory is the
    user's own, and they can leave anything in it. A cache that cannot clean up is still a
    cache that missed, and the job it was asked about must go ahead and run.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not remove the stale cache entry %s", path, exc_info=True)


def remember(
    key: str,
    kind: str,
    result: dict[str, Any],
    artifacts: Iterable[Path | str],
    config: Config | None = None,
) -> None:
    """Record a finished result against its key, naming the files it owns."""
    config = config or get_config()
    entry = {
        "key": key,
        "kind": kind,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "artifacts": [str(one) for one in artifacts],
        "result": result,
    }
    path = _entry_path(key, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    log.info("Cached %s result under %s", kind, key[:12])
