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
| 1 | `2026-06_Zinc_and_Monkfish` | Zinc - Set 2 Main | concert | not started | **Anchor** — strongest current-taste exemplar; two-camera, 2026 |
| 2 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Freefall Timeline, Sunshine Timeline, Mercies Timeline | concert | not started | Canonical snapshot for Ryan and Hang; music-performance cuts only |
| 3 | `Mike Tucker Scullers` | Concert Full Cut | concert | not started | Good two-camera concert; older, so recency weighting applies |
| 4 | `Archive/Client/Ryan Devlin Projects Current` | Stablemates | concert | not started | Older but explicitly taste-endorsed |
| 5 | `2026-06_Zinc_and_Monkfish` | Monkfish Main | concert | not started | **Partial** — only a couple of tunes cut; measure those tunes only |
| 6 | `Archive/Client/Side Step Blues Clues Album` | Blues Clues Main, Devlin Time Main, For All The Other Times Main, Intro Main, Outro Main, People We Love Main, Walk Spirit Talk Spirit Main, EJ's Blues Main | studio-session | not started | Multi-camera studio album shoot — a distinct cutting context |
| 7 | `Archive/Client/Ryan and Hang Main - 9-23-24 gene edit` | Blues for Alice timeline, Three Card Molly timeline | concert | conditional | Include **only if** pre-flight shows 2+ cameras; suspected single-camera |

~7 concert-context timelines + 8 studio-session timelines. Full-set timelines
carry many tunes, so per-cut counts run into the hundreds.

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
- [ ] 2+ source angles — gates the conditional entry, and drops any timeline
      that turns out single-camera.
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
| _(none yet)_ | | | | | | |
