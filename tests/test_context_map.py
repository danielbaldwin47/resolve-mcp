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


def test_map_covers_every_module_and_test_file(context_text: str) -> None:
    expected = {n for n in map(map_name, tracked("src", "tests")) if n}
    assert expected, "git ls-files returned nothing — run from a checkout"
    missing = sorted(n for n in expected if f"`{n}`" not in context_text)
    assert not missing, f"CONTEXT.md is missing rows for: {missing}"


def test_map_names_no_file_that_does_not_exist(context_text: str) -> None:
    """A row for a module that was moved or deleted is a wrong map, not a short one."""
    live = {n for n in map(map_name, tracked("src", "tests")) if n}
    packages = "analysis|audio|cut|jobs|resolve|titles|tools|video|fakes"
    named = set(re.findall(rf"`((?:{packages})/\w+)`", context_text))
    named |= set(re.findall(r"`(test_\w+)`", context_text))
    stale = sorted(n for n in named if n not in live)
    assert not stale, f"CONTEXT.md names files that are not tracked: {stale}"


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


def test_every_map_row_names_a_test_and_a_seam(context_text: str) -> None:
    rows = [line for line in context_text.splitlines() if line.startswith("| `")]
    assert len(rows) > 100
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 4, row
        assert re.fullmatch(r"`test_\w+`", cells[2]), row
        assert cells[3] in {"fake", "pure", "live", "sub"}, row
