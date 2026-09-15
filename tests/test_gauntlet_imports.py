"""Every import in every module under `gauntlet/` still resolves.

`gauntlet/` is agent-owned recon code outside every other gate: mypy's `files` is
`src`/`tests`/`scripts` and pytest never collects the tree, so a rename in `src/`
leaves a dead import there for weeks (PR #262 found `ab_pack.py` importing a
symbol that had moved modules; a sibling, `recon/mid_p3r2_plan.py`, was dead the
same way). One parametrised case per file, so a failure names the file.

**Why it resolves imports instead of executing them.** These modules are scripts,
not libraries: the body *is* the work. Executing all 156 rewrites a tracked
receipt JSON on every run, and `recon/gpu_watch.py` alone shells out to
`nvidia-smi` eight times with 12-second sleeps between them — a fake-tier gate
cannot do that, and CI has no GPU. So each import statement is resolved instead:
the imported module is imported for real (`resolve_mcp`, stdlib, numpy — all
side-effect-free) and each `from X import name` is checked against it. That is
exactly the failure class the tree keeps growing, and it covers imports inside
functions too, which executing the module would not reach. A module that fails
to parse fails its case as well.
"""

from __future__ import annotations

import ast
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GAUNTLET = REPO_ROOT / "gauntlet"
# The `recon/*` modules that `import ab_pack` put this on `sys.path` themselves,
# a few lines before the import; mirror that so their import is resolvable here.
TOOLS = GAUNTLET / "tools"


class Target(NamedTuple):
    """One imported name: `from <module> import <symbol>`, or `import <module>`."""

    module: str
    symbol: str | None
    lineno: int

    def statement(self) -> str:
        if self.symbol is None:
            return f"import {self.module}"
        return f"from {self.module} import {self.symbol}"


@pytest.fixture(autouse=True, scope="module")
def _ab_pack_on_path() -> Iterator[None]:
    sys.path.insert(0, str(TOOLS))
    try:
        yield
    finally:
        sys.path.remove(str(TOOLS))


def gauntlet_modules() -> list[Path]:
    """Every `*.py` under `gauntlet/`, `__pycache__` aside, in a stable order."""
    return sorted(
        path
        for path in GAUNTLET.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )


def module_id(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def targets(tree: ast.AST) -> list[Target]:
    found: list[Target] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [Target(alias.name, None, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                # `gauntlet/` is not a package, so a relative import is itself dead.
                found.append(Target("." * node.level + (node.module or ""), None, node.lineno))
                continue
            found += [
                Target(node.module, None if alias.name == "*" else alias.name, node.lineno)
                for alias in node.names
            ]
    return found


def failure(target: Target) -> str | None:
    """The reason this import does not resolve, or None when it does."""
    try:
        module = importlib.import_module(target.module)
    except Exception as exc:  # noqa: BLE001 — any failure is the finding
        return f"{target.statement()} -> {type(exc).__name__}: {exc}"
    if target.symbol is None or hasattr(module, target.symbol):
        return None
    try:  # a `from package import submodule` that has not been imported yet
        importlib.import_module(f"{target.module}.{target.symbol}")
    except Exception:  # noqa: BLE001 — the name is simply not there
        return f"{target.statement()} -> {target.module} has no {target.symbol!r}"
    return None


def test_the_walk_finds_the_tree() -> None:
    """A walk that found nothing would leave every case below vacuously green."""
    assert len(gauntlet_modules()) > 100


@pytest.mark.parametrize("path", gauntlet_modules(), ids=module_id)
def test_gauntlet_module_imports_resolve(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    dead = [
        f"{module_id(path)}:{target.lineno} {reason}"
        for target in targets(tree)
        if (reason := failure(target)) is not None
    ]
    assert not dead, "dead imports:\n" + "\n".join(dead)
