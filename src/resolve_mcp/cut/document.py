"""Reading a cut file off disk.

The mechanics — read the bytes, hash exactly those bytes, parse or report why not — are
shared with the titles file and live in :mod:`resolve_mcp.document`. What is cut-specific
is which rule a parse failure belongs to: E1 is "JSON parses", so an unparseable file is
an E1 finding rather than an exception, while a path that cannot be read at all raises,
because the call itself was wrong and there is nothing to report findings about.
"""

from __future__ import annotations

from ..document import HASH_DIGEST_BYTES, LoadedDocument, content_hash, read_document
from .validate import parse_failure_finding

LoadedCut = LoadedDocument
"""A cut file as read. The shape is shared; the name is what the build reports on."""


def read_cut_file(cut_file: str) -> LoadedCut:
    """Read and parse ``cut_file``, reporting an unparseable one as E1."""
    return read_document(cut_file, what="cut file", parse_failure=parse_failure_finding)


__all__ = ["HASH_DIGEST_BYTES", "LoadedCut", "content_hash", "read_cut_file"]
