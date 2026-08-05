"""Reading a titles file off disk.

The mechanics are shared with the cut file (:mod:`resolve_mcp.document`). What is
titles-specific is which rule a parse failure belongs to: T1 is "JSON parses", so an
unparseable file is a T1 finding rather than an exception.
"""

from __future__ import annotations

from ..document import LoadedDocument, read_document
from .validate import parse_failure_finding

LoadedTitles = LoadedDocument
"""A titles file as read. The shape is shared; the name is what the apply reports on."""


def read_titles_file(titles_file: str) -> LoadedTitles:
    """Read and parse ``titles_file``, reporting an unparseable one as T1."""
    return read_document(titles_file, what="titles file", parse_failure=parse_failure_finding)


__all__ = ["LoadedTitles", "read_titles_file"]
