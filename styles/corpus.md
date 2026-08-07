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
| 2 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Freefall Timeline, Sunshine Timeline, Mercies Timeline | concert | not started | Canonical snapshot for Ryan and Hang; music-performance cuts only |
| 3 | `Mike Tucker Scullers` | Concert Full Cut | concert | not started | Good two-camera concert; older, so recency weighting applies |
| 4 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Stablemates | concert | not started | Older but explicitly taste-endorsed. Read from the Ryan and Hang project, not `Ryan Devlin Projects Current` — same timeline, one project to open |
| 5 | `2026-06_Zinc_and_Monkfish` | Monkfish Main | concert | not started | **Partial** — only a couple of tunes cut; measure those tunes only |
| 6 | `Archive/Client/Side Step Blues Clues Album` | Blues Clues Main, Devlin Time Main, For All The Other Times Main, Intro Main, Outro Main, People We Love Main, Walk Spirit Talk Spirit Main, EJ's Blues Main | studio-session | **deferred** | Footage not on the box; and studio-session is a different cutting context from the concert work this pass is about. Revisit when the concert profile is settled |
| 7 | ~~`…Ryan and Hang…` Blues for Alice, Three Card Molly~~ | — | — | **dropped** | Confirmed single-camera (director, 2026-08-07) — no angle switches, so nothing to measure |

7 concert-context timelines across 4 projects. The 8 studio-session timelines
are deferred, not excluded. Full-set timelines carry many tunes, so per-cut
counts run into the hundreds.

Entries 4, 6 and 7 were settled by the director on 2026-08-07: Stablemates
reachable from the entry-2 project, Side Step deferred, and the conditional
entry resolved single-camera without needing a pre-flight.

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

`alignment` is `correlate_timeline`'s reading of where the times were measured
from. **`timeline_start` rows are excluded from every timing claim** — those
times count from the timeline's own first frame rather than from the mix, and a
file measured against the wrong zero looks exactly like a file measured against
the right one.

| # | Timeline | Context | Cuts | Alignment | Sidecar | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Zinc - Set 2 Main | concert | 366 (360 measured, 6 openings) | `audio_clip`, matched | `angles/2026-06_Zinc_and_Monkfish.json` (confirmed 2026-08-07) | `…/analysis/Zinc---Set-2-Main-dcb16e19eca1.correlate.json` |

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
  applied.
- **The transient numbers do not depend on the grid.** Onsets are measured off
  the mix directly, which is why they are the row's trustworthy half — and
  they are also the half the profile's core principle is about.
