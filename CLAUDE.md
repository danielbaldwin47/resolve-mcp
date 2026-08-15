# resolve-mcp

An MCP server that lets an agent edit concert footage in DaVinci Resolve
Studio — the server measures; Claude decides. Map and vocabulary:
`CONTEXT.md`.

## Test seams

Before building a ticket, decide **which seam verifies it** and say so in the
PR. A ticket whose acceptance criteria cannot be checked at any seam is not
ready to build — that is the thing to resolve first. Spec #22 already names
each ticket's seam inline; keep that habit.

**1. Fake tier — `uv run pytest -m 'not live'` (the default).**
`tests/fakes/` substitutes the Resolve singleton at the connection manager —
the single seam. The fakes deliberately mimic the real API's quirks (getters
return `None`, `LoadProject` returns `None`, settings come back as strings,
`die_after` drops the handle mid-call), so everything that is a *decision*
verifies here: config and env overrides, loader path resolution, the
interpreter guard (platform-parameterised, so Windows paths are testable from
Linux), envelope and error shaping, `run_python` semantics, tool registration.
Pure functions (validation, time math) and workers on fixture audio live here
too. Tools are called directly, never over stdio.

**2. Live smoke — `uv run pytest -m live`.**
The only place the direct-attach path is real: needs Resolve Studio running
with a python.org interpreter; an autouse fixture skips every live test when
Resolve is unreachable. Sessions run on the live Windows 11 box with Resolve
Studio up, so run this tier yourself: an AC marked "(live smoke)" is yours to
execute, and a live pass means the tests **ran** — a run that skipped means
Resolve was unreachable, not that the tier passed. Record the result on the
ticket (hardware, interpreter, outcome); an unrecorded pass gets re-run. ACs
that need the human's hands — a specific project open, media only they have,
a click in the UI — go to the human: flag them **before opening the PR** so
they can run first, and list them under the ticket's `## Needs from you`.

**What no seam covers, and why it is the dangerous part:** whether
fusionscript accepts the attach on a given interpreter at all. ADR 0001: on a
uv-managed standalone interpreter the failure is a Windows access violation —
the process dies with nothing to catch, so no test can observe it; and
`uv sync` picks that interpreter silently, so every unit test passes while
attach dies. The pre-load interpreter guard exists because a crash that kills
the test runner is invisible to the test runner. Treat "all fakes green" as
proof of decisions, never of the attach.

**Log a line for every connection state change** (attach, reconnect, handle
death) — a live failure outside your session (a human-run AC, real MCP use)
gets diagnosed from the log or not at all. (forest-shell #81: a silent lifecycle left a
cannot-unlock bug with two candidate causes for a week.)

Why the seam rule exists: forest-shell ran seven tickets green against its
unit tier alone; the first pass on a real compositor produced eight bugs at
once.

## Session workflow

Work from ticket #N happens on branch `issue-<N>`; non-ticket changes (doc
tweaks, tooling fixes) take a branch named for the change (e.g.
`fix-context-guard`). Background sessions inherit whatever branch the
launcher was on — verify or create the right branch before the first write.
(And launcher-side: a background launch that shows no assistant output
produced no work; relaunch rather than assume.)

1. **Push the branch after the first commit** — pushed work survives a lost
   session. Done when the branch exists on origin. Do not open a PR yet: a
   pre-review PR has no valid `Review:` line, so it is born gate-red and
   forces a second full review after fixes land just to satisfy the check.
2. **Review on the branch, before the PR exists.** Review weight follows
   what the diff touches, not how simple it looks (a three-line log fix
   here hid a mislabelled recovery path that only the review caught):
   - **Anything executable** — `src/`, `tests/`, `.claude/hooks/`, workflow
     YAML — gets `/code-review` (the two-axis mattpocock skill — Standards
     and Spec as parallel sub-agents). Hooks and workflows count: they are
     config that executes, and a broken gate fails silently for weeks.
   - **Pure prose** — docs, README, CLAUDE.md — gets one lightweight inline
     pass, and the line reads `Review: clean — prose only, single-pass`.
3. **Fix findings; re-check the fix diff only** — a focused pass over what
   changed, not a second full review. One full review per PR is the
   default; a fresh full pass is only for fixes large enough to be a new
   diff.
