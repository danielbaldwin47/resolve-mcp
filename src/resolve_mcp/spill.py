"""Returns too big for a reply, written where the agent can grep them instead.

The client caps a tool result at roughly 25k tokens, and a concert media pool or timeline
is far past that. Rather than truncate silently, a listing over its cap comes back capped
*and* says where the whole thing landed — the locked hybrid inline/disk return shape.

``capped`` is the one place that shape is decided. Every listing that can outgrow a reply
goes through it, so ``truncated`` and ``spilled_to`` mean the same thing in every reply and
the file on disk is the same reply carrying all of it. The hand-written copies drifted
before this existed: the media listing's spilled file was missing the truncation keys every
other spilled file carried, and the job listing capped with a vocabulary of its own and
never spilled at all (#224).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .config import Config, get_config
from .logging_config import get_logger
from .naming import timestamped_name

log = get_logger("spill")


def capped(
    reply: dict[str, Any],
    *,
    key: str,
    whole: list[Any],
    limit: int,
    label: str,
    fallback: str,
    config: Config | None = None,
    counted: int | None = None,
    share: Callable[[list[Any], int], list[Any]] | None = None,
) -> dict[str, Any]:
    """``reply`` with ``key`` capped at ``limit``, and the whole of it on disk if it did not fit.

    ``counted`` is what the cap is measured against when that is not the length of ``whole``
    — a stack of tracks is capped on the shots inside it, not on how many tracks there are.
    ``share`` is how the budget is spent when a plain head slice is the wrong answer, for the
    same reason: filling one track before the next would hand back a stacked reference with
    every angle below the first one empty.
    """
    cap = max(int(limit), 0)
    against = len(whole) if counted is None else counted
    if against <= cap:
        return untruncated({**reply, key: whole})

    shown = (share or _head)(whole, cap)
    spilled = spill(label, untruncated({**reply, key: whole}), config or get_config(), fallback)
    return {**reply, key: shown, "truncated": True, "spilled_to": spilled}


def untruncated(reply: dict[str, Any]) -> dict[str, Any]:
    """A reply with nothing to cap: the same two keys, both saying so."""
    return {**reply, "truncated": False, "spilled_to": None}


def spill(label: str, payload: dict[str, Any], config: Config, fallback: str) -> str:
    """Write the whole reading to the listing directory and return the path."""
    target = config.listing_dir / timestamped_name(label, ".json", fallback)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Spilled the full %s reading to %s", fallback, target)
    return str(target)


def _head(items: list[Any], cap: int) -> list[Any]:
    return items[:cap]
