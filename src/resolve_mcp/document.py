"""Reading an agent-authored JSON file off disk — the one place JSON becomes a document.

The server never writes these files: authorship of both the cut and the titles stays with
Claude and the director. It reads one, hashes exactly the bytes it read, and reports what
it found. The hash is what ties a built or titled timeline back to the file state that
produced it, so it is taken here, on the same bytes that were parsed, and never
recomputed from the parsed object.

The split between raising and reporting is the same for every such file: a path that
cannot be read is a wrong *call* and raises, while a file that is there and is not JSON
is a validation *result* and comes back as the caller's own first rule.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from .errors import InvalidRequestError
from .findings import Finding

HASH_DIGEST_BYTES = 16
"""BLAKE2b digest length: 32 hex characters, short enough to read in a report."""


class LoadedDocument(NamedTuple):
    """A file as read: always a path and a hash, a document only if it parsed."""

    path: Path
    content_hash: str
    doc: Any  # the parsed document, or None when parse_error says why there is none
    parse_error: Finding | None


@dataclass(frozen=True)
class Preflight:
    """A file as read, and what the rules said about it — the shape every dry run has.

    The cut and the titles rule sets share nothing but this: the document they were run
    over travels with their findings, so the operation that follows is judged on and
    built from the same reading. Each rule set subclasses this to carry whatever else its
    apply needs; the pair and the severity split are defined once, here.
    """

    loaded: LoadedDocument
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]


def content_hash(data: bytes) -> str:
    """The BLAKE2b hash a report echoes for provenance."""
    return hashlib.blake2b(data, digest_size=HASH_DIGEST_BYTES).hexdigest()


def read_document(
    file: str,
    *,
    what: str,
    parse_failure: Callable[[str], Finding],
) -> LoadedDocument:
    """Read and parse ``file``. ``what`` names it in the message the agent reads."""
    path = Path(file).expanduser()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InvalidRequestError(
            cause=f"Could not read the {what} {str(path)!r}: {exc.strerror or exc}.",
            fix=f"Check the path. {what.capitalize()}s are authored by you, on disk; the "
            f"server never writes them.",
            detail={"file": str(path)},
        ) from exc

    digest = content_hash(data)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return LoadedDocument(path, digest, None, parse_failure(str(exc)))
    return LoadedDocument(path, digest, doc, None)


__all__ = [
    "HASH_DIGEST_BYTES",
    "LoadedDocument",
    "Preflight",
    "content_hash",
    "read_document",
]
