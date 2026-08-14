"""Hash-keyed results, so analysis is paid for once per media state.

Two kinds of identity live here, and the difference is the whole design:

* **Audio is identified by its bytes** (``identity``). The same concert reaches this server
  under several names — the director's master, the copy an acquisition staged into the cache
  directory, an excerpt rendered for one song — and keying on the name made each of those pay
  for its own beat model over identical audio (#193). ``sha256`` of the file is the identity,
  wherever the file sits, which is also what #22 asked for ("workers key off content hash of
  cached audio + params hash") and what keeps one concert's beats off another's cut.

* **Video sources are fingerprinted, not hashed** (``fingerprint``). A camera master is tens
  of gigabytes that sit unchanged for months; reading all of it to prove that takes minutes,
  and the jobs that key off it — scene cuts, grabbed frames, occlusion — never read it end to
  end themselves. Path, size and mtime answer "is this the same file" well enough there, and
  the cost of being wrong is one redundant rerun rather than a wrong answer.

Reading the bytes is not free, so **a hash is remembered against a fingerprint**
(``known_hash``): the first sight of a file state costs one read, and every later call for
that same path, size and mtime costs a ``stat``. That memo is what lets a starter whose whole
contract is to return a job id at once (#22, story 25) key on content — an audio master gets
read end to end by the analysis it is about to start anyway. A note that cannot be read,
cannot be written, or no longer describes the file costs a reread. What it cannot do is be
*more* trusting than the fingerprint it is guarded by, which is why audio under ``audio_dir``
is hashed for real every time and never read off a note: see ``audio_identity``.

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


def known_hash(path: Path | str, config: Config | None = None) -> str:
    """The file's content hash, read once per file state and remembered against a stat.

    The memo is what makes content identity affordable where it is asked for — in a starter,
    before a job id goes back. It is a shortcut and nothing else: a note that cannot be read,
    cannot be written, or no longer describes the file on disk costs a reread.
    """
    config = config or get_config()
    seen = fingerprint(path)
    note = _note_path(seen, config)
    remembered = _remembered(note, seen)
    if remembered is not None:
        return remembered
    log.info("Hashing %s (%d bytes): first sight of this file state", seen["path"], seen["size"])
    digest = content_hash(path)
    _write_note(note, {**seen, "sha256": digest})
    return digest


def audio_identity(path: Path | str, config: Config | None = None) -> dict[str, Any]:
    """What audio is, for cache purposes: its bytes, wherever the file happens to sit.

    The audio half of the rule at the top of this module, as one call — so two jobs keying
    off the same master agree about what it is, rather than each deciding for itself. Named
    for what it covers, because the other half of the rule is a different call: pointing this
    one at a camera master would read tens of gigabytes to answer a question ``fingerprint``
    answers with a stat.

    Audio this server wrote is hashed for real every time, never off a note. The note's own
    guard is a fingerprint, and a fingerprint can be fooled — a same-size rewrite in place, on
    a filesystem whose mtime is granular to two seconds, is the same file state by that
    reading and different audio in fact. Everywhere else that risk is the one this cache
    already ran (the master used to be fingerprinted outright, with no hash behind it), so the
    note leaves it exactly where it was. Under ``audio_dir`` it would be a new risk on the
    substrate every later job keys off, where a false hit attributes one concert's beats to
    another — and it buys nothing worth that, because these are the files this server sized
    itself and can afford to read.
    """
    config = config or get_config()
    resolved = Path(path)
    if resolved.resolve().is_relative_to(config.audio_dir.resolve()):
        return {"sha256": content_hash(resolved)}
    return {"sha256": known_hash(resolved, config)}


def _note_path(seen: Mapping[str, Any], config: Config) -> Path:
    """One file per file state, named after the fingerprint it remembers a hash for."""
    token = hashlib.sha256(
        json.dumps([seen["path"], seen["size"], seen["mtime_ns"]]).encode("utf-8")
    ).hexdigest()
    return config.identity_dir / f"{token}.json"


def _remembered(note: Path, seen: Mapping[str, Any]) -> str | None:
    """The hash this note carries, if it still describes the file that was just stat'd."""
    try:
        noted = json.loads(note.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Discarding an unreadable identity note: %s", note)
        _discard(note)
        return None
    if not isinstance(noted, dict) or not isinstance(noted.get("sha256"), str):
        log.warning("Discarding an identity note that is not one: %s", note)
        _discard(note)
        return None
    describes = all(noted.get(field) == seen[field] for field in ("path", "size", "mtime_ns"))
    # A note that no longer describes the file needs no discarding of its own: it is named
    # after the file state it answers for, so the reread it just forced overwrites it.
    return str(noted["sha256"]) if describes else None


def _write_note(note: Path, remembering: Mapping[str, Any]) -> None:
    """Write down what this file state hashed to. A memo that cannot be kept is not an error."""
    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(json.dumps(remembering, indent=2), encoding="utf-8")
    except OSError:
        log.warning("Could not remember the identity of %s", remembering["path"], exc_info=True)


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
