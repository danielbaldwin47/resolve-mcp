"""Returns too big for a reply, written where the agent can grep them instead.

The client caps a tool result at roughly 25k tokens, and a concert media pool or timeline
is far past that. Rather than truncate silently, a listing over its cap comes back capped
*and* says where the whole thing landed — the locked hybrid inline/disk return shape.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config
from .logging_config import get_logger
from .naming import timestamped_name

log = get_logger("spill")


def spill(label: str, payload: dict[str, Any], config: Config, fallback: str) -> str:
    """Write the whole reading to the listing directory and return the path."""
    target = config.listing_dir / timestamped_name(label, ".json", fallback)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Spilled the full %s reading to %s", fallback, target)
    return str(target)
