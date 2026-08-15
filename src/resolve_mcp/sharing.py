"""Do it again if the other side of a poll had the file open.

Windows refuses to replace a file while another handle holds it, and refuses the read that
lands mid-replace. Every file this server polls has two handles on it whenever anything is
looking — a job record with ``get_job`` on one side and the worker saving its progress on the
other, a claim file with a rival deciding whether to wait on one side and the run holding it
refreshing on the other. The window is microseconds wide, so a short retry closes it.

Letting the error out is expensive at both ends and in every caller: on the writing side it
kills the worker mid-save and leaves a record saying ``running`` for ever; on the reading side
it reports a running job as gone, or a dead run's claim as one nobody may take. Neutral
ground, beside ``spill``, because nothing about the retry is job-shaped: it is a fact about
the filesystem, and a second copy of it anywhere would be a second policy to keep in step.
"""

from __future__ import annotations

import time
from collections.abc import Callable

ATTEMPTS = 20
PAUSE = 0.01
"""How long either side of a poll waits out the other's handle. See ``sharing``."""


def sharing[T](attempt: Callable[[], T]) -> T:
    """Run it, and run it again while Windows says another handle has the file."""
    for attempt_number in range(ATTEMPTS):
        try:
            return attempt()
        except PermissionError:
            if attempt_number == ATTEMPTS - 1:
                raise
            time.sleep(PAUSE)
    raise AssertionError("unreachable")  # pragma: no cover
