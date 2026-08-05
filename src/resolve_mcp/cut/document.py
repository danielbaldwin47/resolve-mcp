"""Reading a cut file off disk — the one place JSON becomes a document.

The server never writes cut files: authorship stays with Claude and the director. It
reads one, hashes exactly the bytes it read, and reports what it found. The hash is what
ties a built timeline back to the cut state that produced it, so it is taken here, on the
same bytes that were parsed, and never recomputed from the parsed object.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, NamedTuple

from ..errors import InvalidRequestError
from .validate import Finding, parse_failure_finding

HASH_DIGEST_BYTES = 16
"""BLAKE2b digest length: 32 hex characters, short enough to read in a build report."""


class LoadedCut(NamedTuple):
    """A cut file as read: always a path and a hash, a document only if it parsed."""

    path: Path
    content_hash: str
    doc: Any  # the parsed document, or None when parse_error says why there is none
    parse_error: Finding | None


def content_hash(data: bytes) -> str:
    """The BLAKE2b hash a build report echoes for provenance."""
    return hashlib.blake2b(data, digest_size=HASH_DIGEST_BYTES).hexdigest()


def read_cut_file(cut_file: str) -> LoadedCut:
    """Read and parse ``cut_file``.

    A file that is missing or unreadable raises: the call itself was wrong, and there is
    nothing to report findings about. A file that is there but is not JSON comes back as
    an E1 finding, because that *is* a validation result — rule E1 is "JSON parses".
    """
    path = Path(cut_file).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InvalidRequestError(
            cause=f"Could not read the cut file {str(path)!r}: {exc.strerror or exc}.",
            fix="Check the path. Cut files are authored by you, on disk; the server "
            "never writes them.",
            detail={"cut_file": str(path)},
        ) from exc

    digest = content_hash(data)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return LoadedCut(path, digest, None, parse_failure_finding(str(exc)))
    return LoadedCut(path, digest, doc, None)


__all__ = ["HASH_DIGEST_BYTES", "LoadedCut", "content_hash", "read_cut_file"]
