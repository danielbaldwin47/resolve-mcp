# `analysis/` — compute jobs that read audio and write findings to disk

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`applause` (bursts → tune boundaries, then a beat-density floor drops the calls
with no pulse under them, #133; every boundary then walks forward off the applause
to where the loudness curve says the band comes in, and a mix the threshold finds
no clapping in at all is read at its own scale instead, #179),
`barmap` (a cut read against the **bar map** `bars` writes: nearest bar line with a
signed offset — a cut just before a downbeat is a cut on the one — giving `map_bar`,
`in_group` and `bar_offset` per cut and the `bar_groups`/`bar_offsets` blocks over
them, ungated on the beat gate since the map exists for the grids that gate refuses
whole; pure, read by `correlate`, #180/#215),
`bars` (the **bar map** itself: a rule layer over the grid for the material the beat
model will not commit a meter to — folds a too-fast grid to the tactus, then
scores every meter and phase against a per-beat accent reading and takes the
widest lead over the runner-up, refusing rather than guessing when the accents
say nothing. The accent reading is injected per ADR 0002 and defaults to RMS off
the mix; a named stem reads that instead, #180),
`beats` (grid + downbeats, model
injected per ADR 0002; `trust` says which beats the grid describes well enough
to count, #112; `spacing` says how wide a beat is at each beat), `correlate`
(measure a cut against its music — by default the *visible* edit,
every frame resolved to the topmost enabled video item with uncovered stretches
as black shots, #142; `track=` measures one video track alone. Gates the beat
statistics on `trust`, refuses as `stranded` a cut further from its beat than a
beat is wide — the grid does not reach it, #160 — and leaves the transient ones
ungated. Composes the readings other modules own rather than computing them:
`rhythm` for `shot_rhythm`, `barmap` for a cut's place in the form (`bars=`, the
optional **bar map**), `subject` for `on_soloist`. What is left here is the join —
reading the timeline, putting every shot on the music's clock, writing the file,
#215),
`cuda` (preloads
the CUDA runtime the `analysis` extra ships, so CTranslate2 finds it on Windows;
pure decisions, #128),
`decode` (WAV → numpy, no third-party decoder), `device` (which device the
torch models infer on, announced once per process, carried in job records, and
handed to the models rather than left to their defaults — no silent CPU
fallback, #202, and CUDA since #245: `inference_device()`, the inventory and
the record of the flip in
`docs/reference/compute-device-inventory.md`), `drums` (hits per stem), `energy`
(loudness curves; `rms_curve` is the cheap level-only pass, no K-weighting and
no onsets), `fills` (drum-fill candidates), `halves` (shared
identify/cache/write pattern — `written` is the one door every analysis file
goes through, so every one of them opens `kind`/`audio`/`duration_seconds`
(#223, guarded across detectors by `tests/test_analysis_reports.py`;
`correlate`'s join over cut rows is the documented exception), and where the
naming rule lives: a header-stats builder is `gist`, a record builder is
`rows` — plus `collected`/`stem_named` — where a
separation's melodic stems are, third pass included, and which one was asked
for; one convention for every detector that reads one: `phrases` off the line,
`bars` off the pulse, `structure` off all of them (#220). `fills` still finds
the drum pass its own way),
`melody` (notes off one melodic stem —
monophonic pitch + gating, model injected per ADR 0002; the reading `phrases`
is a rule layer over, as `drums` is to `fills`), `music` (beats + energy + gist
job; `beats_of`/`energy_of` are the shared entries other jobs read a grid or a
loudness curve through, one measurement per piece of audio), `phrases` (phrase boundaries: where the soloist stops, which is the
cut-placement unit #46 named, #143),
`records` (sliceable record files — `write` and, beside it, the one strong reader
`rows(path, field)` every caller shares: four refusals over a file that is not one of
these, plus the numeric-`t` filter and the time-order sort that make the rest a
timeline. `allow_empty=` drops the empty-file refusal and is for the caller reading
back a file it just wrote — nothing found is a wrong path only when an agent named
the file, #222), `rhythm` (how varied the cutting is, one entry
`read(rows, levels)` over per-cut rows and a level curve: `shot_rhythm` bins the
shot lengths, measures the longest strict A/B alternation run and the longest
monotonic duration `ramp`, and says `reads_metronomic` with the heuristic that drew
it; its `gears` block splits the cut's span into loudness terciles off a 1 s RMS
curve and reports cuts per minute in each, the loud/quiet `rate_ratio`, where the
sub-2 s shots sit, `one_speed`, and `outside_shots` — shots past the analysed mix,
counted apart rather than clamped into a tercile — plus `quiet_floor`, the passages
the slow gear is held through, found by smoothing that curve rather than off the
per-window tercile labels, each read for the spread its lone flashes are not holding
up (#190). Warnings the report carries, never gates; pure, read by `correlate`,
#215), `silence` (RMS spans), `solos` (front
of band changes: lead off the stem energy, timbre off one stem's brightness —
with the third pass on disk the voices are `wind`/`comp` rather than `other`
and timbre reads `wind`, #157), `stats` (the readings taken over a column of
records — signed offsets with early and late counted apart, a histogram, and
whether a column was measured at all — shared by `correlate` and the joins it
composes so the rules have one copy, #215), `structure` (tunes + solo changes job; both
halves read the shared beats half and the tune half the shared energy half;
its stem loader is error shaping over `halves.collected`, #220), `subject`
(what a shot is framed on crossed with who is out front: the angle sidecar's subject read as
player/ensemble/other, joined to the solo windows in seconds so a shot that
outlives its solo is split where the front changed — pure, no I/O, read by
`correlate`, #181), `transcribe`
(job), `transcript` (document + Word/Transcription/Transcriber vocabulary),
`virtual` (a cut file read back as the words it will contain — the P4
self-review, warnings only, touches no Resolve handle), `whisper`
(default backend: faster-whisper large-v3).
