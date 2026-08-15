# resolve-mcp

An MCP server that lets an agent edit concert footage in DaVinci Resolve
Studio — the server measures; Claude decides. Map: `CONTEXT.md`;
vocabulary: `docs/context/vocabulary.md`.

## Test seams

Before building a ticket, decide **which seam verifies it** and say so in the
PR. A ticket whose acceptance criteria cannot be checked at any seam is not
ready to build — that is the thing to resolve first. Every AC line ends
with its seam — `(fake tier)`, `(live smoke)`, or `(human)`.

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
**ticket** (hardware, interpreter, outcome — the PR may duplicate it, never
replace it); an unrecorded pass gets re-run. ACs that need the human's hands
— a specific project open, media only they have, a click in the UI — go to
the human: flag them **before opening the PR** so they can run first, and
list them under the ticket's `## Needs from you`.

Running it: open project `mcp-tests-zinc` (a copy of the client media —
the suite deletes timelines, so never the client project); **one
`pytest -m live` at a time across all sessions** — every run attaches to
the single Resolve instance, so schedule the live *step*, not the whole
ticket; scratch projects the run creates cannot be deleted through the API
(`DeleteProject` returns `False` on this box) — list them for hand
deletion under `## Needs from you`; `-m live -k real_separator` needs no
Resolve (only `audio-separator` on PATH) and is the one seam that proves
stem labels; a leftover timeline gets deleted in a later pass, after the
switch away from it has settled — switch-then-delete in one pass crashed
Resolve mid-autosave.

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
gets diagnosed from the log or not at all (forest-shell #81: a silent
lifecycle hid a bug's cause for a week). The seam rule exists because a
sister repo ran seven tickets green on fakes alone and met eight bugs on the
first real pass.

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
   - **Pure prose** — docs, README, CLAUDE.md — is written and reviewed
     under `/writing-for-agents` (load the skill before the first edit;
     the pass checks pointers, hierarchy, leading words, negation,
     pruning), and the line reads `Review: clean @<sha> — prose only,
     /writing-for-agents pass`.
3. **Fix findings; re-check the fix diff only** — a focused pass over what
   changed, not a second full review. One full review per PR is the
   default; a fresh full pass is only for fixes large enough to be a new
   diff.
4. **Open the PR with the review record already in the body**: findings and
   their resolutions (if any) first, ending with the `Review:` line. The
   line names the commit you reviewed — `Review: clean @<sha>` (the short
   sha, `git rev-parse --short HEAD` on the reviewed branch), optionally
   followed by a summary. The gate reads only the **last** `Review:` line
   outside a fenced code block (leading `-`, `>` and `**` are tolerated) and
   passes only when that sha is the PR head, so a PR opened this way is
   green from its first gate run. Any commit pushed after the line — human
   feedback, a CI fix, a merge from `origin/main` — reddens the gate until
   you re-review the new diff and append a fresh `Review: clean @<newsha>`;
   the earlier lines stay above it as the record.
5. **Merge through the PR** — everything reaches `main` through a PR, never
   a direct commit.
6. **After a stacked PR merges, verify its commits reached main by
   content** — `git log origin/main` shows `Merge pull request #<n>` (the
   default here, `.claude/dispatch.json`) or a squashed subject ending
   `(#<n>)`, and the files are there. `merge-base --is-ancestor` exits 1
   for every squashed PR, so a failing check alone proves nothing. The risk
   is real either way: a PR merged into a just-consumed parent branch reads
   MERGED while its commits never reach main. Then prune the residue:
   `uv run python scripts/prune_merged.py` lists the merged remote
   branches, local branches and worktrees it would drop; `--apply` drops
   them (locked, dirty, open-PR and unmerged-commit items are never
   touched).
7. **If the PR was squashed, continue on a fresh branch** from
   `origin/main`, cherry-picking only the new commits — never force-push;
   the old branch would drag merged commits back in. After a merge-commit
   PR the same branch is safe.
8. **Close the ticket with a comment** — PR link, what landed, the live
   record, any unrun live ACs. The live record's home is the **ticket**; the
   PR body may repeat it, and a record that lives only on the PR is lost to
   anyone reading the ticket (#219's live pass survives only on PR #243).
   Never a bare close: a ticket the PR auto-closed still gets the comment
   (#167–#178 were bulk-closed silent and a reader has no outcome), and
   `context-guard.py` blocks a `gh issue close` that carries no
   `--comment`.

When resolving merge conflicts, grep every conflicted file for `<<<<<<<`
before committing — especially markdown: CI never reads it, and a leftover
marker has reached `main` that way. mypy strict forbids implicit re-export:
import a symbol from its defining module (`from resolve_mcp.ffmpeg import
Runner`), never as `sibling.Runner` through a module that merely imports it.

Every implementation comment on a ticket (close or status) ends with a
`## Needs from you` section as its **last** section, listing each item that
requires the human — decisions to make, live ACs to run, installs, scratch
projects to delete — even when already discussed above. "Merge PR #n" is
not an item: an open PR already says it, and fourteen identical
"merge it" sections buried the two real asks. If nothing is needed, omit
the section; its absence is the signal that the ticket asks nothing of you.

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
    uv run mypy src tests > mypy.scratch.log 2>&1
    uv run ruff check src tests > ruff.scratch.log 2>&1
    gh issue view <n> --json body -q .body > issue.scratch.log
    gh pr diff <n> > pr.scratch.log
    git merge origin/main --no-edit > merge.scratch.log 2>&1

then Grep `FAILED|passed|error` (or the section you need) in the log
(`*.scratch.log` is gitignored; never commit a log). Every one of these is
the whole command: no `;`, no `&&`, no `$(mktemp)`, no `for`/`while`, no
`sleep N;` prefix (the harness blocks it — 154 guard rejections and 25
sleep blocks were the largest wasted-turn class in the transcripts). Never
`| tail` — a tail caps one run and runs repeat. Waiting on CI is one
`Monitor` (or one `gh pr checks <n>` after a wakeup), not a `--watch` or a
sleep loop. Delegate exploration to a read-only subagent; Read only what
you will edit, ranged (grep first) on big files; do not re-read a file
after editing it. The hooks in `.claude/hooks/` enforce this on both shell
tools and every reader: a `pytest|mypy|ruff` run or `gh … view|diff` that
does not land in a file, and any whole-file dump of a guarded file (`cat`,
`sed -n p`, uncounted `head`/`tail`, `Get-Content`, `type`, readers fed by
`ls`/`find`/`xargs` or a loop) are blocked; ranged reads pass (#249). They
also block whole-file re-reads and whole-file Reads of code, config and
markdown files over 400 lines (CLAUDE.md exempt). A block from them is the
rule firing, not an obstacle to route around; the block message names the
fix, and `sed -n` or `head` on the same file is the same cost, not a way
through.

The same scratch-file rule covers `gh` — issue bodies, comment threads,
and PR diffs were the biggest single results in past sessions; a `--json`
field filter that does not pull the body (`-q .title`) is fine.

**Orient from `CONTEXT.md` first** — the map is one table, ~150 lines:
which module owns X, which test covers it, at which seam. The narrative
behind each area (vocabulary, `analysis/`, `resolve/`, the test map, …)
lives in `docs/context/<area>.md`: Grep it, or Read it ranged, only when
you are about to work in that area — never whole (the read guard now
blocks whole-file Reads of any markdown over 400 lines except this file).
Explores are for what a map can't hold (exact signatures, current
behaviour). A PR that adds, moves, or deletes a module or test file
updates the map in the same PR — `tests/test_context_map.py` fails
otherwise — and area narrative goes to the area doc, not the map.

**Session budget.** Context starts at ~44k tokens and grows ~500 per
message; a ticket that cannot land in ~150 tool calls does not fit one
session — split it (file the second ticket, then continue) rather than push
on. Half the growth is your own Edit/Write payloads and thinking, which no
result-side rule can shrink; a ticket that adds three or more new modules
delegates each module's implementation to a subagent in the same worktree
and keeps only receipts (commit sha, gate lines) here, the way review is
already delegated (#248 measures this per session).

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
  against local `main` sweeps in commits that are not yours.
- **R2 — Worktree pytest runs the main checkout's source.** The editable
  install points at the main working copy, so a new test in a worktree
  fails against old code with no import error to warn you. Run `uv sync`
  in the worktree first, then trust a red run (21 sessions paid a turn
  for this — the sync is the first command after `EnterWorktree`).
- **R3 — `ruff format` is not a gate.** CI (`ci.yml`) runs `ruff check`
  only; `ruff format --check` fails repo-wide by design. Leave formatting
  the repo never enforced.
- **R4 — Merging is the human's.** Auto mode denies `gh pr merge` (nine
  sessions rediscovered it); the session's last act is the PR open plus the
  ticket comment, and the human merges.

## Doc maintenance

Every edit to CLAUDE.md, CONTEXT.md or `docs/agents/` goes through
`/writing-for-agents` (step 2 above); a dated full pass over all three
runs periodically (last: 2026-08-15, targeted; 2026-08-10, full on
CLAUDE.md and CONTEXT.md). Session-memory facts that prove repo-general
over multiple sessions graduate into this file and the memory note is
deleted. Verified against mattpocock-skills 1.2.3.

## Agent skills

### Issue tracker

Issues live as GitHub issues on `danielbaldwin47/resolve-mcp`, driven via
the `gh` CLI. For issue CRUD, native blocking edges, and — when a wayfinder
map is open — frontier query, claim, resolve, see
`docs/agents/issue-tracker.md`. Ticket splitting: `/to-tickets`.

### Triage labels

The five canonical triage roles, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` (the map), `docs/context/` (vocabulary + area narrative) and `docs/adr/` at
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
