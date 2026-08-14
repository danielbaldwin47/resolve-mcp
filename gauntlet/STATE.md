# Gauntlet loop — state

Goal: agent-produced concert edits through resolve-mcp beat Daniel's human
final cuts in blind critic comparison, piece by piece. Every critic loss
becomes server/workflow work — never a hand-tuned edit fix.

## Fixed facts

- Branch/worktree: `gauntlet-loop-1` at `.claude/worktrees/gauntlet-loop-1`.
- Human final cuts (the bar): `S:\Deliverables\Ryan Devlin\6-17-26 Zinc Bar\Full Videos\`
  — Hardest Part, Maitland Boulevard, Sambra, Soultrane, Taurus People.
- Resolve open; `Zinc SYNC` = synced footage timeline, `Zinc - Set 2 Main` =
  original cut timeline.
- Secondary bar: Emmet Cohen official live videos → `C:\Users\Daniel\.claude\jobs\ba57c700\tmp\refs\`.
- Progress artifact: https://claude.ai/code/artifact/83654b7b-7a77-4f50-a8d3-957a20cab5ff
  (source: `C:\Users\Daniel\.claude\jobs\ba57c700\tmp\gauntlet-progress.html`).

## Protocol

Pieces: song opening, cut timing vs music, angle choice, transitions,
energy arc, ending. Per piece, per round:

1. Builder agent (fresh context) produces the piece as a real edit through
   the server; renders it.
2. Critic agent (fresh context, harsh, blind): both versions with labels
   stripped, picks the one a viewer would rather watch, names single
   biggest remaining gap. Praise discarded.
3. Loss → gap lands in `gauntlet/GAPS.md` and becomes server/workflow work
   on this branch. Never hand-tune the edit.
4. Repeat until critics pick ours blind. Close rule (since R2's 1–1
   split): a piece closes only when a MAJORITY OF THREE fresh,
   independent critics picks ours on the same sealed pack.
5. Director's rulings (binding on every critic brief):
   - 2026-08-13: color/grade differences are OUT OF SCOPE — grading is
     not part of this gauntlet; critics must be told to ignore them.

## Status

- **Piece 1 · Song opening (Taurus People, first 90 s): CLOSED — WON 2–1**
  (R3, 2026-08-13, sealed pack `taurus_opening_r3`). Render:
  `gauntlet/renders/taurus_opening_r3.mp4`, timeline
  'Taurus People Opening R3 v3'.
- **Piece 2 · Ending (Taurus, last 90 s): CLOSED — WON 2–1** (P2R3,
  2026-08-13, pack `taurus_ending_p2r3`; render
  `gauntlet/renders/taurus_ending_p2r3.mp4`). R3 = R2 minus the last
  cut: final 13.7 s on the ensemble wide. Carry-forward critiques:
  3.3 s climax jab reads as a cliff; long-short alternation predictable
  by 40 s.
- **Piece 3 · Cut timing + angle choice (mid-song trading window):
  CLOSED — WON 2–1** (P3R2, pack `taurus_mid_p3r2`, render
  `gauntlet/renders/taurus_mid_p3r2.mp4`).
- **Piece 4 · Energy arc, FULL SONG: CLOSED — WON 3–0 UNANIMOUS**
  (P4R2, 2026-08-14, pack `taurus_full_p4r2`, render
  `gauntlet/renders/taurus_full_p4r2.mp4`, 497.7 s). All three fresh
  critics picked ours; all three read the human's arc as inverted
  (fastest in quiet, parked in loud). Ours' remaining flank: the quiet
  trough (79–157 s) reads static — carry into any future round.
- **GAUNTLET COMPLETE: every named piece won blind.** Remaining
  engineering debt before a PR to main: /code-review two-axis over the
  full branch diff per CLAUDE.md (tail device, detached jobs, occlusion,
  shot_rhythm/gears all reviewed piecewise in-branch; a final
  Review: line is still owed), G13 (4K default), G6 (sidecar datum),
  G7 tickets, G1/G2 detector gaps.
- Iteration 0 (2026-08-13): setup done. Recon workflow launched
  (live probe, docs survey, human cut stats, Emmet refs) → results land in
  `gauntlet/recon/`.
