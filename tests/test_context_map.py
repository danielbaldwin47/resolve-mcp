"""CONTEXT.md is a complete, short map — asserted against the tree, not remembered.

The map lists every module under ``src/resolve_mcp/`` and every test file, names
each in backticks (``` `analysis/beats` ```, ``` `test_beat_grid` ```,
``` `fakes/core` ```), carries no issue-number history, and points at the area
docs under ``docs/context/``. Grep-based, fake tier: it reads the files as text.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "CONTEXT.md"
AREA_DOCS = ROOT / "docs" / "context"

MAX_LINES = 150


def tracked(*paths: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def map_name(path: str) -> str | None:
    """The backticked name the map is expected to carry for one tracked file.

    Packages (``__init__.py``) are not modules and get no row; non-Python files
    (fixture data) are described in prose, not mapped by name.
    """
    p = Path(path)
    if p.suffix != ".py" or p.name == "__init__.py":
        return None
    if p.parts[0] == "src":
        return "/".join(p.with_suffix("").parts[2:])
    return "/".join(p.with_suffix("").parts[1:])


@pytest.fixture(scope="module")
def context_text() -> str:
    return CONTEXT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def src_names() -> set[str]:
    names = {n for n in map(map_name, tracked("src")) if n}
    assert names, "git ls-files returned nothing — run from a checkout"
    return names


@pytest.fixture(scope="module")
def tracked_tests() -> set[str]:
    return {n for n in map(map_name, tracked("tests")) if n}


def table_rows(context_text: str) -> list[list[str]]:
    rows = [line for line in context_text.splitlines() if line.startswith("| `")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows]


def test_map_covers_every_module_and_test_file(
    context_text: str, src_names: set[str], tracked_tests: set[str]
) -> None:
    missing = sorted(n for n in src_names | tracked_tests if f"`{n}`" not in context_text)
    assert not missing, f"CONTEXT.md is missing: {missing}"


def test_table_is_exactly_the_tracked_modules_once_each(
    context_text: str, src_names: set[str]
) -> None:
    """A row for a moved or deleted module is a wrong map, not a short one; a
    module with two rows is two answers to "who owns X"."""
    first_cells = [row[0].strip("`") for row in table_rows(context_text)]
    assert len(first_cells) == len(set(first_cells)), "a module has two rows"
    assert set(first_cells) == src_names, (
        f"rows without a module: {sorted(set(first_cells) - src_names)}; "
        f"modules without a row: {sorted(src_names - set(first_cells))}"
    )


def test_map_names_no_test_file_that_does_not_exist(
    context_text: str, tracked_tests: set[str]
) -> None:
    named = set(re.findall(r"`((?:test_\w+|fakes/\w+))`", context_text))
    stale = sorted(n for n in named if n not in tracked_tests)
    assert not stale, f"CONTEXT.md names test files that are not tracked: {stale}"


def test_map_stays_short(context_text: str) -> None:
    lines = context_text.count("\n")
    assert lines <= MAX_LINES, f"CONTEXT.md is {lines} lines; the map's budget is {MAX_LINES}"


def test_map_carries_no_issue_numbers(context_text: str) -> None:
    hits = re.findall(r"#\d+", context_text)
    assert not hits, f"issue numbers belong in docs/context/, not the map: {hits}"


def test_map_links_every_area_doc(context_text: str) -> None:
    docs = sorted(p.name for p in AREA_DOCS.glob("*.md"))
    assert {"vocabulary.md", "analysis.md", "tests.md"} <= set(docs)
    unlinked = [d for d in docs if f"docs/context/{d}" not in context_text]
    assert not unlinked, f"area docs CONTEXT.md does not link: {unlinked}"


def test_every_map_row_names_a_test_and_a_seam(
    context_text: str, tracked_tests: set[str]
) -> None:
    for cells in table_rows(context_text):
        assert len(cells) == 4, cells
        assert re.fullmatch(r"`test_\w+`", cells[2]), cells
        assert cells[2].strip("`") in tracked_tests, cells
        assert cells[3] in {"fake", "pure", "live", "sub"}, cells
