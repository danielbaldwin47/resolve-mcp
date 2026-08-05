# resolve-mcp

## Test seams

Before building a ticket, decide **which seam verifies it** and say so in the
PR. A ticket whose acceptance criteria cannot be checked at any seam is not
ready to build — that is the thing to resolve first. Spec #22 already names
each ticket's seam inline; keep that habit.

**1. Fake tier — `uv run pytest -m 'not live'` (the default).**
`tests/fakes.py` substitutes the Resolve singleton at the connection manager —
the single seam. The fakes deliberately mimic the real API's quirks (getters
return `None`, `LoadProject` returns `None`, settings come back as strings,
`die_after` drops the handle mid-call), so everything that is a *decision*
verifies here: config and env overrides, loader path resolution, the
interpreter guard (platform-parameterised, so Windows paths are testable from
Linux), envelope and error shaping, `run_python` semantics, tool registration.
Pure functions (validation, time math) and workers on fixture audio live here
too. Tools are called directly, never over stdio.

**2. Live smoke — `uv run pytest -m live`.**
The only place the direct-attach path is real: needs Resolve Studio running on
the Windows 11 box with a python.org interpreter; an autouse fixture skips
when unreachable. Any AC marked "(live smoke)" lands here — on the machine
agents do not develop on. So a ticket with live ACs closes when the fake-tier
work is done, with a comment naming exactly which ACs are unrun and why; the
live pass happens when a human is at that machine, and its result goes on the
ticket (hardware, interpreter, outcome), because an unrecorded pass gets re-run.

**What no seam covers, and why it is the dangerous part:** whether
fusionscript accepts the attach on a given interpreter at all. ADR 0001: on a
uv-managed standalone interpreter the failure is a Windows access violation —
the process dies with nothing to catch, so no test can observe it; and
`uv sync` picks that interpreter silently, so every unit test passes while
attach dies. The pre-load interpreter guard exists because a crash that kills
the test runner is invisible to the test runner. Treat "all fakes green" as
proof of decisions, never of the attach.

**Log a line for every connection state change** (attach, reconnect, handle
death) — a live failure on a machine you are not at gets diagnosed from the
log or not at all. (forest-shell #81: a silent lifecycle left a
cannot-unlock bug with two candidate causes for a week.)

Why the seam rule exists: forest-shell ran seven tickets green against its
unit tier alone; the first pass on a real compositor produced eight bugs at
once. The same class lives here between the fakes and the attach.

## Session workflow

Work from ticket #N happens on branch `issue-<N>`: push early, open a draft
PR. Review weight follows what the diff touches, not how simple it looks —
"looks simple" is self-assessed by the same author who made the mistake (a
three-line log fix here hid a mislabelled recovery path that only the review
caught):

- **Anything executable** — `src/`, `tests/`, `.claude/hooks/`, workflow
  YAML — gets `/code-review` (the two-axis mattpocock skill — Standards and
  Spec as parallel sub-agents). Hooks and workflows count: they are config
  that executes, and a broken gate fails silently for weeks.
- **Pure prose** — docs, README, CLAUDE.md — gets one lightweight inline
  pass, and the line reads `Review: clean — prose only, single-pass`.

Either way, append a `Review:` line to the PR body — `Review: clean`, or
`Review:` followed by the findings written out. "findings held" with nothing
above it is a contentless token; the review gate rejects it.
Everything reaches `main` through a PR — never commit to `main` directly.
After a stacked PR merges, verify its head is an ancestor of `origin/main`
(`git merge-base --is-ancestor`) — a PR that merges into a just-consumed
parent branch reads MERGED while its commits never reach main.
Close the ticket with the PR link when the work is complete; name any unrun
live ACs in the close comment.

CI (`.github/workflows/ci.yml`) runs the fake tier, mypy strict, and ruff on
every PR; `review-gate.yml` blocks merge until the PR body's `Review:` line
reads clean. Both are required status checks on `main`.

## Context discipline

One rule: nothing enters the session unless the session is about to act on
it. Noisy commands (`pytest`, `mypy`, `ruff`) redirect to a scratch file and
grep the decisive line back:

    log=$(mktemp); uv run pytest -m 'not live' >"$log" 2>&1; grep -E 'FAILED|passed|error' "$log"

Never `| tail` — a tail caps one run and runs repeat. Delegate exploration to
a read-only subagent; Read only what you will edit, ranged (grep first) on
big files; do not re-read a file after editing it. The hooks in
`.claude/hooks/` enforce the cat/tail rules and whole-file re-reads — a block
from them is the rule firing, not an obstacle to route around.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `danielbaldwin47/resolve-mcp`, driven via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
