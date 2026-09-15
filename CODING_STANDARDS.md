# Coding standards

The rules the `/code-review` Standards axis reads. Each retires a finding
that recurred across the last eight review records (retro 2026-09-15); a
finding that recurs again becomes a rule here in the PR that fixes it.

1. **Names say what the thing is here.** `offline` means missing media; a
   helper that returns a failure string is `sha_failure`, not
   `reviewed_commit`. Retires: mysterious names (five PRs).
2. **One source of truth per fact.** A second copy of a `Runner`, an env
   dict or a comprehension drifts (a copy lost `encoding="utf-8"`): extract,
   then import. Retires: drifted copies (five PRs).
3. **A rename greps the old name everywhere** — wire keys, docstrings,
   `docs/`, `gauntlet/`, saved prompts (#260 left the wire key `opened`).
   Retires: half-renames.
4. **A behaviour change updates the doc that describes it in the same
   diff** — the `CONTEXT.md` row, the area doc under `docs/context/`, the
   docstring, `gauntlet/HANDOFF.md`. Retires: stale docs (four PRs).
5. **The Spec reviewer runs the fake tier before ruling** — in a worktree
   `uv sync` first (Gotcha R2), then
   `uv run pytest -m 'not live' > pytest.scratch.log 2>&1` and Grep the
   log. "Spec, not verified" is not a verdict. Retires: unverified Spec
   reports (three PRs).
6. **A guard regex ships with a pass/block table row** in its test file
   (`tests/test_context_guard.py`, `tests/test_read_guard.py`); a bypass
   found in review becomes a row before the fix lands. Retires: untabled
   bypasses.
7. **Log every connection state change** — attach, reconnect, handle death.
   A live failure outside your session (a human-run AC, real MCP use) is
   diagnosed from the log or not at all. Retires: silent lifecycles.
8. **Import a symbol from its defining module** —
   `from resolve_mcp.ffmpeg import Runner`, never `sibling.Runner` through a
   module that merely imports it; mypy strict forbids implicit re-export.
   Retires: re-export errors at the mypy gate.
