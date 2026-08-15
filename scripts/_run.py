"""The one subprocess seam the scripts in this directory share.

Every script here learns what it knows through a ``Runner`` — a callable from
argv to stdout — so the fake tier drives it on fixtures of the ``git`` / ``gh``
output instead of a real repository. ``subprocess_runner`` is the real one.

Kept in its own module because the shape was copied once and drifted (the copy
lost ``encoding="utf-8"``, which is what decodes a UTF-8 ``git log`` on the
Windows box). Both scripts are run as modules from the repo root —
``uv run python -m scripts.prune_merged``, ``python3 -m scripts.review_gate`` —
so this import resolves the same way in CI, in a session, and under pytest.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

Runner = Callable[[Sequence[str]], str]


class CommandError(RuntimeError):
    """A command the Runner issued failed; the message carries argv and stderr."""


def subprocess_runner(argv: Sequence[str]) -> str:
    proc = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise CommandError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout
