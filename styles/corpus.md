# The style corpus

Which timelines the profiles are learned from, in what order, and how far each
one got. Every `[measured — …]` tag in `base.md` or `concert.md` points back
here; a claim whose evidence is not in the measured table is not measured,
whatever its tag says.

The selection is settled in **#21** — it is the director's call, not an
inference from project names, and it is closed: *"judged sufficient for v1 — no
further corpus hunting."* The list below is that decision in working form.

The corpus is **ordered**. First entries get labelled and measured first and
seed the profile's first draft, and where old and new work disagree the profile
resolves toward the recent (#21 policy 2 — taste beats recency for membership,
recency breaks ties).

## The list

| # | Project | Timeline(s) | Context | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | `2026-06_Zinc_and_Monkfish` | Zinc - Set 2 Main | concert | **measured, labelled** | **Anchor** — strongest current-taste exemplar; two-camera, 2026 |
| 2 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Freefall Timeline, Sunshine Timeline, Mercies Timeline | concert | **measured, labelled** | Canonical snapshot for Ryan and Hang; music-performance cuts only |
| 3 | `Mike Tucker Scullers` | Concert Full Cut | concert | **measured, labelled** | Good two-camera concert; older, so recency weighting applies |
| 4 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Stablemates | concert | not started | Older but explicitly taste-endorsed. Read from the Ryan and Hang project, not `Ryan Devlin Projects Current` — same timeline, one project to open |
| 5 | `2026-06_Zinc_and_Monkfish` | Monkfish Main | concert | **measured, labelled** | **Partial** — only a couple of tunes cut; measure those tunes only |
| 6 | `Archive/Client/Side Step Blues Clues Album` | Blues Clues Main, Devlin Time Main, For All The Other Times Main, Intro Main, Outro Main, People We Love Main, Walk Spirit Talk Spirit Main, EJ's Blues Main | studio-session | **deferred** | Footage not on the box; and studio-session is a different cutting context from the concert work this pass is about. Revisit when the concert profile is settled |
| 7 | ~~`…Ryan and Hang…` Blues for Alice, Three Card Molly~~ | — | — | **dropped** | Confirmed single-camera (director, 2026-08-07) — no angle switches, so nothing to measure |

7 concert-context timelines across **3 projects** — entries 1 and 5 are both
`2026-06_Zinc_and_Monkfish`, entries 2 and 4 are both the Ryan and Hang gene
edit, and entry 3 stands alone. Worth stating plainly because #21 policy 4
counts *projects*, not timelines: measuring all seven would still leave every
claim resting on three, and six of the seven are already done. The 8
studio-session timelines are deferred, not excluded. Full-set timelines carry
many tunes, so per-cut counts run into the hundreds.

Entries 4, 6 and 7 were settled by the director on 2026-08-07: Stablemates
reachable from the entry-2 project, Side Step deferred, and the conditional
entry resolved single-camera without needing a pre-flight.

**Every measured timeline is now labelled.** The director supplied the two
mappings that no render or frame grab could give (2026-08-07): Scullers Angle 1
is the moving, tighter camera and Angle 2 the fixed wide side-view; Monkfish
Video 1 is the moving camera and Video 2 the fixed drum angle. Both timelines
were re-measured with the sidecars attached, and **every timing number came
back identical** — same cut counts, same offsets, same shot lengths — which is
the point of re-running rather than naming the shares by hand: labels are
supposed to add names and nothing else, and here they demonstrably did.

**Entry 4 is the only one left, and it is blocked on a clock rather than on
access.** Stablemates has no render, and its A1 audio items disagree about
where the mix starts — 86391 from one, 86055 from another — because each
multicam angle carries its own source offset. Every other entry had either a
clip carrying the analysed mix or a render whose duration proved the frame.
This has neither, so there is no number to name and defend, and it waits for
a full-timeline export or the director rather than getting measured against a
guess. Its 19 shots are the smallest sample in the corpus; publishing them
against the wrong zero would cost more than leaving them out.

## Every measurement below was made on the CPU torch build

