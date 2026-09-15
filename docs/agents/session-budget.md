# Session budget

How many tool calls fit one session, when a ticket splits, and when a
module's implementation is delegated. Pointed to from CLAUDE.md, "Context
discipline"; read before a ticket that adds three or more modules or a
multi-PR sweep.

Context starts at ~44k tokens and grows ~500 per message; a ticket that
cannot land in ~150 tool calls does not fit one session — split it (file the
second ticket, then continue) rather than push on. Half the growth is your
own Edit/Write payloads and thinking, which no result-side rule can shrink;
a ticket that adds three or more new modules delegates each module's
implementation to a subagent in the same worktree and keeps only receipts
(commit sha, gate lines) in the session, the way review is already
delegated (#248 measures this per session).

Long multi-PR sweeps (merge trains, cross-PR audits) shard per-PR into
subagents; the orchestrating session keeps receipts, not diffs — past
sweeps that inlined everything ended at 2× the usable context budget.
