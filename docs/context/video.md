# `video/` — frame routes and the picture readings

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`video/` — `ffmpeg` (the commands video routes run), `frames` (frame
grabs — the one compute route that is not a job), `jpeg` (read back
dimensions), `scenes` (scene-cut detection as cached job), `blocking` (how
blocked one frame is: the near-field obstruction arithmetic, numpy + scipy,
no I/O — plus the **discriminator** that says which of its blobs matter,
`novel` off the run's own occupancy and `hidden` off its per-pixel median,
either clearing its level making the sample an obstruction, #189),
`occlusion` (that arithmetic as a cached job over a sampled range —
per-sample scores and the windows to keep a cut out of, every window classed
`obstruction` or `scene` and none of them filtered out), `sampled`
(what both sampled-decode scans share: the range check, the sample grid and
the runs that become windows — one copy, because a range check that clamps
instead of refusing answers for footage nobody decoded), `picture`
(how good one frame looks: sharpness as acutance, exposure, clipped
highlights, and stability as the residual after global motion compensation,
so a pan is not a wobble and a pair across a cut is unmeasurable rather than
unstable; numpy + scipy, no I/O), `quality` (that arithmetic as a cached job
over a sampled range — the same window shape as `occlusion`, with three
floors and a reason on every window; the floors are calibrated on the corpus,
`docs/reference/image-quality-calibration.md`, and `analysis/correlate` joins
a scan of a render onto the cut's own shots, #182), `framing`
(how far the picture steps *across* a cut and the 30-degree-rule jump-cut
flag: layout, content and size terms over two grey frames, numpy only, no
I/O — the pack measures with it, `analysis/correlate` joins its catalog on),
`supers` (the **super**: which burned-in graphics are up when, and which cuts land
inside one. Pure numpy + scipy over frames somebody else decoded — the pack does
both decodes, a coarse scan for where and a native-fps walk for the exact in and
out, and `analysis/correlate` joins the catalog on as `straddles_super`, #183),
`source` (clip name → file path + the clip's own frame numbering).
