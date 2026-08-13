# r1 diagnosis — "the 13-shot edit never reached the pixels"

**Date:** 2026-08-13 · **Subject:** `gauntlet/renders/taurus_opening_r1.mp4`,
timeline `Taurus People Opening v3`, cut file
`projects/mcp-tests-zinc/taurus-people-opening.cut.json`

## Verdict

**None of (a), (b), or (c). The reported symptom does not exist.** The render
*is* the 13-shot cut. The failure is in the **measuring instrument**:
`SCENE_THRESHOLD = 0.27` in `gauntlet/tools/ab_pack.py:45` sits above the
ffmpeg scene score of every angle change in this footage, so `detect_cuts`
reported 1 cut where there are 13. The "single continuous shot" was an
artefact of the A/B pack, never of the edit.

Root cause: **(d) the verification tool is miscalibrated.**

## Evidence

### 1. The render alternates angles — viewed, not inferred

Frames pulled from `taurus_opening_r1.mp4` (`gauntlet/recon/r1_frames/`),
sampled inside shots where the plan claims a given angle:

| t (s) | expected angle | what the frame shows |
|---|---|---|
| 2.0 | FX6 wide | front-on room wide: piano L, sax centre, drums R |
| 5.5 | A7IV | over-the-drums medium, sax in profile L |
| 12.5 | FX6 wide | front-on room wide |
| 19.5 | A7IV | over-the-drums medium |
| 33.0 | A7IV | over-the-drums medium |
| 37.5 | FX6 wide | front-on room wide, bassist centre |
| 50.0 | A7IV | over-the-drums medium |
| 73.0 | A7IV | over-the-drums medium |

8/8 match the built item at that record frame. Two visibly different cameras.

### 2. The built timeline matches the cut file frame-for-frame

Live read of V1 items (`gauntlet/recon/r1_items.py` → `r1_items.json`,
table in `r1_items.scratch.log`). `Taurus People Opening v3`, 13 items,
two distinct MediaPoolItem ids (`45885962…` = `A015C001_2606170J.MXF`,
`e9e5c74c…` = `20260617_D_A7IV_0006.MP4`), strictly alternating.

Every placed source range equals the authored one, exactly:

```
s01 fx6  cut 54396-54502   placed 54396-54502   rec 86413-86519
s02 a7iv cut 85772-85931   placed 85772-85931   rec 86519-86678
s03 fx6  cut 54661-54834   placed 54661-54834   rec 86678-86851
...
s13 fx6  cut 56207-56541   placed 56207-56541   rec 88224-88558
```

(v1 and v2 are earlier passes of the same 13-shot structure, ±1–2 frames on
some boundaries; all three alternate. v3 is the one that was rendered —
`taurus_render_hd.py:11` names it explicitly and sets it current first.)

Both clips report `Start = 0`, so the known `startFrame`-vs-`Start` hazard in
`resolve/build.py` (source frames passed verbatim, never rebased) is dormant
here and cannot be the cause.

### 3. Threshold sweep — the detector, not the edit

`gauntlet/recon/r1_scenesweep.py` → `r1_scenesweep.scratch.log`. Ground truth
= the 13 item boundaries of v3.

| threshold | render_full | pack_A/clip.mp4 | pack_B (human cut) |
|---|---|---|---|
| 0.02 | 14 (13/13 truth, 0 extra) | 13 (13/13) | 16 |
| 0.04–0.18 | **13 (13/13 truth, 0 extra)** | **13 (13/13)** | 13–15 |
| **0.27 (shipped)** | **1** | **1** | **9** |

Identical at scale 320 and 640, so it is not a downscale artefact. Detected
times at thr 0.06 are `[0.542, 4.963, 11.595, 18.81, 22.94, 32.616, 36.662,
49.049, 53.095, 61.27, 63.772, 71.238, 76.076]` — the v3 item starts to the
millisecond.

The whole plateau 0.04 → 0.18 gives exactly 13 with zero false positives.
0.27 is not marginally high; it is off the end of a wide correct band.

The footage explains why: one dim, warm, red-curtained room, two cameras with
overlapping subjects and near-identical grade. Inter-angle scene scores land
in 0.18–0.27. A threshold tuned for stock/broadcast material is blind here.

