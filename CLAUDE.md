# resolve-mcp

An MCP server that lets an agent edit concert footage in DaVinci Resolve
Studio — the server measures; Claude decides. Map: `CONTEXT.md`; vocabulary:
`docs/context/vocabulary.md`; code rules: `CODING_STANDARDS.md`.

## Test seams

Before building a ticket, decide **which seam verifies it** and say so in the
PR; a ticket no seam can check is not ready to build — resolve that first.
Every AC line ends with its seam: `(fake tier)`, `(live smoke)` or `(human)`.

**1. Fake tier — `uv run pytest -m 'not live'` (the default).**
`tests/fakes/` substitutes the Resolve singleton at the connection manager —
the single seam — and mimics the real API's quirks (`None` getters, string
settings, `die_after` dropping the handle mid-call), so everything that is a
*decision* verifies here, plus pure functions and workers on fixture audio.
Tools are called directly, never over stdio.

**2. Live smoke — `uv run pytest -m live`.** The only place the direct-attach
path is real: needs Resolve Studio running with a python.org interpreter; an
autouse fixture skips every live test when Resolve is unreachable. An AC
marked "(live smoke)" is yours to execute on the live Windows 11 box, and a
pass means the tests **ran** — a skipped run means Resolve was unreachable.
Record the result on the **ticket** (hardware, interpreter, outcome); an
unrecorded pass gets re-run. ACs that need the human's hands — a project
open, media only they have, a click in the UI — are flagged **before opening
the PR** under `## Needs from you`. Before the first live run read "Running
the live tier" in `docs/context/tests.md` (project, scheduling, cleanup).

**What no seam covers:** whether fusionscript accepts the attach on a given
interpreter at all. ADR 0001: on a uv-managed standalone interpreter the
process dies in a Windows access violation nothing can catch, and `uv sync`
picks that interpreter silently — hence the pre-load interpreter guard.
Treat "all fakes green" as proof of decisions, never of the attach.

## Session workflow

Work from ticket #N happens on branch `issue-<N>`; non-ticket changes take a
branch named for the change (e.g. `fix-context-guard`). Background sessions
inherit the launcher's branch — verify or create the right one before the
first write (launcher-side: a launch with no assistant output did no work).

1. **Push the branch after the first commit** — pushed work survives a lost
   session; open no PR yet (a pre-review PR is born gate-red).
2. **Review on the branch, before the PR exists.** Weight follows what the
   diff touches, not how simple it looks. Executable — `src/`, `tests/`,
   `.claude/hooks/`, workflow YAML — gets `/code-review`: Standards against
   `CODING_STANDARDS.md`, Spec against the ticket, parallel sub-agents.
   Prose — docs, README, CLAUDE.md — is written and reviewed under
   `/writing-for-agents` (load it before the first edit); its line reads
   `Review: clean @<sha> — prose only, /writing-for-agents pass`.
3. **Fix findings; re-check the fix diff only** — one full review per PR; a
   fresh full pass only for fixes large enough to be a new diff.
4. **Open the PR with the review record in the body**: findings and
   resolutions, ending with `Review: clean @<sha>` for the commit reviewed.
   The gate passes only when the last `Review:` line's sha is the PR head,
   so every later commit reddens it until you re-review the new diff and
   stamp `uv run python scripts/review_stamp.py <pr> --summary "..."` — it
   takes the sha from the PR, refuses unless your `HEAD` is that commit, and
   re-checks the body against the gate; earlier lines stay as the record.
5. **Merge through the PR** — everything reaches `main` through a PR. CI
   (`ci.yml`: fake tier, mypy strict, ruff) and `review-gate.yml` (the
   `Review:` line reads clean) are the required status checks on `main`.
6. **After a stacked PR merges, verify its commits reached main by content**
   — `git log origin/main` shows `Merge pull request #<n>` or a subject
   ending `(#<n>)` and the files are there (`merge-base --is-ancestor` exits
   1 for every squashed PR; a PR merged into a consumed parent reads MERGED
   with nothing on main). Then `uv run python scripts/prune_merged.py` lists
   the merged residue; `--apply` drops it.
7. **If the PR was squashed, continue on a fresh branch** from `origin/main`,
   cherry-picking only the new commits — never force-push; the old branch
   would drag merged commits back in. When resolving conflicts, grep every
   conflicted file for `<<<<<<<` before committing — markdown too.
8. **Close the ticket with a comment** — PR link, what landed, the live
   record (home: the ticket), any unrun live ACs. The PR body links its
   ticket as `Refs #<n>` — the review gate refuses a closing keyword
   (`Closes #<n>`, `Fixes`, `Resolves`), because the merge would close the
   ticket with no outcome — and the ticket closes by
   `gh issue close <n> --comment` after the merge (`context-guard.py` blocks
   a close with no `--comment`). Every implementation comment ends with
   `## Needs from you` as its **last** section: each item that needs the
   human — decisions, live ACs, installs, scratch projects to delete — even
   when discussed above; "Merge PR #n" is not an item. If nothing is needed,
   omit the section; its absence says the ticket asks nothing.

## Context discipline

