# Post-gauntlet handoff — PR + tickets

Written 2026-08-14 before context compaction. Next tasks Daniel asked for:
(1) open the PR on `gauntlet-loop-1`, (2) file tickets for EVERY issue
worked or outstanding — resolved ones get a ticket with details +
resolution, then immediately closed (documentation); outstanding ones
filed via `/to-tickets` (user skill; if absent post-compact, ask or use
`gh issue create` per `docs/agents/issue-tracker.md` conventions +
`docs/agents/triage-labels.md` labels).

## Facts

- Worktree: `.claude/worktrees/gauntlet-loop-1`, branch `gauntlet-loop-1`,
  pushed through `8c24f48`, tree clean. Repo: danielbaldwin47/resolve-mcp.
- Outcome: all four gauntlet pieces won blind (opening 2–1, ending 2–1,
  mid-song 2–1, full-song energy arc 3–0). Full round records:
  `gauntlet/GAPS.md`; status: `gauntlet/STATE.md`.
- Progress artifact: https://claude.ai/code/artifact/83654b7b-7a77-4f50-a8d3-957a20cab5ff
  (source `C:\Users\Daniel\.claude\jobs\ba57c700\tmp\gauntlet-progress.html`).
- Renders: `gauntlet/renders/` (capstone `taurus_full_p4r2.mp4`); sealed
  packs `gauntlet/packs/`.

## PR requirements (CLAUDE.md session workflow — follow exactly)

1. Review BEFORE the PR exists: full-branch `/code-review`
   (mattpocock two-axis: Standards + Spec) over `origin/main...HEAD`
   (R1: pin to origin/main). Executable diff includes `src/`, `tests/`,
   `gauntlet/tools/`, hooks — everything.
2. Fix findings; re-check fix diff only.
3. PR body: findings + resolutions first, LAST line must read
   `Review: clean` (gate reads only the last `Review:` line).
4. CI gates: `uv run pytest -m 'not live'` (last known 1721+ passing),
   `uv run mypy` strict, `ruff check` — all were green at each commit.
5. Merge through the PR; never direct to main. After merge, verify commits
   reached main by content (squash vs merge-commit, CLAUDE.md step 6).
6. In-branch piecewise reviews already done (cite in PR body): detached
   jobs — 4 focused rounds (commits 5b021b1, d1e17cf, ec42905, ad9d02d);
   tail device — 1 round + fixes (b04ecc9 → b743b7e). Live proofs recorded
   in gauntlet/recon/.

## Server/src work on the branch (for PR description)

- Detached job runner: jobs/runner.py, store.py, worker.py, detached.py —
  jobs survive launcher exit; atomic-link stems claims; launcher-sidecar
  (never writes the record); pid-recycling/zombie handling.
- `tail` schema device (cut/tail.py, resolve/tail.py, schema §8, E12):
  dissolve_to_black/hard_to_black + audio_fade_frames via OTIO staging
  round-trip (scripting API has NO transition calls — discovered live).
- `analyze_occlusion` (video/blocking.py, video/occlusion.py, tool):
  near-field obstruction scoring + unusable windows.
- correlate additions: `shot_rhythm` block (histogram, alternation,
  reads_metronomic) commit 21edcd1; `gears` block (loudness-tercile cut
  rates, one_speed) commit 595bcc1 + energy.rms_curve.
- Style layer (styles/concert.md): card convention, motivated cuts,
  ladder constraints, end-on-ensemble, coda deceleration, bimodal spread,
  accents-vs-onsets two-scale placement, arc-gear table — all measured/
  provenance-tagged. styles/angles/mcp-tests-zinc.json sidecar.
- Gauntlet harness: gauntlet/tools/ab_pack.py v3 (blind packs: cuts,
  sheets, cutstrips, motion classes, transition typing, audio classing,
  ending ramp, seal guard).
- projects/mcp-tests-zinc/: songs.json + four cut files + titles.

## Tickets — RESOLVED during the loop (file + document + close each)

1. G3: ab_pack scene threshold 0.27 blind on matched-grade cuts → 0.10 +
   refuse-to-seal guard; R1 verdict voided. (gauntlet/recon/r1_diagnosis.md)
