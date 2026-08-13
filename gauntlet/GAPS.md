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

## G3 — the gauntlet's own measuring tool lied, and the pack sealed anyway (in-work)

Round 1 (Taurus opening) — REVISED after diagnosis
(`gauntlet/recon/r1_diagnosis.md`). The render WAS the 13-shot edit:
frames verified against both cameras, placed source ranges equal the
authored ones frame-for-frame, correlate was right. The false symptom came
from `ab_pack.py`'s scene threshold 0.27 — matched-grade cuts between two
cameras in one dim room score 0.18–0.27, so ours read as 1 cut and the
human's 13 read as 9. Every threshold 0.04–0.18 finds exactly 13/13 with
zero false positives. Both arms of the blind pack were corrupt; R1 verdict
void. Fixes: threshold → 0.10; pack refuses to seal when detected cuts fall
under ~80% of an expected count; concert.md gains "scene-detect count ≈
timeline item count" beside the correlate self-review (correlate proves the
timeline, scene detection proves the pixels; disagreement is the alarm).
correlate's boundary-vs-visual blind spot did NOT fire but stays on the
risk list.

## G7 — four dormant build-path risks from the audit (open → tickets)

Found while auditing `resolve/build` for R1 (none fired — both clips report
`Start = 0`): raw `startFrame` used with no `Start` rebase; no clamping plus
fail-open E5 on bounds-less clips; `_append` not comparing the returned
list length; `_verify` never reading source frames back.

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

## G8 — song-opening title card convention absent from our workflow (open)

R1b critic. Human deliverable spends the pre-entrance dead air on a title
card and clears it at the entrance so reveal = downbeat; our builder had no
titling pass in the gauntlet protocol and spent the reveal on silence after
a 0.5 s black flash ("too short to read as a fade, too long to be
invisible"). Work: measure the card convention across all 5 deliverables
(in/out times vs first note), write it into styles/concert.md openings with
measured provenance, and make the titling pass part of the opening piece.

## G9 — unmotivated cuts in sparse passages; no "tighten then go still" gesture (open)

R1b critic. Ours ping-pongs two framings on a timer through the quiet
section and lets the 13 dB character change pass unmarked; human cuts on
the section's loudest peak, accelerates (1.7 s, 2.2 s shots) through the
decay, then releases into a long hold. Style work: every cut needs a
nameable motivation; sparse passages hold longer; approach transitions
with acceleration and release. Server work: the events that motivate cuts
(fills, entrances, solo changes, phrase ends) come from stems — blocked by
G4 — plus a framing-distinctness measure so "new picture" vs "same two
pictures" is a number (2 pictures ours vs 3 with a scale change, human's).

## G10 — separator resolution silently picked a CPU-only install (open)

Prep, round 1b. `config.audio_separator` defaults to the bare name
`audio-separator`, so the worker ran whichever one PATH happened to name —
here the system Python 3.12 install, whose torch is `2.13.0+cpu`. Nothing in
the record, the worker log, or the envelope said "CPU": the job simply ran,
and the only symptom was that it took forty minutes to get partway through
one model pass. htdemucs_ft is four bagged passes plus a drum stage, so the
whole separation was hours away while a 16 GB RTX 4080 SUPER sat at 2%.

Measured, same 71-minute board mix, same model:

| | separator torch | one htdemucs_ft pass | GPU util |
|---|---|---|---|
| before | 2.13.0+cpu | 94% in 36.6 min (~2.6 %/min) | 0-4% |
| after | 2.13.0+cu130 | 100% in ~50 s (~120 %/min) | 78-91%, 185 W |

≈45× on the pass rate. (The before figure had a second CPU separation
competing for cores, so the honest headline is "tens of times", not a
precise multiple.)

Fastest fix, and the one taken: a dedicated GPU env plus the config
override, leaving the repo venv and the system Python untouched —
`uv venv C:\Users\Daniel\.venvs\audio-separator-gpu --python 3.12`, then
`audio-separator[gpu]` and torch/torchvision from
`https://download.pytorch.org/whl/cu130` (cu130 because the driver reports
CUDA UMD 13.3; the cu128 index has no torch 2.13.0), then
`RESOLVE_MCP_AUDIO_SEPARATOR` pointed at that env's `audio-separator.exe`.
Two dependency traps in that env, both worth knowing: `audio-separator[gpu]`
does not pull `audioread`, and librosa 1.0.0 — which uv picks — dropped it,
so the CLI died on import with the separator's traceback buried in
`error.detail.output`; pinning `librosa==0.11.0` to match the known-good env
fixed it.

Server work: log the separator's device at separation start. audio-separator
already prints it on its first lines —

```
INFO - separator - PyTorch Version: 2.13.0+cu130
INFO - separator - CUDA is available in Torch, setting Torch device to CUDA
INFO - separator - ONNXruntime has CUDAExecutionProvider available, enabling acceleration
```

— and `separator._run` already reads every line, but `on_line` only keeps a
tail for the failure path and pulls percentages out. Parse the device line
there, log it, and put it on the job record beside `step`, so a CPU fallback
is visible in one glance instead of being inferred from a rate. The same
seam would have made this gap a five-second read rather than a process-tree
excavation.

## Round record

- **R1 · Taurus opening · VOID** (ours = A). Critic judged a corrupt pack
  (G3): ours shown as 1 cut, human's as 9 — both actually 13. Verdict
  discarded; re-judge with fixed tool = R1b. Still standing from R1: the
  critic's 10 judgeability gaps (G5) — stills-based limits are real
  regardless of the threshold bug.
- **R1b · Taurus opening · LOSS, legit** (ours = A, human = B; clean pack,
  13 cuts each, identical audio — pure staging test). Decided in the first
  3 seconds (title card vs black flash, G8) and at the t=38 transition
  (human's accelerate-and-release vs our unmarked step, G9). Human's
  recorded weakness: 15.8 s locked hold where the music thins — the
  longest stillest stretch in either version. Critic judgeability adds:
  cut-boundary frames (not midpoints), in-shot motion, transition type
  (hard cut vs dissolve), jump-cut risk on framing returns → ab_pack v2.
  G3 fix verified in the round: guard refused a deliberately wrong
  expected count; clean pack sealed 13/13.
