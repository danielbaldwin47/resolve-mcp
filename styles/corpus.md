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