4. **Open the PR with the review record already in the body**: findings and
   their resolutions (if any) first, ending with the `Review:` line. The
   gate reads only the **last** `Review:` line in the body and passes only
   `Review: clean` (optionally followed by a summary) — any other last line
   blocks merge — so a PR opened this way is green from its first gate run.
   If a PR gains commits after opening (human feedback, CI failures),
   re-review the new diff and append a fresh `Review:` line; the earlier
   lines stay above it as the record.
5. **Merge through the PR** — everything reaches `main` through a PR, never
   a direct commit.
6. **After a stacked PR merges, verify its commits reached main by
   content.** PRs here land both ways — merge commits and squashes — so
   check `git log origin/main` for either the `Merge pull request #<n>`
   commit or a squashed subject ending `(#<n>)`, and confirm the files
   landed. `git merge-base --is-ancestor <head-sha> origin/main` proves a
   merge-commit PR, but exits 1 for every squashed one — a failing check
   alone proves nothing (#45: squashed PR #109 read as unlanded). The risk
   the check exists for is real either way: a PR that merges into a
   just-consumed parent branch reads MERGED while its commits never reach
   main. Then prune the residue: `uv run python scripts/prune_merged.py`
   lists the merged remote branches, local branches and worktrees it would
   drop; `--apply` drops them (locked, dirty, open-PR and unmerged-commit
   items are never touched).
7. **If the PR was squashed, continue its ticket on a fresh branch.** The
   old branch then sits on history main no longer shares; a second PR from
   it drags the already-merged commits back in. Branch from `origin/main`
   and cherry-pick only the new commits — never force-push. (#45: after
   #109's squash, the continuation landed cleanly from the fresh branch
   `issue-45-entries-2-3` as PR #111. After a merge-commit PR, continuing
   on the same branch is safe.)
8. **Close the ticket with the PR link**; name any unrun live ACs in the
   close comment.

When resolving merge conflicts, grep every conflicted file for `<<<<<<<`
before committing — especially markdown: CI never reads it, and a leftover
marker has reached `main` that way. mypy strict forbids implicit re-export:
import a symbol from its defining module (`from resolve_mcp.ffmpeg import
Runner`), never as `sibling.Runner` through a module that merely imports it.

Every implementation comment on a ticket (close or status) ends with a
`## Needs from you` section as its **last** section, listing each item that
requires the human — decisions to make, live ACs to run, installs — even when
already discussed above. If nothing is needed, omit the section; its absence
is the signal that the ticket asks nothing of you.

CI (`.github/workflows/ci.yml`) runs the fake tier, mypy strict, and ruff on
every PR; `review-gate.yml` blocks merge until the PR body's `Review:` line
reads clean. Both are required status checks on `main`.

## Context discipline

One rule: nothing enters the session unless the session is about to act on
it. Noisy commands (`pytest`, `mypy`, `ruff`) redirect to a scratch file and
the decisive line comes back via the Grep tool. Two plain calls — the
worktree guard refuses compound commands (`;`-chains, `$(...)`, env-var
paths), and Claude Code (≥2.1.232) forces a manual approval prompt on any
compound that pairs a directory change with output redirection — `cd`
under Git Bash, `Set-Location` under PowerShell, so switching shells is no
way out. The guard fires before it reads the target: an absolute log path
does not clear it, an allow-rule does not either, and auto mode is
explicitly barred from deciding. So the redirect is one bare command, run
from the session's own cwd — a worktree session already starts in its
worktree — to a gitignored repo-local log:

    uv run pytest -m 'not live' > pytest.scratch.log 2>&1

then Grep `FAILED|passed|error` in `pytest.scratch.log` (`*.scratch.log` is
gitignored; never commit a log). Never `| tail` — a tail caps one run and
runs repeat. Delegate exploration to
a read-only subagent; Read only what you will edit, ranged (grep first) on
big files; do not re-read a file after editing it. The hooks in
`.claude/hooks/` enforce the cat/tail rules, whole-file re-reads, and
whole-file reads of code/config files over 400 lines (markdown exempt) — a
block from them is the rule firing, not an obstacle to route around; the
block message names the fix.

The same scratch-file rule covers `gh` — issue bodies, comment threads,
and PR diffs were the biggest single results in past sessions:
`gh issue view <n> --json body -q .body > issue.scratch.log`, then Grep
back the section you need.

**Orient from `CONTEXT.md` first** — the repo map answers "which module
owns X, where is the seam, which test file covers Y"; explores are for
what a map can't hold (exact signatures, current behaviour). A PR that
adds, moves, or deletes a module updates the map in the same PR.

Long multi-PR sweeps (merge trains, cross-PR audits) shard per-PR into
subagents; the orchestrating session keeps receipts, not diffs — past
sweeps that inlined everything ended at 2× the usable context budget.

## Compute device

**GPU-first.** Every compute path runs on the card wherever a GPU path
exists; the CPU is a fallback the log and the job record name at WARNING.
No path is CPU by policy any more: #245 moved the last two (beats and
applause) onto CUDA torch. The per-path table — which paths have a GPU
path, which have none worth wiring, and how each reports a fallback — is
`docs/reference/compute-device-inventory.md`. Read it before you launch
a separation, transcription or beat-grid job, before you touch a compute
path, and when you add one — a new path takes its own row in the same PR.

