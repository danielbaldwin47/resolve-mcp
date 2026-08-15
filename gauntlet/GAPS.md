# Gap ledger

Every critic loss and prep finding lands here as server or workflow work.
Never a hand-tuned fix to an edit. Status: open / in-work / fixed (PR).

## G1 — applause/tune detection blind on board mixes (FIXED, #179)

Prep, iter 1. Peak applause probability 0.297 over the whole 74-min Reaper
board mix vs 0.3 threshold → 1 tune found where 5 exist. The concert
pillar's song-start proposal depends on this route. Board/DI mixes have no
room mic; the detector needs either a lower-threshold mode with beat-density
gating, a spectral applause signature that survives a board mix, or a
cross-correlation route against deliverables when they exist (that is how
the gauntlet measured the real spans).

**Fixed by two rules in `analysis/applause.py`**, both arithmetic over
measurements that already existed. First, the threshold is a ceiling: when a
whole file holds under 10 s over it, the curve is read at 0.09 of its own
peak — the peak that lasts a burst's worth, not a single frame — and the
burst minimum drops to 2.0 s with it (`reading`). It is a fallback and not a
recalibration on purpose: scaling the room mic the same way turns the
clapping after every solo into a boundary, 19 calls where 13 belong. Second,
the applause says a tune *ended*, not that the next one started: on this mix
the announcement between them ran 0.3 s to 65 s at 20-40 dB under the music,
so each boundary walks forward to where the loudness curve `analyze_music`
already wrote comes up to the file's median less 6 dB and holds for 10 s
(`settled`), and a call the band never comes in on is refused.

Measured, this mix, `gauntlet/recon/board_tunes_job.json` (the real job) and
`board_boundary_check.json` (the shipped functions, before and after):

| | tunes | boundaries within 5 s of the human spans |
|---|---|---|
| before | 1 | 0 of 5 |
| after | 5 | 5 of 5, worst error 1.63 s |

Every constant is the middle of a measured plateau, not a fit:
`board_boundary_sweep.json` runs 140 settings and 35 of them call this set
correctly, worst error across all 35 of 2.01 s.

`scullers_boundary_check.json` is the no-regression control — the room mic
from #133, 13 calls at a peak of 0.65. The fallback correctly does not fire:
threshold, burst count and burst length identical to before. The settle step
does run there, as it does everywhere, and takes its call count 13 → 11
before the pulse check and 10 → 9 after. Both losses are argued rather than
accidental: one is the 296-440 s call the pulse check independently drops as
talking, and one is an opening whose 293 s hold 27 s of music. Anyone who
wants the old boundaries on room-mic material passes `settle_seconds=0`.

## G2 — beat grid unusable as bar map on this corpus anchor (in-work)

Prep, iter 1. `meter: 1`, median gap 0.28 s (~214 "bpm"), 71.7% trust,
27 gaps >2 s. Onset-scale placement only; beat-1/bar-position style rules
cannot fire. Known confound from docs survey (same on the corpus anchor).

