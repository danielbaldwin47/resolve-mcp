# Image-quality calibration

Where the floors in `resolve_mcp.video.quality` come from, and what they were
measured against. Receipt: `gauntlet/recon/image_quality_calib.json`, written by
`gauntlet/recon/image_quality_calib.py`. Measured 2026-08-14 for #182.

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
| clipped | max 0.0002 (one pixel in ~5000) | blown ×1.6: median 0.029 |
| stability | p05 0.938, median 1.000 | shake 3 px: median 0.605 |

Sharpness separates completely — the whole corpus sits above 0.58 and every
defocused frame below 0.20, with nothing in between. Clipping separates by two
orders of magnitude: this room's lighting essentially never burns a highlight
through. Stability separates with an overlap of about 1% of samples, which is the
interesting number below.

## The rule, and the floors it gives

> Midway between the corpus 5th percentile and the median of the mildest failure,
> moved to the neighbouring grid point that vetoes less of the corpus (0.05 for the
> two 0-to-1 readings, 0.005 for clipping, which lives near zero).

Percentiles rather than extremes on both sides: the tails of a 3600-sample scan are
whip pans and strobe frames, and a floor set on those is a floor set on the
estimator's worst moment rather than on the footage. Rounding towards the laxer grid
point so no floor vetoes delivered footage because of where a grid line fell.

| floor | value | vetoes of the corpus | catches of the mildest failure |
| --- | --- | --- | --- |
| `min_sharpness` | 0.40 | 0 of 3600 | 100% |
| `max_clipped` | 0.015 | 0 of 3600 | 94.3% |
| `min_stability` | 0.75 | 40 of 3561 (1.1%) | 99.9% |

The sharpness floor is nowhere near a cliff: every candidate from 0.25 to 0.50 vetoes
none of the corpus and catches all of the defocus. The stability floor is the one
worth arguing about, and the sweep in the receipt is why 0.75 rather than 0.60: at
0.60 a 1%-of-frame wobble is caught 10% of the time, at 0.65 it is caught 94%, and
the corpus veto rate is flat at ~1.1% across the whole range. The overlap is not the
floor's fault and moving it does not fix it.

## What the 1.1% is, and what it changed

Those 40 samples are inside delivered, accepted footage. They are quarter-second
dips — a whip pan, a strobe frame, a correlation that landed badly — with locked-off
footage either side. Two decisions follow from them, both in the code:

- **A stretch is judged on its median stability, not its minimum**
  (`picture.summarize`, and the per-shot column in `analysis.correlate`). At a 1.1%
  sample rate, a five-second shot holds about twenty samples, so judging a shot on
  its unluckiest one would veto roughly a fifth of a clean edit. `stability_min` is
  carried beside the median so the dip stays visible.
- **A lone unusable sample is not a window** (`quality.MIN_WINDOW_SAMPLES`). The
  sample stays in the curve and in `unusable_samples`; what it does not do is become
  a stretch to keep a cut out of.

Clipping keeps its maximum: a frame with a stage light burned through it is visible
the instant it is on screen, and it has no estimator noise to speak of.

## What this does not calibrate

- **Absolute focus.** Sharpness is no-reference and content-aware only up to
  acutance. A shallow-focus shot whose subject is sharp and whose frame is mostly
  bokeh scores low and is not wrong. Treat a low score as a frame to look at.
- **Other rooms.** Every number here is one session at one club with one lighting
  rig. The rule travels; the floors are this corpus's. Re-run the script against a
  new corpus before trusting them on it.
- **Fine jitter.** Stability is read between samples, so 4 samples a second sees
  sway up to about 2 Hz. A true micro-jitter needs `sample_fps` raised.
- **Raw angles.** Only the deliverables were measured. The raw camera masters have
  not been swept, so the rate at which the *sources* fail these floors is unknown —
  which is the number that would say how much a builder's angle choice is actually
  being constrained (#182 leaves this open).
