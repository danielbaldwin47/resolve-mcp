# Agent-owned trees and docs

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

Neither is read by server code; both are the agent's own working record, and
like `styles/` they are data, not modules.

`gauntlet/` — the gauntlet loop: agent-built cuts judged blind against the
director's own final cuts, piece by piece, where every critic loss becomes
server or workflow work and never a hand-tuned edit. `STATE.md` (protocol,
close rule, where each piece stands), `GAPS.md` (the gap ledger — one entry
per critic loss or prep finding, open/in-work/fixed), `HANDOFF.md`.
`tools/ab_pack.py` is the harness proper: a **sealed blind A/B pack builder**
— two videos in, deterministic A/B labels out, plus contact sheets,
cut-boundary filmstrips and a measured `cuts.json`, with the label→source
mapping quarantined in `assignment.json` so a critic reads the pack without
knowing whose cut is whose; it refuses to seal when its scene scan finds far
fewer cuts than the timeline holds (G3). Given each label's own
`correlate_timeline` cuts file (`--a-subjects`/`--b-subjects`, both or
neither) it also carries the on-soloist track, stripped to four columns so no
timeline or clip name reaches the pack (#181). `recon/` is one-off instruments —
one script plus its JSON receipt per question (plans, builds, pixel checks,
occlusion scans). Renders, packs, frame dirs and the per-frame ffmpeg dumps
under `recon/` are regenerable and gitignored; only scripts and receipts are
committed.

`projects/<project>/` — the agent-authored files for one Resolve project:
`README.md` (its fixed facts — timelines, master mix, what is unverified),
`songs.json`, the cut and titles files, and `cards/` — the PNG title-card
route (`bake_taurus_cards.py` bakes a `%04d` RGBA frame run per card, fade
ramps included, for a project whose media pool holds no GUI-authored Text+
template; `titles/schema.py` §6).

## Tooling — `scripts/`

Repo maintenance, not server code. `prune_merged.py` — the post-merge sweep
(CLAUDE.md step 6): lists, or with `--apply` removes, remote branches whose PR
merged, their local branches, and `.claude/worktrees/` worktrees whose branch
is merged. Merged means the tip is on `origin/main`, or a PR **into main** was
squashed from exactly that tip — a PR merged into another branch does not count
(the stacked-PR trap in CLAUDE.md step 6). Never a locked or dirty worktree, the
branch a locked worktree holds, an open-PR branch, or a tip with commits
`origin/main` lacks. Every `gh`/`git` call goes through one injectable
`Runner`, which is the seam `tests/test_prune_merged.py` drives on fixtures.

## Docs

- `docs/adr/` — 0001 interpreter must be a registered install; 0002
  analysis models are injected; 0003 stems fingerprinted (path is a
  content hash); 0004 editor-state getters answer only for the current
  timeline; 0005 source frames are read off the left offset, not the
  source start; 0006 markers ride the mix across a rebuild; 0007 audio is
  identified by content, the hash remembered against a stat; 0008 beats and
  applause infer on CUDA, and the device is named rather than defaulted.
- `docs/agents/` — issue-tracker conventions (wayfinder map ops), triage
  labels, domain-docs usage, the style layer (sidecar + profile formats,
  provenance tags, how a corpus pass is run), the concert pillar
  (`concert.md`: the director's three inputs, session-start analysis prep,
  song-by-song planning, the mandatory `correlate_timeline` self-review,
  the cut report and review-round conventions, #16), the rough-cut pillar
  (`rough-cut.md`: the brief and b-roll catalog the agent owns, the
  assembly loop, the `virtual_transcript` self-review and the cut report;
  also home of the `projects/<project>/` convention and the songs file's
  ownership, #132).
- Landing places for artifacts that today live only in issue and PR
  threads: research reports → `docs/research/`, spike reports and design
  bibles → `docs/reference/`, adversarial and other standalone reviews →
  `reviews/` (dated filenames). All merge to `main` in the PR that
  produced them — a finding on an unmerged branch or in a thread is
  unreadable from here.
- Wayfinder: map = issue #1, scope = #2, spec = #22, build tickets #23–#47.