A CPU reading where the table says a GPU path exists is a **broken install,
not a slow box**: stop, fix it (or hand it to the human under `## Needs
from you`), then run. Waiting it out is the G10 failure (#202) — the
separator ran hours on the CPU under a WARNING nobody acted on. The
server now refuses a `+cpu` separator outright
(`RESOLVE_MCP_SEPARATOR_ALLOW_CPU`, README) and the live separator test
is red on a CPU device; treat either as the install fix below, never as
a reason to set the override on this box. Check before a live run:

    audio-separator --env_info 2>&1 | grep -E "PyTorch|ONNX"

The separator's own torch decides its device — the server's config cannot
move a `+cpu` build onto the card, only refuse it. On the live
box `audio-separator` resolves to the system Python 3.12
(`%LOCALAPPDATA%\Programs\Python\Python312`), separate from the repo
venv; its torch must be a `+cu` build. Restore one (driver is CUDA 13.x)
with:

    py -3.12 -m pip install "torch==2.13.0+cu130" "torchvision==0.28.0+cu130" --index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.org/simple

`pip index versions torch --index-url .../cu130` lists the versions when
the pin moves. Live-tier separation tests are only a pass on a `+cu` build.

## Gotchas

Facts rediscovered across sessions, promoted from session memory. Each one
cost a session real time at least twice before landing here; a gotcha that
later gets a real fix gets its line removed.

- **R1 — Worktree sessions: pin every review and diff to `origin/main`.**
  The local `main` checkout lags while other worktrees merge; a diff
  against local `main` sweeps in commits that are not yours. (Session
  memory `worktree-main-is-stale`, 2026-08.)
- **R2 — Worktree pytest runs the main checkout's source.** The editable
  install points at the main working copy, so a new test in a worktree
  fails against old code with no import error to warn you. Reinstall in
  the worktree (`uv sync`) before trusting a red run. (Session memory
  `worktree-tests-hit-main-checkout`, 2026-08.)
- **R3 — `ruff format` is not a gate.** CI (`ci.yml`) runs `ruff check`
  only; `ruff format --check` fails repo-wide by design. Don't "fix"
  formatting the repo never enforced. (Session memory
  `ruff-format-is-not-a-gate`, 2026-08.)

## Doc maintenance

A dated `/writing-for-agents` pass over CLAUDE.md, CONTEXT.md and `docs/`
runs periodically (last: 2026-08-10, CLAUDE.md and CONTEXT.md only);
session-memory facts that prove repo-general over multiple sessions
graduate into this file and the memory note becomes history. Verified
against mattpocock-skills 1.2.3.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `danielbaldwin47/resolve-mcp`, driven via
the `gh` CLI. For issue CRUD **and the wayfinder map operations** — frontier
query, claim, blocking edges, resolve — see `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` (repo map + vocabulary) and `docs/adr/` at
the repo root. See `docs/agents/domain.md`.

### The concert pillar

P3: the style-driven concert cut. The director's three inputs, session-start
analysis prep, song-by-song planning, the mandatory `correlate_timeline`
self-review, the cut report and review-round conventions:
`docs/agents/concert.md`.

### The rough-cut pillar

P4: transcript-driven A-roll assembly. The per-project brief and b-roll
catalog the agent owns, the assembly loop, the `virtual_transcript`
self-review and the cut report: `docs/agents/rough-cut.md`.

### The style layer

`styles/` — profiles and angle sidecars, agent-authored and never touched by
server code. Provenance tags, sidecar format, and how a corpus pass is run:
`docs/agents/style-layer.md`.