### 4. The A/B pack was corrupt on *both* arms

`pack_B` is the human's delivered cut of the same 90 s
(`\\TRUENAS\...\6-17 - Zinc Set 2 - Taurus People.mp4`, per
`taurus_opening_r1.SEALED.json`). At 0.27 it reported 9 cuts; it actually has
13. So the blind comparison — "A: 1 cut / 0.67 per min vs B: 9 cuts / 6.0 per
min" — was wrong in both columns and manufactured a dramatic difference
between two cuts that in truth have **the same shot count**. Any critic
judging from `manifest.json` was reasoning about fiction.

### 5. `correlate_timeline` was right, and its blind spot stayed dormant

`correlate_timeline` reported cuts 14, and per-clip 7 × FX6 (70.3 %) /
6 × A7IV (29.1 %) / 1 black (0.6 %) — matching the pixels and the plan's
claimed ~70 % FX6. Transient offsets: 13 measured, median 32 ms, max 43 ms.

The known blind spot is real but did **not** fire: correlate counts *item
boundaries*, not visual change, so a same-clip-contiguous timeline would still
read as N cuts. Here the items genuinely differ, so correlate and the pixels
agree. Worth keeping on the risk list; not implicated in this incident.

## Why the losing hypotheses lose

- **(a) cut file / builder arithmetic.** The zeros were applied correctly.
  `taurus_plan.py:110-116` computes `in = REC_IN + a - zero` per angle with
  `FX6_ZERO = 117576`, `A7_ZERO = 86306`, `MIX_ZERO = 86401` — the right zero
  for each source. Spot check: s02 in `85772 + 86306 = 172078` = record frame
  of s01's out `54502 + 117576 = 172078`. Record-contiguous, source-distinct.
  Exactly what a multi-angle cut should look like.
- **(b) server placed items wrong.** Placed source ranges are byte-identical
  to the cut file for all 13 segments (§2). `build_timeline` reported
  `segments 13, gaps 1, audio true, warnings []`, and `_verify` read the
  items back. No mismatch to name.
- **(c) render pulled wrong content.** `render_timeline` was given
  `timeline="Taurus People Opening v3"` explicitly after `SetCurrentTimeline`;
  output is 2158 frames @ 23.976 = 90.027 s (timeline duration 2158 frames),
  and the pixels at eight sample times match the eight corresponding items.

## Fixes implied

1. **`gauntlet/tools/ab_pack.py:45` — lower `SCENE_THRESHOLD` to `0.10`.**
   Centre of the 0.04–0.18 plateau; 13/13 with 0 false positives on both arms
   and on two scales. This alone fixes the incident.
2. **Make the pack self-checking.** When a pack is built from a Resolve
   timeline, the item count is known for free — `detect_cuts` finding fewer
   than ~80 % of the known item boundaries should abort the pack, not seal it.
   A verification tool that can silently report 1/13 and still emit a
   confident `manifest.json` is worse than no tool.
3. **Never let a single detector be the sole witness.** The blind A/B compare
   consumed only `cuts.json` + contact sheets. Cheap tell that was already in
   the artefacts and got ignored: `A/sheet_1.jpg` is 15 KB (2 thumbnails) vs
   `B/sheet_1.jpg` at 122 KB (10) — and shot 01 on A's sheet is pure black.
   A sheet whose first shot is black and whose second is 89.5 s long is a
   detector failure signature, not an edit.
4. **Record the shot-count sanity check in the workflow.** "Scene detection
   count ≈ timeline item count" belongs next to the mandatory
   `correlate_timeline` self-review in `docs/agents/concert.md`: correlate
   proves the *timeline*, scene detection proves the *pixels*, and the two
   disagreeing by 12 is the alarm.

## No server bug found

`resolve_mcp` cut authoring, `build_timeline`, and `render_timeline` all
behaved correctly end to end on this ticket. The four structural risks the
build-path audit surfaced (raw `startFrame` with no `Start` rebase; no
clamping and fail-open E5 on bounds-less clips; `_append` not comparing the
returned list length; `_verify` never reading source frames back) are real but
none of them fired here — worth their own tickets, not this one.
