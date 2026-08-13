# Gap ledger

Every critic loss and prep finding lands here as server or workflow work.
Never a hand-tuned fix to an edit. Status: open / in-work / fixed (PR).

## G1 — applause/tune detection blind on board mixes (open)

Prep, iter 1. Peak applause probability 0.297 over the whole 74-min Reaper
board mix vs 0.3 threshold → 1 tune found where 5 exist. The concert
pillar's song-start proposal depends on this route. Board/DI mixes have no
room mic; the detector needs either a lower-threshold mode with beat-density
gating, a spectral applause signature that survives a board mix, or a
cross-correlation route against deliverables when they exist (that is how
the gauntlet measured the real spans).

## G2 — beat grid unusable as bar map on this corpus anchor (open)

Prep, iter 1. `meter: 1`, median gap 0.28 s (~214 "bpm"), 71.7% trust,
27 gaps >2 s. Onset-scale placement only; beat-1/bar-position style rules
cannot fire. Known confound from docs survey (same on the corpus anchor).

## G3 — a built timeline can render as no edit at all, and nothing notices (open, root-cause in progress)

Round 1 (Taurus opening). Builder authored 13 shots; `correlate_timeline`
verified 13 cuts with clean offsets; the render contains ONE visible cut
(the deliberate black lift) then 89.5 s of a single locked camera. Blind
critic: "a security camera pointed at a jazz quartet." The self-review
measures cut *times* against music but never that the frame actually
*changes* at a cut. Fix direction (pending diagnosis): per-cut visual-change
verification (frame delta across each boundary) in correlate or a new
verify-render step; plus whatever root cause the diagnosis names (builder
record→source arithmetic vs resolve/build placement vs render target).

## G4 — long jobs die with the launching process (open)

Prep + round 1. `separate_stems` (in-process daemon thread) died when the
launcher exited; cache holds an empty `mix/`. Phrase/solo/fill analysis —
the pillar's core inputs — never became available. Jobs need a detached
runner (subprocess that survives the MCP process) or a documented
long-lived-host pattern that agents can actually satisfy.

## G5 — critic judgeability: the measurements a blind viewer-judge needs (open)

Round 1 critic, verbatim needs; each is server measurement work:
1. Per-shot motion metric (optical-flow magnitude, global-vs-local split) —
   locked wide vs slow developing wide.
2. Per-shot stability score (residual after global motion compensation) —
   handheld wobble invisible in stills.
3. Per-cut visual delta (framing histogram/embedding distance across the
   boundary) + 30-degree-rule flag + match-on-action frames either side.
4. Cut-to-beat offsets at sub-100 ms, in ms and beat fractions (1-s RMS
   cannot resolve musicality).
5. Audio class track (applause/speech/music/silence at 1 s resolution).
6. Per-shot subject labeling × who-is-soloing track — the core concert
   question.
7. Per-shot sharpness, clipped-highlight %, exposure variance.
8. Super/graphic presence detection with in/out timecodes + straddle check.
9. Head/tail treatment: fade-in vs dropped frames, audio floor handling.
10. Audio feel across cuts (balance/room-tone jumps) beyond RMS level.

## G6 — angle sidecar A7IV zero off by one (open, trivial)

Builder measured A7IV record zero 86306 live; sidecar says 86307.
Correct the sidecar datum.

## Round record

- **R1 · Taurus opening · LOSS** (ours = A). Critic on ours: not an edit —
  one locked 89.5 s wide (G3 artifact). Critic on human's: last 30 s is
  mechanical ping-pong between three near-duplicate framings — cuts because
  the timer said so. (Keep: that is the bar's weak flank.)
