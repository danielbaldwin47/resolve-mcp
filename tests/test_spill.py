"""One definition of a truncated reply — the cap, the two flags, and the file on disk.

Every listing that can outgrow a reply goes through ``capped``. These tests are about the
helper itself rather than any one listing: the five call sites each assert their own
vocabulary, but what a truncated reply *means* is decided here and nowhere else (#224).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.spill import capped, untruncated


def test_a_listing_inside_the_cap_stays_inline() -> None:
    reply = capped(
        {"bin": "Master"},
        key="clips",
        whole=[1, 2, 3],
        limit=10,
        label="Master",
        fallback="media-pool",
    )

    assert reply["clips"] == [1, 2, 3]
    assert reply["truncated"] is False
    assert reply["spilled_to"] is None
    assert reply["bin"] == "Master"


def test_a_listing_past_the_cap_is_capped_and_the_whole_of_it_goes_to_disk() -> None:
    reply = capped(
        {"bin": "Master", "count": 5},
        key="clips",
        whole=[1, 2, 3, 4, 5],
        limit=2,
        label="Master",
        fallback="media-pool",
    )

    assert reply["clips"] == [1, 2]
    assert reply["truncated"] is True
    assert reply["spilled_to"] is not None
    assert Path(reply["spilled_to"]).exists()


def test_the_spilled_file_is_the_same_reply_carrying_all_of_it() -> None:
    """The file is not a different shape: same keys, whole list, both flags saying so."""
    reply = capped(
        {"bin": "Master", "count": 5},
        key="clips",
        whole=[1, 2, 3, 4, 5],
        limit=2,
        label="Master",
        fallback="media-pool",
    )

    written = _read(reply["spilled_to"])

    assert written["clips"] == [1, 2, 3, 4, 5]
    assert written["truncated"] is False
    assert written["spilled_to"] is None
    assert written["bin"] == "Master"
    assert written["count"] == 5


def test_a_cap_of_zero_keeps_nothing_inline_and_still_spills() -> None:
    reply = capped(
        {}, key="items", whole=[1, 2], limit=0, label="none", fallback="listing"
    )

    assert reply["items"] == []
    assert reply["truncated"] is True
    assert _read(reply["spilled_to"])["items"] == [1, 2]


def test_a_negative_limit_reads_as_no_room_at_all() -> None:
    reply = capped(
        {}, key="items", whole=[1], limit=-5, label="none", fallback="listing"
    )

    assert reply["items"] == []
    assert reply["truncated"] is True


def test_an_empty_listing_is_never_truncated() -> None:
    reply = capped({}, key="items", whole=[], limit=0, label="none", fallback="listing")

    assert reply["items"] == []
    assert reply["truncated"] is False
    assert reply["spilled_to"] is None


def test_a_reply_can_cap_on_a_count_the_items_do_not_carry() -> None:
    """A stack of tracks is capped on the shots inside it, not on how many tracks there are."""
    tracks = [{"items": [1, 2, 3]}, {"items": [4, 5, 6]}]

    reply = capped(
        {},
        key="tracks",
        whole=tracks,
        limit=4,
        counted=6,
        label="stack",
        fallback="timeline",
    )

    assert reply["truncated"] is True
    assert _read(reply["spilled_to"])["tracks"] == tracks


def test_a_reply_can_share_the_cap_its_own_way() -> None:
    reply = capped(
        {},
        key="items",
        whole=[1, 2, 3, 4],
        limit=2,
        share=lambda items, cap: items[-cap:],
        label="tail",
        fallback="listing",
    )

    assert reply["items"] == [3, 4]


def test_capping_leaves_the_reply_it_was_handed_alone() -> None:
    given: dict[str, Any] = {"count": 3}

    capped(given, key="items", whole=[1, 2, 3], limit=1, label="x", fallback="listing")

    assert given == {"count": 3}


def test_untruncated_says_a_reply_had_nothing_to_cap() -> None:
    assert untruncated({"detail": "summary"}) == {
        "detail": "summary",
        "truncated": False,
        "spilled_to": None,
    }


def _read(path: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return loaded