One rule: nothing enters the session unless the session is about to act on
it. Noisy commands (`pytest`, `mypy`, `ruff`, `gh … view|diff`) redirect to a
gitignored repo-local scratch log and the decisive line comes back via the
Grep tool, as one bare command from the session's own cwd: the worktree guard
refuses compounds (`;`, `&&`, `$(...)`, `for`/`while`, a `sleep N;` prefix,
env-var paths, a `cd`/`Set-Location` paired with a redirect) and fires before
it reads the target, so an absolute log path or an allow-rule does not clear
it and auto mode cannot decide it.

    uv run pytest -m 'not live' > pytest.scratch.log 2>&1
    gh issue view <n> --json body -q .body > issue-<n>.scratch.log
    gh pr diff <n> > pr-<n>.scratch.log

`mypy`, `ruff check`, `git merge origin/main --no-edit` and `gh issue view
<n> --comments` take the same shape; then Grep `FAILED|passed|error` (or the
section you need) in the log; never commit a log. The `gh` logs carry the
ticket number: two sessions in one checkout sharing one unnumbered log built
the wrong ticket; a `--json` field filter that skips the body (`-q .title`)
is fine unlanded. A `| tail` is no landing — a tail caps one run and runs
repeat — where a pipe of filters only (grep, rg, wc, Select-String, findstr,
Measure-Object; `| grep -c FAILED`) bounds the run and passes. Waiting on CI
is one `Monitor`, not `--watch`, a sleep loop or `tail -f`. Delegate
exploration to a read-only subagent; Read only what you will edit, ranged
(grep first) on big files; do not re-read a file after editing it. Hooks in
`.claude/hooks/` (`context-guard.py` on both shell tools, `read-guard.py` on
every reader; pass/block tables in `tests/test_context_guard.py` and
`tests/test_read_guard.py`) block unlanded noisy runs, whole-file dumps and
re-reads; the block message names the fix.

**Orient from `CONTEXT.md` first** — one table, ~150 lines: module → test →
seam. Area narrative lives in `docs/context/<area>.md`: Grep it, or Read it
ranged, only when you are about to work there; Explores are for what a map
can't hold (exact signatures, current behaviour). A PR that adds, moves or
deletes a module or test file updates the map in the same PR
(`tests/test_context_map.py` fails otherwise), narrative in the area doc.

**Session budget** — tool calls per session, when a ticket splits, when a
module's implementation is delegated: `docs/agents/session-budget.md`, read
before a ticket that adds three or more modules or a multi-PR sweep.

## Compute device

**GPU-first.** Every compute path runs on the card wherever a GPU path
exists; the CPU is a fallback the log and the job record name at WARNING. The
per-path table (GPU path or none worth wiring, how each reports a fallback)
is `docs/reference/compute-device-inventory.md`: read it before a separation,
transcription or beat-grid job, before you touch a compute path, and when you
add one (a new path takes its own row).

A CPU reading where the table says a GPU path exists is a **broken install,
not a slow box**: stop, fix it (or hand it to the human under `## Needs from
you`), then run. The server refuses a `+cpu` separator outright
(`RESOLVE_MCP_SEPARATOR_ALLOW_CPU`, README) and the live separator test is
red on a CPU device — both mean the install fix, never the override; the
`--env_info` check and the `+cu` torch restore command are the inventory's
"Checking and restoring the live box's build".

## Gotchas

Facts rediscovered across sessions — each cost a session real time at least
twice before landing here; a gotcha that gets a real fix loses its line.

- **R1 — Worktree sessions: pin every review and diff to `origin/main`**; the
  local `main` checkout lags while other worktrees merge.
- **R2 — Worktree pytest runs the main checkout's source** (editable
  install), so a new test fails against old code with no import error;
  `uv sync` is the first command after `EnterWorktree`, then trust a red run.
- **R3 — `ruff format` is not a gate.** CI runs `ruff check` only;
  `ruff format --check` fails repo-wide by design.
- **R4 — Merging is the human's.** Auto mode denies `gh pr merge`; the
  session's last act is the PR open plus the ticket comment.
- **R5 — reddit is unreachable from this box.** It rejects this box's user
  agent on every URL shape (`www.`, `old.`, `.json`, `i.reddit`); source
  research elsewhere from the first fetch.

## Doc maintenance

Every edit to CLAUDE.md, CONTEXT.md or `docs/agents/` goes through
`/writing-for-agents` (step 2); a dated full pass over all three runs
periodically (last: 2026-09-15, full on CLAUDE.md; 2026-08-10, full on
CONTEXT.md). Verified against mattpocock-skills 1.2.3.

## Agent skills

Pointer docs in `docs/agents/`: `issue-tracker.md` (issues via `gh`, blocking
edges, wayfinder frontier ops; `/to-tickets` splits a ticket),
`triage-labels.md` (five roles), `domain.md` (using `CONTEXT.md`,
`docs/context/`, `docs/adr/`), `concert.md` (P3, the style-driven concert
cut, with its mandatory `correlate_timeline` self-review), `rough-cut.md`
(P4, transcript-driven A-roll assembly, with its `virtual_transcript`
self-review), `style-layer.md` (`styles/` profiles and sidecars,
agent-authored).
