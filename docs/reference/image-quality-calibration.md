# Image-quality calibration

Where the floors and guards in `resolve_mcp.video.picture` and
`resolve_mcp.video.quality` come from, and what they were measured against.
Receipts: `gauntlet/recon/image_quality_calib.json` (the floors),
`gauntlet/recon/quality_cut_guard.json` (the discontinuity guard),
`gauntlet/recon/quality_shots.json` (both, on a real cut and a real angle).
Measured 2026-08-14 for #182.

## The question

`analyze_quality` vetoes a shot for being soft, blown out or shaky. Each veto is a
floor, and a floor picked from a chair is a rule about nothing. It has to sit below
footage a director shipped — those frames were accepted by the only judge that
counts — and above footage that has actually failed.

## What was measured

**Known-good** is the five human deliverables of the corpus session
(`S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos`), sampled through the
decode `analyze_quality` runs: a 320x180 grey grid at 4 samples a second, 180 s from
a quarter of the way into each song, 3600 samples in all. A window rather than five
whole songs, because five 4K masters end to end is an hour of ffmpeg to answer what
3600 samples already answer; a quarter of the way in, because that is the band
playing rather than the count-in.

**Known-bad** is those same frames with each failure applied to them:

| failure | how | mildest case |
| --- | --- | --- |
| defocus | gaussian blur on the grid | σ=1, about six pixels at the 4K master |
| blown | luma gain before clipping | ×1.6, a stop and a half |
| shake | every other frame translated | 3 px, ~1% of frame width |

Degrading the corpus rather than composing a fixture is the point. The question is
whether the reading separates *this footage* from *this footage gone wrong*; a
synthetic pair could be separated by a measurement that says nothing about a concert.

## What the readings did

| reading | delivered corpus | mildest failure |
| --- | --- | --- |
| sharpness | min 0.582, p05 0.647, median 0.705, max 0.810 | defocus σ=1: 0.157–0.197 |
| clipped | max 0.0002 (two pixels in ten thousand) | blown ×1.6: median 0.029 |
| stability | min 0.844, p05 0.938, median 1.000 | shake 3 px: median 0.605 |

Every reading separates completely. Nothing in the corpus overlaps any of the three
degraded distributions, and the gaps are wide: the softest delivered frame is three
times sharper than the sharpest defocused one.

## The rule, and the floors it gives

> Midway between the corpus 5th percentile and the median of the mildest failure,
> moved to the neighbouring grid point that vetoes less of the corpus (0.05 for the
> two 0-to-1 readings, 0.005 for clipping, which lives near zero).

Percentiles rather than extremes on both sides: the tails of a 3600-sample scan are
whip pans and strobe frames, and a floor set on those is a floor set on the
estimator's worst moment. Rounding towards the laxer grid point so no floor vetoes
delivered footage because of where a grid line fell.

| floor | value | vetoes of the corpus | catches of the mildest failure |
| --- | --- | --- | --- |
| `min_sharpness` | 0.40 | 0 of 3600 | 100% |
| `max_clipped` | 0.015 | 0 of 3600 | 94.3% |
| `min_stability` | 0.75 | 0 of 3553 measurable | 99.9% |

None of the three is near a cliff. Every sharpness candidate from 0.25 to 0.50
vetoes none of the corpus and catches all of the defocus; every stability candidate
from 0.60 to 0.80 vetoes none of the corpus, and the 3 px shake goes from 10% caught
at 0.60 to 99% at 0.70 as the floor crosses its distribution. The whole sweep,
including what each candidate would have let past, is in the receipt.

## The discontinuity guard, and the bug it was hiding

Stability is measured between neighbouring frames, so it depends entirely on
refusing to score a pair the correlator cannot align — a pair across a cut is two
different pictures, and the correlator answers anyway, with a large meaningless
shift that then poisons the trend its neighbours are judged against.

The first pass at this guard was set by eye, and it was too loose. On the Taurus
People deliverable one cut (phase-correlation peak 0.020 against a 0.01 floor, a
30 px shift against a 32 px ceiling) went unrecognised, and dragged six samples of a
locked-off shot to a stability of zero — a delivered shot the report called shaky.
`quality_cut_guard.py` measures every frame pair of every deliverable and splits
them by whether a detected cut falls inside the pair:

| | pairs | peak p01 | peak p05 | peak median |
| --- | --- | --- | --- | --- |
| inside a shot | 14397 | 0.287 | 0.397 | 0.583 |
| across a cut | (601 labelled, ~1 in 3 truly crossing) | 0.000 | 0.017 | — |

The two are cleanly separated and the sweep between them is flat: a peak floor of
0.03 catches every crossing pair, 0.12 catches no more, and nothing at all lies in
between. `PEAK_FLOOR` sits in the middle of that gap at **0.05**, refusing 0.6% of
in-shot pairs — which are flat frames the contrast guard would refuse anyway.
Separately, no in-shot pair anywhere in the corpus moves more than 3.1% of frame
width between samples, so `CUT_SHIFT` came down from 0.10 to **0.04**.

What that fix was worth, measured the same way on the same footage:

| | before | after |
| --- | --- | --- |
| corpus samples under the stability floor | 40 of 3561 (1.1%) | 0 of 3553 |
| lowest single corpus stability sample | 0.000 | 0.844 |
| shots of the human's Taurus People cut called shaky | 1 of 78 | 0 of 78 |

The 1.1% was not estimator noise to be smoothed over in the aggregation — it was
this bug, and two decisions taken to work around it (judging a stretch on its median
stability, and refusing to publish a one-sample window) were reverted once it was
fixed. A stretch is judged on its worst moment, which is what a veto means.

## On a real cut and a real angle (#182 AC1)

`quality_shots.py`, against the same corpus and the live Resolve project:

- **Per shot** — the human's Taurus People cut, 78 shots from the scene detector.
  Sharpness spreads 0.62 to 0.85 across shots, so the reading discriminates between
  a delivered cut's own shots rather than saturating; stability — each shot's own
  worst sample — runs 0.875 to 1.00, and no shot misses a floor. A delivered cut
  vetoing none of itself is the result to want.
- **Per window** — a 2-minute span of a raw A7IV angle out of the open project,
  scanned through `analyze_quality`: 480 samples, none unusable, no windows, median
  sharpness 0.686. Raw footage from this rig reads much like the cut made from it.

## What this does not calibrate

- **Absolute focus.** Sharpness is no-reference and content-aware only up to
  acutance. A shallow-focus shot whose subject is sharp and whose frame is mostly
  bokeh scores low and is not wrong. Treat a low score as a frame to look at.
- **Other rooms.** Every number here is one session at one club with one lighting
  rig. The rule travels; the floors are this corpus's. Re-run the scripts against a
  new corpus before trusting them on it.
- **Fine jitter.** Stability is read between samples, so 4 samples a second sees
  sway up to about 2 Hz. A true micro-jitter needs `sample_fps` raised.
- **Footage that actually failed.** The bad side is degraded good footage, not a
  take the director rejected for being soft. Nobody keeps those, which is why it was
  done this way — but it means the readings are calibrated against a *model* of each
  failure rather than against the real thing.
- **Raw angles at scale.** One angle span was scanned, not the card. How often a
  source fails these floors is still unknown, and that is the number that would say
  how much a builder's angle choice is really being constrained (#182 leaves it
  open).
