# `tests/` — the test map narrative

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

Test files follow the module they cover, so the media pair splits the same way
the source does: `test_media_pool.py` covers `resolve/pool` (bin addressing,
clip lookup, clip reading, frame bounds, offline) and `test_media_tools.py`
covers `resolve/media` (what the six operations do with what the adapter hands
them). Both drive the fake seam through `tools/media`, and both build their
pools from `tests/mediapool.py` (`a_file`, `a_clip`, `a_shallow_copy_pool`) so
the pair cannot drift on what a clip or a shadowed copy looks like.

`test_findings.py` covers `findings` and the pre-flight shape in `document`:
the severity split, the packaged `{errors, warnings}` reply, the refusal
sentence, and that the cut and titles contracts subclass the one shape.
`test_envelope.py` covers the job-record wrap in `tools/envelope` — including
the reconnect path, which is a second return statement and so a second place
the shape can drift (#219).

`test_lease.py` covers `lease` — the liveness truth table in memory, then the
claim protocol with both the liveness answer and the read injected, so a dead
process, a recycled pid and a refused read are arrangements rather than
monkeypatches. `test_detached_jobs.py` keeps what is stems-shaped over it (the
refusals' wording, the claim held across the passes and dropped after them) and
`test_sharing.py` pins the retry both files depend on (#217).

`test_analysis_reports.py` is the one test that spans detectors rather than
following a module: it runs every half that writes an analysis file — beats,
energy, tunes, solos, bars, phrases, fills — and asserts they all open
`kind`/`audio`/`duration_seconds`, the header `analysis/halves.written`
exists to keep (#223). It imports each detector's fakes from that detector's
own test file, so the inputs have one home; what each file *says* stays with
the per-detector tests.

`test_device.py` is the second such spanning file, for the same reason in the
other direction: it covers `analysis/device`, and then reaches into
`beats.beat_this_detector` and `applause.panns_tagger` to assert each hands its
model the announced device (#245). One device decision made in two places is
one thing to verify, not two — split across the per-detector files, neither
half would show that the two sites agree, which is the property ADR 0008 turns
on. Everything else about those detectors stays in `test_beat_grid.py` and
`test_applause_spans.py`. It also reads `pyproject.toml` directly, to pin the
cu130 index shape that no import can observe.

`tests/fakes/` is the fake Resolve API, one module per subsystem — open the
module, not the package, and never the whole package at once:

- `core.py` — `DroppedHandleError`, `AnswersNone` (the primitives)
- `fusion.py` — `FakeSpline`, `FakeFusionInput`, `FakeFusionTool`,
  `FakeFusionComp`
- `timeline_item.py` — `FakeTimelineItem`
- `timeline.py` — `FakeTrack`, `FakeTimeline`, `TrackSpec`, and the frame
  arithmetic an append lands on
- `media.py` — `FakeMediaPoolItem`, `FakeFolder`, `text_plus_template`, and
  the helpers that build clips from paths
- `pool.py` — `FakeMediaPool`, `media_pool()`
- `project.py` — `FakeProject`, `FakeProjectManager`
- `connection.py` — `FakeResolve`, `FakeConnector`, `EXPORT_TYPES`
- `separator.py` — `FakeSeparator`
- `fixtures.py` — `write_wav`/`write_clicks`/`write_hits`/`write_tones`
  (a melodic stem: pitched notes of a known length with known gaps)/
  `write_sections`/
  `write_jpeg`, `ffmpeg_absent`, `ffmpeg_refusing`, `hwaccel_probe_reply`
  (answers the `-hwaccels` capability probe so fake runners survive it);
  and the headers stdlib
  `wave` cannot write, built by hand — `write_float_wav`,
  `write_extensible_pcm_wav`, `write_tagged_wav`
- `builders.py` — `studio()`, `sync_reference()`, `with_a_mix()`

`__init__.py` re-exports every public name, so `from .fakes import X` works
whatever module `X` lives in and no test file names a submodule. Cross-module
references that exist only in annotations sit under `if TYPE_CHECKING`; that
is what keeps the runtime import graph acyclic. Installed by the `attach` fixture in
`tests/conftest.py` (autouse `_clean_globals` resets the seam and pins a
hermetic `Config` around every test). Other helpers: `tests/cutfile.py`
(miniature concert cut file + media pool), `tests/roughcut.py` (the P4
substrate: one talking head said twice, its transcript, and the b-roll that
covers the join), `tests/otio.py` (hand-edited OTIO with a dissolve),
`tests/text_plus_probe.py` (Text+ probe fixtures), `tests/live_state.py` (the
state the live tier builds for itself: `sweep_suite_timelines()` clears the
previous run's leftovers, `restore_current()` leaves the director's cut open,
`write_hard_cut_clip()` generates the clip the scene scan needs — decisions
covered by `test_live_state.py` in the fake tier).

`tests/data/` is the one place real measured footage is committed rather than
faked: `occlusion/*.npz` is the gauntlet's G11 evidence set as the 128x72 grey
the detector reads — six adjudicated 90 s scans, regenerated by
`gauntlet/recon/occl_fixture_grids.py`. It exists because every occlusion
false positive is a real dark bottom-anchored blob, so a drawn fixture can
only agree with the detector that drew it (#189).

Test files pair 1:1 with the module they cover (`test_cut_validate.py` ↔
`cut/validate.py`, `test_timeline_tools.py` ↔ `tools/timeline.py` +
`resolve/timeline.py`, …). `test_wav_container.py` ↔ `audio/riff.py` is the
exception that earns its keep: the headers it covers are read by `audio/wav.py`,
`analysis/decode.py` and `analysis/silence.py` alike, and #110 was a bug in what
all three agreed about. `test_rough_cut_pillar.py` is one of two other
exceptions: it covers no single module, walking the P4 pillar across `cut`,
`build`, `takes` and `virtual` in one pass because the joins are what a
per-module test cannot see. `test_cut_devices.py` (#141) is the second, for the
same reason: gaps and overlay tracks are one device each across `cut/layout`,
`cut/validate`, `resolve/build`, `resolve/takes` and `analysis/virtual`, and the
interesting failures are the disagreements between them. `test_cut_tail.py` is the third: the
tail is one device across `cut/tail`, `cut/validate`, `resolve/tail` and
`resolve/build`, and a dissolve that did not land looks exactly like a cut that
never asked for one. (`test_cut_otio.py` is its pure half, split out with the
module in #221: documents in, documents out, plain dicts and no fakes.)
`test_hardware_decode.py` (#202) is the fourth: NVDEC is
one decision across `ffmpeg` (the probe), `video/ffmpeg` (flags, fallback,
report) and the three video routes that carry the report, and the failure worth
testing is a decode that ran one way and reported another. `test_cut_resolution.py`
(#187) is the fifth: the delivery resolution is one device across `cut/resolution`,
`cut/validate`, `resolve/settings` and `resolve/build`, and a timeline that ignored
the setting is indistinguishable from one that never asked for it until the render
exists. Live tier: `test_live_smoke.py` (module-level
`pytest.mark.live`) and five `@pytest.mark.live` tests in
`test_live_analysis.py` — four over installed models, plus the #192 wind split
over the director's own separated stems, the one test that opts out of the
per-test cache redirect through conftest's `machine_cache` fixture; everything
else is fake-tier. The live tier assumes no
project state it can build itself (#135): a session-scoped sweep clears the last
run's timelines, `a_known_cut` builds and makes current the short cut the export
and round-trip tests read, and `a_clip_with_hard_cuts` generates the scan clip
unless `RESOLVE_MCP_SCENE_SCAN_CLIP` names a real one. That variable is
**unset on the live box**: its pool was checked in #135 and holds no flattened
render, only raw continuous angles — so the generated clip is the default there,
and the variable is for a project that does have an edit to scan.