**Open, 2026-08-15 (#245).** The beat grid and the applause curve now infer on
CUDA — the analysis extra sources torch from the cu130 wheel index on Windows,
where it used to get PyPI's CPU build. Every `[measured — …]` row in this file
predates that flip and was computed on the CPU one.

The re-computation is **pending**: per corpus project, beat grid and applause
curve are recomputed on the CUDA build and diffed against the stored CPU
results. A beat grid survives if its max delta is under one frame at the
timeline's fps; an applause curve survives if no span appears, disappears or
moves a boundary by more than a frame. Rows that survive get a line saying they
were confirmed on the CUDA build; rows that do not are re-derived, and the
profile claims resting on them are re-tagged per
`docs/agents/style-layer.md`. The tolerance and the reasoning are recorded in
`docs/reference/compute-device-inventory.md`, "The torch decision".

Until that diff is recorded on #245, read every tag below as measured on the
CPU build — the numbers are what they always were, but nothing has yet
confirmed the GPU produces them.

## Excluded, and why

- **Jaded Symphony - The Sinclair** — colour grade only; the cutting is not the
  director's.
- **6-17-18 Zinc Bar and Monkfish** — mislabelled copy of
  `2026-06_Zinc_and_Monkfish`; ignore entirely.
- **Five Spot January 2025**, **Zinc Bar August 2025 import**, **Judson's Album
  Promo** — single-camera.
- **Aberdeen and Everything Yes at Rockwood Boston** — audience clips, not a
  concert cut.
- All Ryan and Hang podcast / interview / reel / promo timelines — out of
  corpus.
- Duplicate Ryan and Hang snapshots (Main in 4-30-24, 5-17-24 Backup, and
  Ryan Devlin Projects Current for overlapping timelines) — one canonical
  source per timeline, no double-counting.

**Single-camera timelines leave the corpus at labelling time without a
re-decision** (#21 policy 1): angle-switch behaviour is the core signal, so a
timeline that turns out to have one camera simply drops out.

## Pre-flight, per project

Run when the project is opened, because offline `Project.db` inspection can
enumerate timelines but not media paths — path data lives in blobs, so link
status is only verifiable live through the scripting API.

- [ ] Media linked — spot-check `GetClipProperty("File Path")` on timeline
      clips; relink if archived media moved. Everything under `Archive/Client`
      is a likely relink candidate; entries 1 and 5 are recent and expected
      clean.
- [ ] 2+ source angles — drops any timeline that turns out single-camera.
- [ ] Angle→camera mapping confirmed by the director. Resolve will not say
      which source is behind an angle number, so every multicam project needs
      one look before its role claims mean anything; the screen-time heuristic
      that worked on entry 1 is a starting guess, not an answer.
- [ ] Concert / master audio reachable for the analysis pipeline (#19).
- [ ] Frame grabs render and the sidecar can be written (#13).

## Measured

One row per timeline, appended in order, never rewritten: a rerun that
disagrees with an earlier row gets its own row and a note, because the
disagreement is itself evidence.

The per-entry cautions below that treat rubato gating as pending were written
before the gate existed; **the gated pass has since run** and answers them, in
"The gated pass (#112 AC4)" further down. Every bar-position histogram in the
entries below is ungated and half of them are now known to have come from a
grid the gate refuses whole — read them with that section beside them.

`alignment` is `correlate_timeline`'s reading of where the times were measured
from. **`timeline_start` rows are excluded from every timing claim** — those
times count from the timeline's own first frame rather than from the mix, and a
file measured against the wrong zero looks exactly like a file measured against
the right one.

| # | Timeline | Context | Cuts | Alignment | Sidecar | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Zinc - Set 2 Main | concert | 366 (360 measured, 6 openings) | `audio_clip`, matched | `angles/2026-06_Zinc_and_Monkfish.json` (confirmed 2026-08-07) | `…/analysis/Zinc---Set-2-Main-dcb16e19eca1.correlate.json` |
| 2 | Freefall Timeline | concert | 44 (43 measured, 1 opening) | `given` @ 86400 | `angles/Ryan-and-Hang-Main-9-23-24-gene-edit.json` | `…/analysis/Freefall-Timeline-a82bc5a80bcd.correlate.json` |
| 2 | Sunshine Timeline | concert | 50 (49 measured, 1 opening) | `given` @ 86400 | same | `…/analysis/Sunshine-Timeline-edbbc561a240.correlate.json` |
| 2 | Mercies Timeline | concert | 38 (37 measured, 1 opening) | `given` @ 86400 | same | `…/analysis/Mercies-Timeline-622d697cfd3e.correlate.json` |
| 3 | Concert Full Cut | concert | 349 (326 measured, 23 openings) | `given` @ 75903 | `angles/Mike-Tucker-Scullers.json` (director, 2026-08-07) | `…/analysis/Concert-Full-Cut-fcbbcd2a1999.correlate.json` |
| 5 | Monkfish Main | concert | 233 (228 measured, 5 openings) | `audio_clip`, matched | `angles/2026-06_Zinc_and_Monkfish.json` (director, 2026-08-07) | `…/analysis/Monkfish-Main-8aaccfcad94d.correlate.json` |

Entries 3 and 5 have new result filenames because `angles` is part of the cache
key: the unlabelled runs are still on disk as `Concert-Full-Cut-bc11cb456a36`
and `Monkfish-Main-a14547f8db48`, and diffing them against the rows above is
how "the labels changed no number" was checked.

**Entry 1, measured 2026-08-06.** Against `Zinc Set 2 Reaper v4.wav` (74:10,
48 kHz, the Reaper master mix on A3), beats from `analyze_music` (11,130 beats,
4,831 downbeats). Two angles, both multicam: Video 1 holds 188 cuts and 68.5%
of screen time (13.8 s a shot), Video 2 holds 178 cuts and 31.5% (6.7 s a
shot) — a home angle held long against an angle cut to briefly.

Labelled from frame grabs off the multicam's source clips: an operated
front-of-house **wide** on the FX6, and a locked-off **drummer cam** on the
A7IV at stage right. **Which angle number is which camera could not be read** —
Resolve's API does not expose the multicam's angle mapping — so the sidecar
assigned the wide to Video 1 from the screen-time shape, and the director
confirmed that reading on 2026-08-07. The screen-time heuristic was right here;
it is one data point, and every other multicam project still needs its own look.

| Measure | Value |
| --- | --- |
| Offset to nearest transient | median 33 ms, mean 46 ms, max 345 ms; 195 early / 163 late / 2 on |
| Offset to nearest beat | median 88 ms, mean 1.31 s, max 43.5 s; 194 early / 165 late / 1 on |
| Shot length | median 7.26 s, mean 10.39 s, min 0.46 s, max 71.07 s |
| Bar position | 1:171, 2:111, 3:44, 4:30, 5:2, 6:2 — **not usable, see below** |

Two cautions on this row:

- **The beat grid does not fit this music.** `analyze_music` reported
  `tempo_bpm: 214.29` with `meter: 1` over a jazz set, and the beat-offset mean
  (1.31 s) sits fifteen times its own median (88 ms) — a grid that fits in
  places and wanders in others. The bar-position histogram is derived from that
  grid, so it says nothing yet, and the beat-offset numbers are worth much less
  than the transient ones. This is the rubato gating #13 calls for, not yet
  applied — now tracked as **#112**, which is scoped to settle the question in
  either direction.
- **The transient numbers do not depend on the grid.** Onsets are measured off
  the mix directly, which is why they are the row's trustworthy half — and
  they are also the half the profile's core principle is about.

**Entry 2, measured 2026-08-07.** The three Judson's quartet tunes. Each was
measured against the audio of its own finished render rather than a master mix
file: the masters for Sunshine and Mercies are offline (`C:\…\REAPER Media\`),
and more to the point *no* clip on these timelines is the mix — the music
reaches the cut through the multicam's own audio angle, whose in point belongs
to the multicam's timebase. Alignment is `given` at frame 86400, the timeline's
first frame, which is exact because each render's duration matches its timeline
to within 0.02 s — under a frame at 23.976.

| Measure | Freefall | Sunshine | Mercies |
| --- | --- | --- | --- |
| Cuts measured | 43 | 49 | 37 |
| Offset to nearest transient | median 34 ms, mean 53 ms, max 340 ms | median **17 ms**, mean 34 ms, max 263 ms | median 19 ms, mean 27 ms, max 94 ms |
| Direction | 25 early / 18 late / 0 on | 22 early / 26 late / 1 on | 17 early / 19 late / 1 on |
| Offset to nearest beat | median 63 ms, mean 228 ms, max 6.08 s | median 124 ms, mean 307 ms, max 6.34 s | median 135 ms, mean 146 ms, max 434 ms |
| Shot length | median 6.82 s, mean 15.51 s, 2.54–146.4 s | median 12.01 s, mean 18.34 s, 2.75–93.6 s | median 11.53 s, mean 18.52 s, 1.59–183.9 s |
| Bar position | 1:18, 2:17, 3:4, 4:4 | 1:21, 2:12, 3:10, 4:6 | 1:25, 2:6, 3:2, 4:3, 6:1 |

Angles labelled by **reading the render**, not by inferring from the sources:
a frame of the finished cut at a moment a given angle is on screen *is* that
angle. This is the method the anchor could not use — it has no render — and it
removes the guesswork that made entry 1's mapping need a director's eye.

Three roles, and they recur in all three tunes:

| Role | Freefall | Sunshine | Mercies |
| --- | --- | --- | --- |
| `soloist-moving` — operated camera following whoever is playing | 19 cuts, **75.5%** | 24 cuts, **76.3%** | 17 cuts, **74.4%** |
| `ensemble-wide` — locked full-stage wide | 12 cuts, 14.4% | 16 cuts, 17.0% | 11 cuts, 16.3% |
| `drums-tight` — drummer cam at house right | 13 cuts, 10.1% | 10 cuts, 6.7% | 5 cuts, 4.0% |
| `room-wide` — second wide, from a seat | — | — | 5 cuts, 5.2% |

Cautions on these rows:

- **The beat grid fits better here than on the anchor but is still not gated.**
  Freefall's histogram is strictly 1–4, which is what a grid that fits looks
  like; Mercies puts one cut in a bar 6, which is what one that slips looks
  like. Beat-offset means still run two to five times their medians. Treat the
  bar histograms as suggestive and the transient numbers as evidence, exactly
  as on entry 1.
- **The role names are a judgement, the mapping is not.** Which angle is which
  camera was read off the render and is not in doubt. Whether an operated
  camera that follows the music is best called `soloist-moving` is a vocabulary
  choice, and it is the one term here that did not come from the anchor.

**Entry 3, measured 2026-08-07.** `Mike Tucker Scullers` / `Concert Full Cut`,
349 shots over 95 minutes, two angles. **Labelled by the director on
2026-08-07** — this project has no render and no reachable source frames, so
the mapping could not be read the way entries 1 and 2 were, and a sentence is
all the evidence there is. Angle 1 is the moving, tighter camera; Angle 2 the
fixed wide from the side of the room.

That makes this **the only labelled rig in the corpus without a drummer
camera**, and the one place where the home angle and the wide are demonstrably
different cameras: the wide is fixed and holds a quarter of the cut, while the
moving camera holds three quarters. The sidecar records that "moving" and
"fixed wide" are stated while the moving camera's *subject* is not — nobody has
looked at what it points at — so this timeline supports claims about framing
and operation and is deliberately not counted toward claims about subject.

The master mix is 32-bit float, which `analyze_music` refuses outright
(#110 — and its `fix` line tells the caller to delete the file, which here is
the director's own master on a media drive). Worked around by transcoding to
24-bit PCM with ffmpeg: duration is preserved to the sample, 6130.888708 s both
ways, so no time measured off it moved. Because the transcoded file is not the
one on the timeline it cannot be matched to a clip, so the clock is `given` at
frame 75903 — which is not a guess but what the eighteen A2 clips themselves
say (`record_in - source_in`, the same value on every one of them). The cut
starts about 7½ minutes into the recording, which is why that number sits
before the timeline's own first frame.

| Measure | Value |
| --- | --- |
| Cuts | 349 shots, 326 measured, **23 openings** |
| Offset to nearest transient | median 29 ms, mean 51 ms, max 497 ms; 141 early / 185 late / 0 on |
| Offset to nearest beat | median 97 ms, mean 846 ms, max 22.7 s |
| Shot length | median 8.59 s, mean 16.22 s, min 0.38 s, max 238.9 s |
| Bar position | 1:203, 2:55, 3:38, 4:27, 5:1, 6:1, 7:1 — **not usable** |
| Angles | `soloist-moving` (Angle 1): 182 cuts, 74.3%; `ensemble-wide` (Angle 2): 167 cuts, 25.7% |

Three things worth carrying forward:

- **23 openings** is far more than any other entry (1 each on the Judson's
  tunes, 6 on the anchor). A full-set timeline has gaps between tunes, and a
  shot after a gap has no outgoing angle — so the opening count is roughly the
  tune count, and it is a free structural signal nothing is using yet.
- The **home angle share is 74.3%**, in line with every other entry, and it was
  arrived at before any label existed. The share is a fact about the
  distribution; only the *name* of the angle needs a sidecar — which is exactly
  why the label, when it arrived, moved no number.
- **The second angle here is a wide, and it behaves like the drummer cams do
  elsewhere**: 167 cuts against the home angle's 182, on a quarter of the time.
  Frequent-and-short is a property of being the *other* angle, not of pointing
  at the drums — which only became visible once a rig without a drum camera was
  labelled.
- The bar histogram has positions 5, 6 and 7 in it. There is no such thing in
  4/4. Same conclusion as entries 1 and 2: rubato gating first (#112).

**Entry 5, measured 2026-08-07.** `Monkfish Main`, 233 cuts over 87 minutes,
two angles, **labelled by the director on 2026-08-07** — same project as the
anchor but a different venue five months earlier, and no render, so the mapping
was neither carried over from the Zinc sidecar nor guessed. Video 1 is the
moving camera, Video 2 the fixed drum angle.

Both nights in this project happen to put the operated camera on Video 1 and
the drums on Video 2, so the *numbering* did survive the five months — but
**Video 1 is a wide at Zinc and a moving camera at Monkfish**, which is the
thing the number cannot tell you and the reason the sidecar labels the two
nights separately rather than once. Guessing from the anchor would have got the
home angle right here and its framing wrong.

Alignment is the cleanest of any entry:
`audio_clip`, matched, against `140111-061037.WAV` on A1 with its zero exactly
at the timeline's first frame. Nothing had to be named or transcoded.

**This timeline is only partly cut, and that decides which of its numbers
count.** #21 flagged it and the shots confirm it: five held stretches on
Video 1 — 32.5 min at 17.8 min in, then 10 min, 5 min, 2.6 min and 2.1 min —
account for 3126 s of the 5241 s. They are passages nobody has cut yet, not
shots anybody chose to hold.

| Measure | Value | Comparable? |
| --- | --- | --- |
| Offset to nearest transient | median 41 ms, mean 54 ms, max 433 ms; 118 early / 110 late / 0 on | **yes** — each of the 228 is a cut somebody made |
| Shot length, median | 6.30 s | **yes** — a median over 233 shots is untroubled by five outliers |
| Shot length, mean | 22.49 s (max 1953 s) | **no** — the held stretches are most of the running time |
| Angle share | `soloist-moving` (Video 1) 89.3%, `drums-tight` (Video 2) 10.7% | **no** — share is time, and the uncut time is all Video 1 |
| Angle cut counts | 125 vs 108 | **yes** — a cut count is not inflated by an uncut passage |
| Bar position | 1:96, 2:57, 3:36, 4:39 | see below |

Two notes:

- **This is the second grid that looks structurally sound** — strictly 1–4,
  `meter: 4`, like Freefall and unlike the anchor, Mercies and Scullers. It is
  also the *flattest* histogram in the corpus: 67% on beats 1–2 against 78–81%
  everywhere else. That is the wrong direction for comfort. The timelines whose
  grids are demonstrably broken show the *strongest* beat-1 skew, which is what
  you would expect if part of that skew is the detector snapping to cuts rather
  than the director cutting to beats. Another reason no beat-position claim
  goes in the profile before rubato gating (#112).
- The transient median (41 ms) is the highest in the corpus and still inside
  two frames. Six timelines now span 17–41 ms.

## The gated pass (#112 AC4), measured 2026-08-07

`correlate_timeline` gates the beat statistics. A beat is refused on either of
two independent grounds — a bar position its own meter cannot hold, or an
interval that does not match the tempo around it — and a grid whose meter comes
out as 1 is refused whole rather than filtered, since keeping only its
position-1 beats would leave a histogram reading 100% beat one *by
construction*. Cuts landing on a refused beat stay in the records marked
`in_grid: false` and are counted out of the bar and beat-offset statistics only;
the transient numbers are computed over every cut and are untouched.

**The pass has now run.** Each of the three projects was opened in Resolve in
turn and each timeline re-measured through `correlate_timeline` with
`refresh=True`, on the live Windows 11 box against Resolve Studio 21.0.3.7 on
CPython 3.12.10 — and the attach held throughout, so that interpreter is not a
uv-managed standalone (ADR 0001, where the failure is a process-killing access
violation rather than an error).
Every parameter — beats file, audio, `angles`, `track`, `audio_at` — was copied
verbatim out of the matching pre-gate `.correlate.json` header, so the gate is
the only thing that differs between an entry's row above and its row here. No
number below was computed anywhere but in the tool.

| # | Timeline | Meter | Cuts in span | Gated out | Measured | Bar position, gated | Beat offset: median / mean / max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Zinc - Set 2 Main | **1** | 360 | 360 | **0** | — refused whole | — |
| 2 | Freefall Timeline | 4 | 43 | 8 | 35 | 1:15, 2:14, 3:2, 4:4 | 69 ms / 249 ms / 6.08 s |
| 2 | Sunshine Timeline | **1** | 49 | 49 | **0** | — refused whole | — |
| 2 | Mercies Timeline | **1** | 37 | 37 | **0** | — refused whole | — |
| 3 | Concert Full Cut | 4 | 326 | 108 | 218 | 1:128, 2:40, 3:27, 4:23 | 81 ms / 102 ms / 324 ms |
| 5 | Monkfish Main | 4 | 228 | 26 | 202 | 1:80, 2:49, 3:34, 4:39 | 84 ms / 94 ms / 390 ms |

"Cuts in span" is `count − openings`: an opening has no outgoing angle and no
offset to measure. The transient columns in the entries above are unchanged by
this pass, as designed — the gate never touches them.

**Half the corpus never described a bar position in the first place.** The
offline prediction held exactly: three of the six grids report `meter: 1` and
are refused whole. Every beat in all three is refused on bar position — 11,130
on the anchor, 2,348 on Sunshine, 1,261 on Mercies — and their bar histograms
were therefore never evidence, whatever the entries above appeared to say. The
anchor is the expensive case: 366 shots, the strongest exemplar in the corpus,
and it contributes nothing to any beat claim. It still contributes its 360
transient measurements, which is the half that was always the trustworthy one.

**On the three that survive, the gate removed a tail rather than a centre.**

| Timeline | Beat-offset mean, ungated → gated | Max, ungated → gated | Median, ungated → gated |
| --- | --- | --- | --- |
| Freefall | 228 ms → 249 ms | 6.08 s → 6.08 s | 63 ms → 69 ms |
| Concert Full Cut | 846 ms → **102 ms** | 22.69 s → **0.324 s** | 97 ms → 81 ms |
| Monkfish Main | 457 ms → **94 ms** | 13.92 s → **0.390 s** | 95 ms → 84 ms |

Ungated, every mean ran three to nine times its own median — the signature of a
grid that fits in places and wanders elsewhere, and the reason entry 1's row
carries that caution. Gated, Scullers' and Monkfish's means sit just above their
medians (102 against 81 ms, 94 against 84 ms) and their worst cut is under
0.4 s from a beat, where ungated it was 23 s and 14 s. The medians barely moved,
which is the useful part: the gate is not shifting where cuts sit, it is
deleting the passages where the grid could not say.

**The bar histograms are now strictly 1–4.** Positions 5, 6 and 7 — the ones
that cannot exist in 4/4 and that made the ungated histograms unusable — are
gone from every surviving row, refused rather than argued away.

Summing the three surviving rows (three timelines, one from each of the three
projects, 455 cuts):

| Beat of the bar | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- |
| Cuts | 223 | 103 | 63 | 66 |
| Share | **49.0%** | 22.6% | 13.8% | 14.5% |

Against a uniform 25%, that is a real skew to the first beat, and 71.6% of cuts
fall on beats 1–2. **Gating did not manufacture it and did not remove it**: over
the same three timelines the ungated share on beat 1 was 53.1%, so refusing a
third of the beats moved it four points. That is the shape of a finding rather
than of an artefact — an artefact of a broken grid should have collapsed when
the broken grids were refused.

One confound stays attached, and the gate cannot address it: `beat_this` places
downbeats from the audio, and a downbeat detector keys on strong onsets, which
is also roughly where cuts land. Some part of a beat-1 skew could be the
detector agreeing with the director about where the strong moments are rather
than the director cutting to bar lines. Nothing measurable here separates the
two; it would take a grid from a source independent of the mix — a click track,
or a hand-tapped grid.

### `UNSTEADY_FRACTION = 0.15` stands — it is a shoulder, not a cliff

The one constant in the gate with no fake-tier ground truth. It was checked the
way #35's silence defaults were: run the same tool over the same timelines with
only the constant changed, and see whether any conclusion depends on it. 0.15
was run last of the four so the artifacts left on disk are the shipped value's.

| Timeline | | 0.10 | 0.15 | 0.20 | 0.30 |
| --- | --- | --- | --- | --- | --- |
| Freefall | measured | 34 | 35 | 38 | 38 |
| | median | 66 ms | 69 ms | 61 ms | 61 ms |
| | beat-1 share | 41.2% | 42.9% | 42.1% | 42.1% |
| Concert Full Cut | measured | 207 | 218 | 225 | 231 |
| | median | 82 ms | 81 ms | 80 ms | 82 ms |
| | beat-1 share | 58.0% | 58.7% | 60.0% | 59.7% |
| Monkfish Main | measured | 199 | 202 | 204 | 205 |
| | median | 86 ms | 84 ms | 86 ms | 87 ms |
| | beat-1 share | 39.7% | 39.6% | 39.2% | 39.0% |

Over a **threefold** range of the constant, the surviving cut count moves by
about a tenth, no median moves by more than 8 ms, and no beat-1 share moves by
more than two points. Nothing in this corpus is sensitive to it, so 0.15 is
confirmed by the pass rather than merely left alone — and the honest reading is
that the number is not load-bearing, not that it is precisely right. The three
`meter: 1` refusals are untouched at every value: that refusal comes from the
meter rule, and no setting of this constant rescues them.

### What the gate does not fix: a cut beside a refused stretch

Freefall keeps a cut 6.08 s from its nearest beat at every setting, and it is
the reason Freefall's mean still sits 3.6× its median while the other two came
into line. The record shows why: the cut at t=676.802 is 34 ms from a transient
and marked `in_grid: true` with `in_bar: 1`. The gate refuses *beats*, not cuts
— so where it refuses a run of beats, a cut sitting in that hole is still scored
against the nearest **surviving** beat, however far away that is. It is one cut
in 455 and it does not touch the histogram, but a mean is not safe from it.
The clean fix would be to refuse a cut whose nearest trusted beat is further
than some multiple of the local beat interval; that is a change to the gate, not
to this corpus, and one instance across 455 cuts is thin justification for
making it. Recorded here so the next mean that looks wrong has an explanation
waiting.

**Fixed in #160**, and the diagnosis above was half right. The cut is not in a
hole *inside* the grid: Freefall's grid ends at 670.72 s and the cut is at
676.80 s, so the nearest surviving beat is the last one the detector emitted,
6.08 s earlier, in a stretch whose beats are 0.39 s apart. (An interior hole
mostly gates itself — the steadiness check refuses both beats of an aberrant
interval, so the nearest beat to a cut in the hole is usually a refused one.)
The gate now refuses a cut further from its beat than a beat is wide and counts
it as `stranded`, apart from `gated`. Every beat figure in this document
predates that refusal, and no pass has been re-run against it, so what follows
is a prediction and not a measurement. This cut at least leaves `beat_offsets`
and `bars` — Freefall 35 measured → 34 in the gated pass and 50 → 49 in the
visible-edit one, one off the beat-1 column in each, and the `max_abs` of 6.08 s
with it — while the medians (69 ms, 80 ms) are untouched, a median never having
noticed the cut. The refusal is corpus-wide, though, so any other cut sitting
more than a beat from a trusted beat — at a grid's *start* as well as its end —
would go too, and only a re-run settles how many that is. Note also that this
cut is past the end of its grid and so was already counted in `outside_grid`;
the two counts overlap on it, as they may on any stranded cut beyond the ends.

### Two bookkeeping consequences of re-running

- **The anchor's clock moved one frame, and the gate did not do it.** Zinc's
  alignment now reads `zero_frame: 83824` where the pre-gate row read 83825 —
  that is **PR #121**'s fix to #120, `GetSourceStartFrame` being lossy by a frame
  at 23.976, reaching a second reader. The `audio_clip` zero is derived from it.
  Nothing in the entry-1 row above changes materially — median transient offset
  is still 33 ms and the mean still 46 ms — but the early/late split moved from
  195/163 to 174/183, which is what shifting every measurement by 41.7 ms does
  to a distribution whose median is 33 ms. It reinforces entry 1's own note that
  the direction split carries no habit: a one-frame clock correction reverses it.
  Monkfish is also `audio_clip` and did **not** move, because its audio starts
  exactly on the timeline's first frame and had no fraction to lose.
- **Five of the six result files were overwritten in place.** The cache key
  covers the inputs and the parameters, not the version of the code, so a
  re-run with identical parameters lands on the same filename. The `Result`
  paths in the Measured table above now hold *gated* content; the ungated
  numbers survive in this document and nowhere else. Zinc is the exception —
  its key changed with the one-frame clock, so its gated result is
  `…/analysis/Zinc---Set-2-Main-1cc8ebd14cb3.correlate.json` and its ungated
  `dcb16e19eca1` file is still on disk.

### #40's live AC, discharged the same sitting

`test_correlate_measures_a_real_hand_edited_timeline` had never been recorded
against a real cut. Rather than decide whether this corpus pass supersedes it,
it was run: `RESOLVE_MCP_CORRELATE_BEATS` / `_TIMELINE` / `_AUDIO` pointed at
Monkfish Main and its master, `uv run pytest -m live -k correlate -s` — **1
passed in 30.79 s**, on the same box and Resolve build. It ran without an
`angles` sidecar and into pytest's own temporary cache, and still reported
`measured: 202`, `median_abs: 0.084`, `max_abs: 0.39` — identical to the
labelled run in the table above. That is one more independent demonstration
that labels add names and move no number.

## What the labels settled: operation, not framing

With entries 3 and 5 labelled, all six measured timelines have roles, and one
question that had been stuck on a single project comes free. Review had already
knocked the claim "the home angle is the camera on whoever is playing rather
than the wide" down to `[believed, unverified]`, because the anchor's home
angle is its wide *and* its operated camera at once and so cannot tell the two
readings apart. The two new labels break that tie — not by adding a third vote
for one reading, but by supplying the cases where framing and operation
disagree.

| # | Timeline(s) | Home angle | Framing | Operated? | Other angle | Framing | Operated? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Zinc - Set 2 | `ensemble-wide` 68.5% | wide | **yes** | `drums-tight` 31.5% | tight | no |
| 2 | Freefall, Sunshine, Mercies | `soloist-moving` 74–76% | moving | **yes** | `ensemble-wide` 14–17% | wide | no |
| 3 | Concert Full Cut | `soloist-moving` 74.3% | moving | **yes** | `ensemble-wide` 25.7% | wide | no |
| 5 | Monkfish Main | `soloist-moving` (89.3%, share not usable) | moving | **yes** | `drums-tight` 10.7% | tight | no |

**Framing does not predict the home angle: a wide holds 68.5% on entry 1 and
25.7% on entry 3.** Operation predicts it every time — six timelines, three
projects, and **no locked-off camera is ever the home angle**. Entry 3 is what
makes this checkable at all, being the only rig whose fixed camera is a wide;
without it, "wide" and "locked off" name the same cameras throughout the corpus
and no evidence could separate them.

### The mechanism, and the hardest number in the corpus

The director's account of *why* (2026-08-07): the locked camera is spent on the
drums because the drums are the rhythm section's most watchable instrument,
which frees the operated camera to stay with the soloist — and to roam the band
for movement and interesting shots, since that is where interesting shots come
from.

That account predicts something checkable, so it was checked. Splitting all
1080 shots by role:

| Role | n | median | mean | >30 s | longest |
| --- | --- | --- | --- | --- | --- |
| `drums-tight` (locked, all 6 timelines) | 314 | 5.25 s | 6.1 s | **0 (0.0%)** | **21.5 s** |
| `ensemble-wide`, the 4 locked instances | 206 | 7.3 s | 8.7 s | 2 (1.0%) | 57.4 s |
| `ensemble-wide`, the 1 operated instance (anchor) | 188 | 10.7 s | 13.9 s | 17 (9.0%) | 71.1 s |
| `room-wide` (locked, Mercies) | 5 | 7.5 s | 7.4 s | 0 (0.0%) | 12.9 s |
| `soloist-moving` | 242 | 14.4 s | 24.6 s | 58 (24.0%) | 238.9 s |

**The `drums-tight` row is all six timelines; every other row excludes
Monkfish.** That is not a convenience: Monkfish's five uncut passages all sit
on `soloist-moving`, so they distort that role's numbers and cannot touch the
drum camera's. Excluding a timeline wholesale where only one of its roles is
compromised would have thrown away 108 clean shots — and they are the ones that
carry the ceiling below.

**No drummer shot anywhere in this corpus reaches 22 seconds.** 314 shots, six
timelines, four rooms, four years, zero exceptions — the hardest boundary the
corpus has produced, and a usable rule rather than a tendency.

The sharpest evidence sits *inside* one role. `ensemble-wide` appears five
times; the anchor's instance is operated and the other four are locked. The
operated one puts 9.0% of its shots past 30 s, the locked ones 1.0%. Same role
name, same framing, opposite behaviour — and operation is the only thing that
differs. A locked shot has given everything it has within a few seconds; an
operated camera renews itself by moving, which is exactly the director's
reason, arrived at from the other end.

Two limits worth keeping attached to that:

- **It is a claim about the shape of the rig, not about intent.** The operated
  camera is the one that can follow anything, so it is also the one an editor
  can stay on. That may be why it holds the cut, and this corpus cannot say.
- **The subject question is answered, but as a principle rather than a
  measurement.** The director's account (2026-08-07) is that the moving camera
  generally follows the soloist *and* roams the band for interesting movement —
  soloist-primary, not soloist-only, by design. That is `[stated principle]`
  in the profile, corroborated by entry 2's frame grabs, and it is a different
  and in some ways better kind of evidence than three projects of inferred
  labels would have been. What remains unmeasurable is per-shot subject: no
  analysis in this corpus can see who is on screen, so no claim should ever
  rest on attributing a *particular* shot.

## The visible-edit re-measure (#142), measured 2026-08-10

PR #149 changed what `correlate_timeline` reads by default: every record frame
now resolves to the topmost enabled video item, overlays are shots, and
uncovered stretches are black shots — where every number above came from a
one-track reading of V1. The gated pass predates that change, so all six
timelines were re-measured under the visible-edit reader: each of the three
projects opened in Resolve in turn, every parameter — beats file, audio,
`angles`, `audio_at` — copied verbatim out of the gated artifacts, `track`
left out so the reader is the only thing that differs. Live Windows 11 box,
Resolve Studio 21.0.3.7, CPython 3.12.10 (python.org). The reading version has
travelled in the cache key since #149, so these are new artifacts and the
gated pass's files are still on disk beside them.

| # | Timeline | Tracks read | Meter | Cuts in span | Gated out | Measured | Bar position, gated | Beat offset: median / mean / max | Black |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Zinc - Set 2 Main | 3 | **1** | 399 | 399 | **0** | — refused whole | — | 6 cuts, 23.6 s, 0.6% |
| 2 | Freefall Timeline | 3 | 4 | 60 | 10 | 50 | 1:23, 2:21, 3:2, 4:4 | 80 ms / 204 ms / 6.08 s | 1 cut, one frame |
| 2 | Sunshine Timeline | 2 | **1** | 53 | 53 | **0** | — refused whole | — | 1 cut, one frame |
| 2 | Mercies Timeline | 2 | **1** | 41 | 41 | **0** | — refused whole | — | 1 cut, one frame |
| 3 | Concert Full Cut | 1 | 4 | 370 | 125 | 245 | 1:143, 2:44, 3:32, 4:26 | 82 ms / 105 ms / 423 ms | 22 cuts, 32.7 s, 0.6% |
| 5 | Monkfish Main | 3 | 4 | 244 | 35 | 209 | 1:83, 2:48, 3:36, 4:42 | 80 ms / 93 ms / 390 ms | 4 cuts, 6.8 s, 0.1% |

Result files: `Zinc---Set-2-Main-982f0c672a67`, `Freefall-Timeline-77f6d8407c1e`,
`Sunshine-Timeline-8367ec690d9c`, `Mercies-Timeline-30377dd012c7`,
`Concert-Full-Cut-bcc57f381e58`, `Monkfish-Main-3d0c9950e2b8` (all
`.correlate.json` under the analysis cache).

**The reader is not a confound for the beat-1 claim.** Summing the three
surviving rows — 504 measured cuts now that overlays and gap-returns are
counted, against 455 under the one-track reading:

| Beat of the bar | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- |
| Cuts | 249 | 113 | 70 | 72 |
| Share | **49.4%** | 22.4% | 13.9% | 14.3% |

Beat 1 moves 49.0% → 49.4% and beats 1–2 move 71.6% → 71.8%. Forty-nine more
measured cuts and no share moves by more than half a point: the skew the gated
pass found describes the film the viewer sees, not an artefact of reading V1
alone. The three `meter: 1` refusals are the same three timelines — the reader
cannot rescue a grid whose meter came out unusable — and Freefall's 6.08 s
residual cut (the known cut-beside-a-refused-stretch defect) survives
unchanged.

What the visible reading adds, and what it is worth:

- **Overlays are real but small.** Zinc reads three video tracks and its span
  grows 360 → 399; Freefall 43 → 60 and Monkfish 228 → 244. Scullers is
  single-track, and its 326 → 370 is entirely gaps: 22 black shots and the
  returns from them — cuts the viewer sees that the one-track reading folded
  into their neighbours.
- **Black is a rounding error in these edits.** No timeline puts more than
  0.6% of its span on black, and on the three gene-edit timelines it is
  literally one frame each. The `[measured]` claims above lose nothing to it.
- **Track enable-state was readable only where the timeline was its project's
  current one** (Zinc, Concert Full Cut). On the other four `enabled_known` is
  false and every track was measured as designed (#84) — right here, since
  none of these timelines hides a disabled track.
- **Alignment held.** Every mode and `zero_frame` matches the gated pass,
  including Zinc's post-#121 `83824`.
- **Transient medians are computed over the new cut set** — more cuts, so
  small moves: Zinc 33 ms (unchanged), Monkfish 41, Freefall 25, Sunshine 17,
  Mercies 19, Scullers 30. Still spanning 17–41 ms, all inside a frame.

## The deliverable head/tail survey (G8), measured 2026-08-13

Not a timeline row. Every entry above is a Resolve timeline read through
`correlate_timeline`; this one is five **finished deliverables** read with
ffmpeg, because the thing being measured — what a tune's first and last
seconds are staged as — is invisible to a shot reader. A title is not a shot,
and a dissolve to black is not a cut.

Source: `S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos`, the whole
folder, five songs from Zinc Bar set 2 on 6-17. 3840×2160, 23.976 fps, 10-bit.
Result: `gauntlet/recon/openings_survey.json` (`summary` block, over the curves
it was derived from); scripts `gauntlet/recon/openings_survey.py`,
`openings_summary.py`; contact sheets under `gauntlet/recon/openings_frames/`
(gitignored; regenerate with `openings_survey.py sheets`, then `long`, `luma`,
`tailluma`, `text`, `bandsheet`, then `openings_summary.py`).

| Song | Head device | Black → picture | First note | Title super | Personnel super | Tail |
| --- | --- | --- | --- | --- | --- | --- |
| Sambra | dissolve up from black | 1.46 s | 0.25 s | 4.67–7.51 s (2.84) | 19.27–25.40 s | hard cut to black at −7.94 s |
| Soultrane | dissolve up from black | 2.34 s | 0.45 s | 7.97–13.18 s (5.21) | 69.95–77.87 s | dissolve; black from −1.90 s |
| Maitland Boulevard | dissolve up from black | 1.21 s | 0.65 s | 5.30–9.64 s (4.34) | 16.18–22.98 s | dissolve; black from −0.55 s |
| The Hardest Part is Starting | dissolve up from black | 1.00 s | 1.15 s | 5.38–8.30 s (2.92) | 17.02–23.77 s | hard cut to black at −6.51 s |
| Taurus People | **full-frame title card** | 2.336 s (hard cut) | **2.38 s** | card 0.04–2.336 s (2.29) | 20.65–26.78 s | dissolve; black from −0.17 s |

Method, so the numbers can be re-derived or disbelieved specifically:

- **Structure** (black, fade-up, card plateau, tail) from per-frame
  `signalstats` YAVG. The sources are 10-bit, so limited-range black reads
  **64**, not 16, and the Taurus card — black plus white type — reads 82.8. A
  threshold keyed to 16 calls the whole opening "not black"; a card detector
  keyed to frame 0 alone misses the card, because frame 0 is black and the card
  starts at frame 1. Both mistakes were made before the table above.
  Frame-accurate.
- **Supers** from the near-white pixel share inside a crop of the lower-third
  band (0.55w × 0.14h at 0.03w, 0.76h → gray → threshold 200). YMAX in the same
  band does **not** work: a candle or a cymbal pins it, and the first attempt
  returned four spurious spans per song. The share plateaus flat while type is
  up and ramps over ~0.3 s either side, so the in/out times are good to a
  frame. Every span was cross-read against a 0.5 s contact strip of the same
  crop.
- **Music** from `astats` RMS at 0.1 s, and at 0.02 s across the Taurus
  entrance, which is the one number a claim turns on: room floor −30 to −46 dB
  through 2.36 s, attack at **2.38 s**, peak −13 dB at 2.48 s, against a card
  that cuts to picture at 2.336 s. One frame early.
- Soultrane's personnel super sits at 69.9 s, outside the 60 s scan the other
  four were found in; it was measured on a separate 40–180 s sweep. A scan
  window that finds four of five is a scan window, not a convention.

Two cautions on the tail numbers. Dissolve *lengths* are the weakest figure in
the table — the reference luma is taken twelve seconds before black and a shot
change inside that window moves it, which is why the three dissolves read
5.8 / 5.9 / 10.3 s and nothing narrower than "roughly the last 6–10 s" is
claimed anywhere. Black *onset* has no such problem and is frame-accurate. The
"still playing at the last frame of picture" reading is from frames, by eye,
on all five.

What this row supports: the `[measured — 1 project, n=5 songs, Zinc 6-17 set 2]`
bullets in `concert.md` §5b. What it does not: anything about cut placement,
shot length or angle share — 5 songs of head and tail is a few dozen seconds of
film, and the rest of each deliverable was not read. One project, one night, so
the thin-support rule applies and the tag names the weakness rather than hiding
it.

## The quiet-passage pass (#190), measured 2026-08-14

Not a timeline row either, and a pair rather than a single reading: the same
song cut twice, once by the director and once by us. The question is what
happens *inside* a quiet passage, which the arc-gear table (`concert.md` §3)
does not answer — that table sets the rate a passage runs at, and both cuts
hit it.

Source: `S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos/6-17 - Zinc
Set 2 - Taurus People.mp4` (497.66 s, 3840×2160, 23.976 fps) for the human
cut, and `projects/mcp-tests-zinc/taurus-people-full-r2.cut.json` for ours —
the P4R2 capstone that won 3–0. Result: `gauntlet/recon/quiet_floor.json`;
script `gauntlet/recon/quiet_floor.py`. The two clocks differ by 0.167 s (his
497.664 against our 497.497); the section boundaries are his, so our cut is read
against his song rather than against its own.

The script takes every spread, orphan and passage reading by importing
`analysis.correlate`'s own functions rather than reimplementing them, so the
receipt cannot drift from the tool. Its `server_check` block is `gears.quiet_floor`
run end to end over that level curve and our cut file: **`reads_locked: true`** on
the passage, with the 2.502 s orphan named. The rule fires on the cut the ticket
was written about — recorded, not inferred. Run over the director's cut through
the same code the same passage comes back **`reads_locked: false`**, which is the
half that matters more: a check that fired on everything would prove nothing.

The passage is d38–195 (157 s), derived rather than named: a 1 s RMS curve off
the deliverable's own audio, smoothed with a centred 15-window moving median,
the quiet third of that taken by rank, contiguous runs of ≥20 s kept. It is the
`floor` and `breath` sections of the arc-gear table plus the head of `trade`.

| Cut | Shots | Cuts/min | Median shot | CV | Orphans | CV less orphans |
| --- | --- | --- | --- | --- | --- | --- |
| Director | 20 | 7.64 | 4.53 s | **0.783** | 0 | 0.783 |
| Ours (P4R2) | 18 | 6.88 | 7.45 s | **0.597** | 1 (2.50 s at d109) | 0.564 |

Read by the arc-gear table's own named sections rather than the derived
passage, the same gap shows up, and the numbers want their labels read
carefully. Mean within-section CV — the mean of the per-section CVs — is
**0.692** for him over the eight sections it can be taken over (his `ending` is
a single 21 s shot, so it has no spread) against **0.502** for us over nine.
Inside `floor`+`breath` alone: pooling both sections' shots gives **0.719**
against **0.510**, while the mean of those two sections' own CVs gives **0.718**
against **0.468**. Pooled and mean-of-sections are different readings and the
receipt now carries both under those names; the gap survives either.

Method, so the numbers can be re-derived or disbelieved specifically:

- **Human cuts** from ffmpeg scene detect at ab_pack's calibrated threshold
  0.10 (`scale=320:-2, select gt(scene,0.10), metadata=print`) over the whole
  deliverable: **77 cuts, 78 shots**, which is exactly what the P4R1 pack's own
  scan of the same file found — the one cross-check available. The 0.27 run in
  `human_cuts_taurus.json` is a different instrument and is not comparable.
- **Our cuts** from the cut file's segment lengths at 23.976 fps, so they are
  exact rather than detected. The asymmetry biases *against* the finding: a
  missed detection merges two human shots into one long one, which lowers his
  spread, not ours.
- **Orphan** = a shot under 0.5× the passage median with both neighbours inside
  the passage at or above that median. Two short shots side by side are a burst
  and are not orphans — the distinction is the whole reading, since a burst is
  something a viewer reads as cutting and a lone flash between holds is not.
- A shot belongs to the section or passage its **head** sits in, which is
  `full_gears.py`'s rule kept unchanged so the two receipts can be compared.

Three cautions, because the interesting ones are against this row rather than
for it.

**The floor at 0.65 is one song wide.** It separates exactly two cuts of it. It
is the number `one_speed` already uses for the whole cut, which is why it was
chosen over anything tuned closer to the gap — a threshold fitted to a single
pair is a threshold fitted to nothing.

**The orphan rule is not what draws the verdict here.** Ours fails on the raw
spread too: 0.597 pooled, 0.564 once the flash is dropped, both under the floor.
The correction is worth taking because a spread held up by a lone flash is not a
spread anybody watching sees — but on this evidence it names the flash rather
than deciding the case, and no measurement here shows a passage that the raw CV
would have waved through.

**The 0.5× fraction is doing less work than the neighbour test.** Two of his
shots in the passage sit under half the passage median — 1.752 s (0.387×) and
2.253 s (0.498×) — and neither is an orphan, because each has a shortish shot
beside it (3.879 s and 3.128 s) rather than a hold. That is the burst-versus-
flash distinction earning its keep, and it is also a warning: had the neighbour
test not been there, the director's own passage would have scored two orphans.
His shortness comes in company; ours came alone.

What this row supports: the quiet-passage bullet in `concert.md` §3 and the
`gears.quiet_floor` block in `analysis/correlate.py`. What it does not: anything
about *why* a passage reads static beyond shot length — reframes, push-ins and
what is inside the frame are named in the style as what fills a passage, and
nothing here measured them.