Fix: `detect_bars` (#180) — a second reading over the same grid rather than a
second model. Folds the onset-scale grid to the tactus (only when the grid's own
rate is outside the tapping range, so a backbeat cannot fold a grid that is
already a plausible pulse), then scores every meter and phase against a per-beat
accent reading and takes the widest lead over the runner-up. Refuses rather than
guessing when the accents say nothing, and carries the grid's own `meter: 1` and
214 bpm in the result beside its own answer. `correlate_timeline` takes the map
as `bars=` and reports `map_bar` / `in_group` / `bar_offset` per cut plus a
`bar_groups` histogram, ungated on the #112 beat gate — the map exists for the
grids that gate refuses whole. Style vocabulary in `docs/agents/style-layer.md`
§"Bar and phrase vocabulary"; the claim in `styles/concert.md` §1 is
`[believed, unverified]` until a corpus pass measures it.

**Measured, 2026-08-14** (`gauntlet/recon/g2_bar_map.json`), over the cached
Zinc grid, both witnesses, Taurus span and whole set:

- **The tempo half is fixed.** All four readings — both witnesses, both spans —
  fold to 107.14 bpm from a grid reporting 214.29. That reading rides home in
  every result beside the grid's own.

  It did not start out that way, and the reason is worth keeping. The first
  version chose between halving and thirding on whichever scored higher, and
  both land inside the tapping range at this tempo: the mix folded to 107 and
  its own bass stem to 71, off contrasts of 0.013 against 0.053. At these sample
  sizes that is noise deciding the tempo. Two fixes, both in `analysis/bars`:
  the accent threshold now scales with the span (`_accent_floor` — three
  standard errors of a contrast between two halves, which is 0.18 over a
  hundred-beat fixture and 0.02 over this set, so one fixed number cannot serve
  both), and when no candidate clears it the *least aggressive* fold wins rather
  than the highest-scoring one. The octave error every tempo tracker has,
  answered by assuming least and saying so in `fold_reason`.
- **The bar-line half is refused, and the refusal is measured.** Agreement
  across four-bar windows is 0.10–0.17: adjacent windows of one tune at one
  tempo reach *different* meters and phases 83–90% of the time. Confidence
  0.07–0.13 against a floor of 0.3. Before the agreement check went in, the same
  spans scored 0.3–0.6 on contrast alone over sixty-second windows and every one
  of those readings disagreed with its neighbour — the check is what turned a
  coin flip into an honest refusal.
- **What that means:** RMS at the beat carries no bar-level accent on this
  idiom. Brushes do not mark the one and a walking bass plays every quarter
  alike, so the loudness witness has nothing to find. This is a fact about the
  witness, not about the arithmetic — the same code reads a click track with a
  loud one correctly through the installed beat model (live tier,
  `test_live_analysis.py`).

Still open, and now scoped: **a witness that is not loudness.** The two the
ticket named and this did not try are downbeat tracking proper (a DBN over the
beat activation, which is where the published work on this lives) and harmonic
change — the root the bass lands on, not how hard it lands. Either is its own
ticket. Closing G2 needs one of them plus the director's ear check on whatever
map it produces.

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
   **Delta and flag done** (#184): `resolve_mcp.video.framing` reads layout,
   structure, content and size across each boundary; the pack carries a `delta`
   on every cut plus a `visual_delta` block per label, a method block in the
   manifest and the number captioned on each filmstrip row, and
   `correlate_timeline(deltas=...)` joins a catalog onto the timeline's own
   cuts. Threshold calibrated on the human deliverables
   (`recon/cut_delta_calib.json`): 200 cuts, floor 0.44, median 0.63, flag at
   half the floor (0.20), none of the 200 flagged. Still open from this item:
   match-on-action frames either side — the delta says *how far* the picture
   stepped, not whether the movement carried through the cut.

   Found while calibrating: **Soultrane is cut with multi-second dissolves end
   to end, and the scene detector reads it as zero cuts.** Receipt:
   `recon/soultrane_dissolves.json`. In the 120-240 s window the picture steps
   0.67 across three seconds — as far as any hard cut in the other four songs —
   while the largest single frame-pair delta is 4.95 against a noise floor of
   1.5, and `select='gt(scene,0.015)'` finds nothing. The median three-second
   step is 0.04, so the shots really are holding between transitions: this is an
   edit, not a drifting camera. So one of the five deliverables contributes no
   cuts to any pack, any calibration or any critic pack built from it, and says
   so only as a count of zero. This is `detect_cuts`' documented blind spot at
   whole-song scale; the v3 slow-transition pass looks at shot tails and the
   ending, not at boundaries nobody detected. Ticket: #203.
4. Cut-to-beat offsets at sub-100 ms, in ms and beat fractions (1-s RMS
   cannot resolve musicality).
5. Audio class track (applause/speech/music/silence at 1 s resolution).
6. Per-shot subject labeling × who-is-soloing track — the core concert
   question. **Fixed in #181** (`analysis/subject.py`): the sidecar's subject
   read as player/ensemble/other and joined to the solo windows in seconds, so
   `correlate_timeline` carries `subject`/`subject_kind`/`on_soloist` per shot
   and an `on_soloist` share inline, and `ab_pack.py` carries the same track
   into a pack (`--a-subjects`/`--b-subjects`, both or neither). Authored, not
   detected: no pixel here knows a drummer from a horn player, so the answer is
   only as good as the sidecar — a camera that roams the band is labelled by
   habit rather than by shot, and `unlabelled_seconds` is what says how much of
   the cut no label reached. Measured live on the two closed Taurus pieces
   (`recon/subject_track.py`, receipt beside it): the R3 opening is 52% on the
   ensemble, 48% on a non-soloing player, 0% on the soloist over 81.5 s of
   labelled screen time; the P4 R2 capstone is 53% / 42% / 5% over 489 s. The
   0% is real rather than a hole — this rig's only player camera is the drum
   cam, and the solo map has nobody drumming out front in the opening. Spot
   check against frames of both cameras: the FX6 wide holds the whole band
   (`ensemble`) and the A7IV holds the drummer (`drums`, a player), the two
   labels 15 of the opening's 17 shots carry; the other two are title cards,
   which is what `unlabelled_seconds` counts. Two things the reading refuses to
   round off: a camera on neither a player nor the band (audience, room) has its
   own `elsewhere` line rather than counting as a player nobody was watching,
   and `soloist_seconds_by_follow_camera` says how much of the soloist share
   came from a camera whose sidecar label asserts it follows the front rather
   than from a subject the solo map matched. Pack side verified on the same two
   cuts (`recon/subject_pack.py`): the pack's share equals correlate's exactly,
   nothing but the four subject columns crosses into the pack, and a span that
   cuts through shots counts the part inside where the front held through it.
7. Per-shot sharpness, clipped-highlight %, exposure variance.
8. Super/graphic presence detection with in/out timecodes + straddle check.
   **Done** (#183): `resolve_mcp.video.supers` reads burned-in graphics off a render —
   a **card** holds the whole frame, an **overlay** sits on the picture — and the pack
   carries a `supers` block in `cuts.json`, a `supers.json` catalog beside it, the
   counts in the manifest, and `straddles_super`/`super_kind` on every cut;
   `correlate_timeline(supers=...)` joins that catalog onto a timeline's own cuts.
   Each end of a span is walked at native rate, so an in and an out are frames.

   **The convention is now a number.** On Taurus People the title card measures
   `clears_before: 1` — it clears one frame before the entrance it announces, which is
   what #169 verified by hand off filmstrips.

   **The check itself came back different from how the ticket asked for it.** "No cut
   straddles a super" fails every deliverable in the corpus: the human holds a personnel
   lower third across three and four cuts at a time, because a title track is laid over
   the edit rather than into it. So a straddle is reported as a *fact* and its `kind` is
   what makes it a finding — `straddled_cards` counted apart from `straddled_overlays`.
   Across the five deliverables: one straddle, an overlay, on Taurus People.

   Receipt: `recon/super_scan.json`, all five deliverables, decoded on NVDEC (bit-identical
   to software, checked frame for frame). Six of the ten supers a human can see, **no false
   positives**, every hit confirmed by eye and every box on the caption — the personnel
   lower thirds at 0.86-0.93 of frame height, the title card at 0.47-0.61. Soultrane is
   worth naming: the cut detector finds *zero* cuts in it (#203) and its lower third is
   found anyway, which is what the reading needing no cut list buys.

   Still open from this item: **recall.** Four supers are missed, all of them titles held
   over a picture that never changes under them — either too still to be read at all, or
   outside what a caption looks like. Precision comes from asking exactly that: big enough
   to read (the six found run 819-3395 px) and shaped like a line of text (none taller than
   0.141 of the frame) — against false readings that are either 118-212 px or 0.30-0.51 tall. A graphic outside those bounds, a corner bug most of all,
   cannot be told from the lit nameplate on the piano lid and is not reported. An earlier
   reading asked instead whether the picture changed while the graphic was up; it cost
   seven of the ten and was removed.
9. Head/tail treatment: fade-in vs dropped frames, audio floor handling.
10. Audio feel across cuts (balance/room-tone jumps) beyond RMS level.

## G6 — angle sidecar A7IV zero off by one (closed)

Builder measured A7IV record zero 86306 live; sidecar said 86307.
Fixed 2026-08-14 (#185): the second A7IV item in
`styles/angles/mcp-tests-zinc.json` carried source in 31269 against record
117576; it is 31270, and the entry now records why (live `GetLeftOffset`,
frame proof, and the entry's own duration arithmetic). No test fixture
encoded the old datum, and the cut files under `projects/mcp-tests-zinc/`
were already on 86306.

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
The per-cut half of that measure landed with #184 (`video/framing`): each
boundary now carries how far the picture stepped and whether it stepped far
enough. What is still missing is the *shot-set* half — distinctness across a
passage, not across one cut.

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
| after | 2.13.0+cu130 | 100% in ~55 s (~110 %/min) | 78-91%, 185 W |

≈40× on the pass rate. (The before figure had a second CPU separation
competing for cores, so the honest headline is "tens of times", not a
precise multiple.)

End to end on GPU the whole job — eight bagged htdemucs_ft passes, then the
MDX23C drum decomposition at 60-80% util and ~215 W — took **17 min 15 s**
(19:58:45→20:16:00Z, `separate_stems-87ae86e85665`, `cached: false`,
progress 1.0): 7.5 min of four-stem separation, ~7 min of drum split, the
rest acquisition and collection. At the measured CPU pass rate the
four-stem stage alone would have been on the order of five hours, which is
why this read as a hung job rather than a slow one.

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

## G14 — cut-file schema cannot express a dissolve (CLOSED 2026-08-13)

Piece 2 R1, unanimous 0–3 loss. The measured Taurus tail is a 5.923 s
dissolve to black starting 0.51 s after the last note (4-frame black
tail); the schema's only black device is a literal-black gap, so the
builder shipped a hard cut to black + 6.6 s of nothing — "the piece
doesn't punctuate, it runs out."

**Fixed together with G15** by one schema device: an optional `tail`
object on the cut file, `{type: dissolve_to_black|hard_to_black,
duration_frames, audio_fade_frames}` — `get_cut_schema` §8. Wired
through `cut/tail.py` (one reading), `cut/validate.py` (E12: a dissolve
cannot outrun the shot it fades, a fade cannot outrun the mix, a cut
ending on a gap has no picture to dissolve) and `resolve/tail.py`.

Route, probed live on 21.0.3 read-only first: **the scripting API has no
transition call at all** (`Timeline` has none; `TimelineItem.SetProperty`
offers a *static* `Opacity` and nothing for audio level —
`gauntlet/recon/endev_probe.json`). So a tailed build appends to
`<name> v<N> (tail staging)`, exports OTIO, edits the transitions in, and
imports back as `<name> v<N>`, deleting the staging timeline. Resolve
takes both and renames them: `Cross Dissolve` on video, `Cross Fade
0 dB` on audio.

Resolve's "ok" is not evidence, so the build **reads the tail back** off
the landed timeline before deleting the staging one — a second export,
because the API has no getter for a transition. Live confirm on the
proof run: `Cross Dissolve in_offset 142` on Video 1, `Cross Fade 0 dB
in_offset 125` on Audio 1. A round trip that silently drops them fails
the build instead of shipping a hard cut.

One thing the spike only found by failing on the real cut: Resolve pads
every exported track with a trailing `Gap` out to the *timeline's*
length, so the ordinary concert shape — mix outliving the picture —
exports a V1 ending in black. The fade goes after the last **clip**, not
the last child.

Live proof: `gauntlet/recon/ending_devices_proof.json`. 20 s cut, 142-frame
(5.923 s) dissolve + 125-frame (5.21 s) audio fade, built and rendered on
a scratch timeline. Luma ramps monotonically 59.1 → studio black across
5.25 s of measured intermediate levels (43 samples strictly between 8 %
and 92 %; a hard cut scores 0) and lands on exact black at the last
picture frame; RMS falls −12.0 → −13.0 → −19.6 → −25.3 → −25.4 → −36.4 dB
per second and ends at −48.5 dB. All six bars pass; scratch timelines
deleted.

## G15 — no audio fade device (CLOSED 2026-08-13)

Piece 2 R1. The deliverable's audio rides a ~5.2 s fade to −72 dB under
the tail dissolve, never muted; our render leaves the mix hot to the
last frame. Closed with G14 above: `tail.audio_fade_frames` fades the
master mix's last N frames, ending where the mix ends — independent of
the dissolve's length, which is what makes the measured shape (fade
starting under the dissolve, finishing after it) expressible at all.

## G16 — pack blind spots the ending exposed (open)

Piece 2 R1 critics: (a) no audio-class track — judges could not tell
the −34 dB tail was applause ("if that tail is a crowd, A's black is a
defensible applause bed... the verdict narrows"); the
flatness/centroid method from the ending prep measures exactly this.
(b) the transition typer reports the human's visible 5.9 s dissolves as
hard — ±12-frame window cannot see a multi-second dissolve; must
classify against the human Taurus tail as ground truth.

## G17 — our builds read metronomic; shot-length spread is the gap (in-work)

P3R1 LOSS 1–2, and the "metronome" word has now appeared in FOUR straight
panels (P1R3, P2R2, P2R3, P3R1 — unanimous in the last). Ours: no shot
under 4.4 s, strict A/B alternation, callable by 30 s; cuts averaged
2.06 s from the nearest RMS accent per one judge. Human's same window:
2.25|13.05|2.59|17.02|3.29|5.84|10.30|3.59|2.79|11.22|5.05|7.59|2.54|21.02
— bimodal: 13–21 s holds through sparse trading + 2–4 s bursts at
transitions. Fixes in flight: style (bimodal spread; break the
alternation; accents matter at 0.5–1 s scale, not just onsets at 30 ms)
+ server (correlate report gains a shot_rhythm block with a
reads_metronomic heuristic as a builder self-review gate).

## G18 — the quiet floor reads locked-off even when its gear is right (in-work)

P4R2's remaining flank, carried out of a round we won 3–0 (STATE.md).
Ours holds the quiet passage (derived d38–195) at 6.88 cuts/min — the
0.74× gear the arc table asks for, hit — and still parks: shot-length
CV 0.597 against the human's 0.783 over the same passage, and the
trough at 79–157 s is five holds (17.1|12.9|10.3|20.7|14.4) with one
2.5 s flash punched through them. What makes this its own gap rather
than G17's is scale: G17 is the whole cut reading metronomic, this is
one passage parking while the cut around it varies enough to hide it.
On this pair the orphan correction does **not** change the verdict —
ours fails on the raw spread too (0.597, and 0.564 without the flash),
so the flash is named rather than decisive. It is dropped because a
spread a lone flash holds up is not a spread a viewer sees, not because
it flipped this reading. Fixed both sides (#190): style (quiet passages
keep their spread with no orphan flashes; what raises it is unequal
holds, a reframe or push-in where the footage has one, a scale change)
+ server (`gears.quiet_floor` finds the passages off a smoothed level
curve and reports `cv_less_orphans` / `reads_locked`, a blocker in
`docs/agents/concert.md`), merged as PR #198. **Open until a full-song
build passes it — #206 is that round.** The measurement and the rule
exist; the proving round does not. Both constants the check leans on
(the 0.65 floor, the 0.5× orphan fraction) are one song wide and
provisional — #191 tracks graduating or moving them.

## Round record

- **P3·R2 · mid-song trading window · WIN 2–1 — PIECE 3 CLOSED**
  (ours = B; pack `taurus_mid_p3r2`). Bimodal rebuild: shortest shot
  2.04 s, bursts at front changes, 15.6 s hold through the drum-feature
  core, accents-first placement (0.246 s mean vs R1's judged 2.06 s),
  correlate's new shot_rhythm gate clean pre-render. G17 fixes proven.
  Ours' recorded weaknesses for the capstone: a 25 s two-shot stall
  after a quickening; the loudest return (win-t 66.5, ~13 dB) passing
  unmarked inside a 10.3 s hold, cut 4.8 s late; the last crash caught
  0.78 s late. Human's: 17 s dead-static wide through the trade's
  middle, cuts ignoring the passage's two loudest accents.

- **R3 · Taurus opening · WIN 2–1 — PIECE CLOSED** (ours = A; sealed pack
  `taurus_opening_r3`, three fresh critics, grade excluded per ruling).
  The occlusion-aware rebuild removed both confirmed blocked shots
  structurally, kept the t=38 gesture, tightened the back half, and the
  pixel check matched the plan frame-for-frame (15/15 cuts). Panel
  consensus even among our voters: our back-half tightening ladder reads
  as a mechanical metronome (strict two-framing alternation, monotonic
  9.9→2.9 s) — next-piece style work; and the human's 30 s of parked
  statics drew all three judges' fire, confirming the bar's weak flank.
  Builder's tuning ledger for `analyze_occlusion`: precision 2/6 on FX6
  windows (dark piano lid + tiny parked corner head read as blocking);
  suggested fixes recorded in `gauntlet/recon/occlusion_verdict_r3.json`.
  Recurring nit → G13: built timelines inherit the project's 4K default
  and every round manually sets 1920×1080 before render.
- **P2·R1 · Taurus ending · LOSS 0–3, unanimous** (ours = A; sealed pack
  `taurus_ending_p2r1`). Root cause is a server limitation: the tail
  convention (5.9 s dissolve + audio fade) is inexpressible in schema
  v1, so ours hard-cut to black on the last note and sat 6.6 s dark →
  G14/G15. Critic 2 also read our body as "13 near-metronomic ~6 s
  cuts" — the ladder critique again, style not yet landing in builds.
  Human's recorded weaknesses: 21.3 s parked master framing tail (24%
  of runtime, "stops instead of ending"), and a 4-cuts-in-6.2 s burst
  in the quietest pocket. Pack blind spots → G16.
- **P2·R2 · Taurus ending · LOSS 0–3, unanimous** (ours = A; pack
  `taurus_ending_p2r2`). The tail device worked (dissolve landed at the
  deliverable's numbers, pixel-verified) and the body was the strongest
  build yet (four pictures, ride-the-reframe, quiet pocket held) — but
  the final cadence + applause + fade played on the A7IV drum cam:
  "the last thing the viewer sees is a drummer's back behind a cymbal."
  Style rule for R3: the set's last image belongs to the ensemble;
  decelerate into a free coda. Human's 24–34 s stutter named again by
  two judges.

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
- **R2 · Taurus opening · SPLIT 1–1** — critic 1 picked ours; the
  confirmation critic picked the human on objective grounds: three
  obstructed shots on ours (audience head/hat/back — G11), ours renders
  ungraded ("flat and milky", boundary min_luma 39–44 vs the human's
  22–26 — **G12, WITHDRAWN: director ruled 2026-08-13 that grading is
  out of scope; the grade half of this critic's case is void, and every
  future critic brief must say "ignore color/grade differences"**), and
  our back half read as metronomic to this judge
  (9.8/13.6/10.2/8.4/9.9 s) while the human's tightens across 52–76 s
  tracking secondary loudness bumps. Both judges agree ours wins the
  t=38 gesture. Piece stays open; close rule is now majority of three
  fresh critics. Original R2 record follows for the receipts:
  (ours = B). Fresh critic picked ours: the hard cut landing exactly on
  the t=38 drop and the motivated, held back half beat the human's
  metronomic quiet-passage cuts — the exact G8/G9 work. Ours: card
  frame-identical to the deliverable convention, 13 cuts each with a
  named measured motivation, 5 distinct framings vs the human's 3,
  camera moves treated as hard constraints. Critic's remaining ding on
  ours: the 9.8 s release shot contains a stretch half-blocked by an
  audience head — subject/blocking measurement is still-open G5 work.
  Second independent critic running before the piece closes.
  New gaps from the round: phrase detection over-calls on polyphonic
  `other` (36 boundaries in 90 s — candidates, not verdicts); split_wind
  can't run as a single pass through the tool path without redoing the
  whole stems key (and the lone-pass fallback died writing its second
  half, output tail uncaptured); the beats cache misses on the acquired
  copy because identity is keyed to the director's master (bit-identical
  audio, different fingerprint).