2. G4: long jobs died with launcher → detached runner (4 review rounds,
   live-proven twice). ~1634+ tests.
3. G8: opening title-card convention measured across 5 deliverables →
   styles/concert.md §5b (card only where head silent; dissolve-up +
   lower-third otherwise; tails measured too).
4. G9: motivated cuts / accelerate-release → style bullets.
5. G10: separator silently CPU (system torch 2.13.0+cpu) → dedicated CUDA
   env C:\Users\Daniel\.venvs\audio-separator-gpu + RESOLVE_MCP_AUDIO_SEPARATOR;
   ~40× pass rate, full separation 17 min. Follow-up still open (below).
6. G11: occlusion unmeasurable → analyze_occlusion (35 tests, live-proven).
   Follow-up tuning open (below).
7. G12: grade gap — WITHDRAWN by director ruling (grading out of scope);
   document + close.
8. G14: tail dissolve inexpressible → tail device via OTIO staging.
9. G15: no audio fade → audio_fade_frames on tail device.
10. G16: pack couldn't hear/see slow transitions → ab_pack v3 audio-class
    track + long-dissolve detection.
11. G17: metronome (4 straight panels) → bimodal style + correlate
    shot_rhythm; proven in P3R2 win.
12. Arc gears (capstone R1 loss) → measured human gear table (quiet 0.74×
    / loud 1.15× / sustained peak 1.29×; within-section spread was the
    real deficit) → style + correlate gears; proven in P4R2 3–0 win.

## Tickets — OUTSTANDING (file via /to-tickets, leave open)

1. G1: applause/tune detection blind on board mixes (peak prob 0.297 vs
   0.3 threshold; 1 tune found where 5 exist).
2. G2: beat grid meter:1 on this corpus (onset-scale only, no bar map).
3. G5 remainder: per-shot subject labeling × who-is-soloing; sharpness/
   exposure/clipped-highlight metrics; super/graphic detection with
   in/out + straddle check; per-cut visual delta + 30°-rule; stability
   score. (Motion classes, boundary strips, audio classing, beat offsets
   are done — scope tickets to the remainder.)
4. ~~G6: angle sidecar A7IV zero off-by-one (86306 measured vs 86307)~~ —
   done, #185 (sidecar item source in 31269 → 31270).
5. G7: four dormant build-path risks (startFrame rebase; fail-open E5 on
   bounds-less clips; _append length unchecked; _verify no readback).
6. G13: built timelines inherit project 4K default; every round manually
   set 1920×1080 pre-render — build/render should handle resolution.
7. G10 follow-up: parse separator's device line (PyTorch/CUDA banner) in
   separator._run and surface on the job record so CPU fallback is visible.
8. G11 follow-up: occlusion false-positive discriminator — measured truth:
   "covers a player the shot is framed on" separates all known cases;
   motion does NOT (ending-window evidence); score does NOT rank truth
   (real blocking 0.416 < FP 0.469). Also detector goes blind to a body
   once it stops moving; mid-take reframe FP signature (occlusion_mid).
9. Capstone remaining flank (P4R2 winning-round critique): quiet trough
   79–157 s reads static — five long locked holds + orphan 2.5 s flash;
   future rounds should make the floor breathe.
10. Style layer: graduate gauntlet-measured claims per corpus rules once
    a second project corroborates (currently n=1 project provenance).
11. Stems: split_wind single-pass route (a lone pass through the tool path
    redoes the whole stems key; lone-pass fallback died writing second
    half, output tail uncaptured) + beats cache identity (acquired copy
    misses cache because identity keys to the director's master).
12. Housekeeping: gauntlet/recon/full_gears.py + full_gears.json left
    uncommitted by the style agent? (was clean at last check — verify);
    Emmet Cohen refs in job tmp dir were never used by a critic — note or
    drop.

## Ticket conventions

`gh` CLI, repo danielbaldwin47/resolve-mcp. See docs/agents/issue-tracker.md
(wayfinder map ops — frontier query, claim, edges, resolve) and
docs/agents/triage-labels.md (five canonical roles, label == role name).
Every implementation comment ends with `## Needs from you` as last section
when something needs the human.
