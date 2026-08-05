"""Names the server hands out: spilled files, and the ``<base> v<N>`` timeline versions."""

from __future__ import annotations

from datetime import datetime

import pytest

from resolve_mcp.naming import (
    latest_version,
    next_version_name,
    timestamped_name,
    version_number,
)

WHEN = datetime(2026, 8, 5, 14, 30, 5)


# --- written files ----------------------------------------------------------------------


def test_a_label_becomes_a_timestamped_filename() -> None:
    name = timestamped_name("sunset set", ".drp", "project", WHEN)

    assert name == "sunset-set-20260805-143005.drp"


def test_a_label_that_slugs_to_nothing_falls_back() -> None:
    assert timestamped_name("///", ".json", "media-pool", WHEN) == "media-pool-20260805-143005.json"


# --- timeline versions ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("sunset-set v1", 1, id="first-version"),
        pytest.param("sunset-set v12", 12, id="two-digits"),
        pytest.param("sunset-set v03", 3, id="zero-padded"),
        pytest.param("sunset-set", None, id="the-base-alone"),
        pytest.param("sunset-set v2 copy", None, id="trailing-text"),
        pytest.param("sunset-set  v2", None, id="two-spaces"),
        pytest.param("sunset-set v", None, id="no-number"),
        pytest.param("other-set v2", None, id="another-base"),
        pytest.param("sunset-set v2 v3", None, id="two-suffixes"),
    ],
)
def test_only_an_exact_version_name_carries_a_version(name: str, expected: int | None) -> None:
    assert version_number("sunset-set", name) == expected


def test_a_base_that_is_a_prefix_of_another_does_not_claim_its_versions() -> None:
    assert version_number("sunset", "sunset-set v2") is None


def test_a_base_with_regex_characters_is_matched_literally() -> None:
    assert version_number("set (b).1", "set (b).1 v2") == 2
    assert version_number("set (b).1", "set (b)x1 v2") is None


def test_the_latest_version_is_the_highest_not_the_count() -> None:
    """A deleted v2 must not hand v3's number out a second time."""
    assert latest_version("sunset-set", ["sunset-set v1", "sunset-set v3"]) == 3


def test_a_base_nobody_has_built_yet_is_at_version_zero() -> None:
    assert latest_version("sunset-set", ["holiday-gig v4", "sunset-set"]) == 0


def test_the_next_version_follows_the_highest_that_exists() -> None:
    existing = ["sunset-set v1", "sunset-set v2", "holiday-gig v9", "sunset-set draft"]

    assert next_version_name("sunset-set", existing) == "sunset-set v3"


def test_the_first_build_of_a_base_is_v1() -> None:
    assert next_version_name("sunset-set", []) == "sunset-set v1"
