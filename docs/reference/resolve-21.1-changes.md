# Resolve changes since 21.0 — the `get_whats_new` cache

One `get_whats_new(since="21.0")` call against DaVinci Resolve Studio 21.1,
made 2026-09-15 and saved here so a session reads the file instead of
re-fetching (one research session made the call 13 times). The tool returned
the 21.0.1–21.0.4 entries below and **no 21.1 entry**; call `get_whats_new`
again only for a version later than 21.0.4, and append the result here.

## Scripting API lines (what an agent working `resolve/` cares about)

- 21.0.4 — get the timeline clip selection; get a timeline object from its
  media pool timeline entry; `SetRenderSettings` options for handles,
  extents and data burn-in; DRT imports relink to existing nested clips.
- 21.0.3 — list project attributes in a project bin; user-preference
  presets; list UI layout presets; list and delete data burn-in presets;
  wrong audio-mapping scripting results fixed.
- 21.0.1 — `RemoveMotionBlur` uses the correct encode parameters;
  `GenerateSpeech` character limit made consistent; transcription honours
  the project settings language.

## Full entries

### 21.0.4 — 2026-08-05

- Relink clips can now relink proxies with different formats.
- DRT imports automatically relink to existing nested clips if present.
- Selected clip stays in view with clip filter changes in the Color page.
- Support for importing Affinity legacy file formats.
- Support for Sony FX-5 X-OCN clips.
- Scripting API to get timeline clip selection.
- Scripting API to get timeline object from media pool timeline entry.
- SetRenderSettings API options for handles, extents and data burn.
- Addressed realtime playback issues on large cached timelines.
- Addressed power bin navigation bar issue in media pool windows.
- Addressed segment clip suffixes in media management.
- Addressed issue with edit index ordering.
- Addressed source viewer audio issue when pasting timecode.
- Addressed multiple retime speed curve editing issues.
- Addressed path length issues for Fusion templates on Windows.
- Addressed missing marker notes tag for data burn in.
- Addressed issue with decoding some DPX files.
- Addressed issue with decoding some CR2 stills.
- General performance and stability improvements.

### 21.0.3 — 2026-07-22

- New legacy speed mode retime curve option.
- DNG pre tone curve is enabled by default in new projects.
- Addressed missing QuickSync encode options on older Intel GPUs.
- Addressed issue with adding keyframe at a subframe.
- Addressed issue with pasting keyframes to linked audio clips.
- Added configurable keyframe ease actions.
- Inspector frame display for interlaced transition durations.
- Addressed interlaced generator and transition duration interpretation.
- Addressed audio duration for inserted segment of interlaced media.
- Addressed field dominance detection issue for some interlaced clips.
- Addressed Fusion issue with importing PSDs with disabled layers.
- Addressed issue with rendered Magic Mask for photos in album.
- Addressed bezier window move when adjusting vertex smoothness.
- Addressed issue with decoding some Sony BURANO X-OCN clips.
- Addressed preview issue with HDR still exports on some browsers.
- Addressed wrong audio mapping scripting results in some scenarios.
- Updated DaVinci Resolve 21 reference manual.
- Replay SDI output now always shows selected camera.
- Scripting API support to list project attributes in a project bin.
- Scripting API support for user preferences presets.
- Scripting API support for listing UI layout presets.
- Scripting API support for listing and deleting data burn in presets.
- Addressed installation issues on some Windows systems.
- Custom install location for Windows ARM encode SDK plugins.
- General performance and stability improvements.

### 21.0.2 — 2026-07-02

- Addressed H.264 and H.265 NVIDIA decode performance.
- Addressed thumbnail preview when bypassing color or Fusion.
- Improved display of retime speed curve.
- Addressed issue with pasting copied keyframes between clips.
- Addressed issue with video inspector zoom minimum values.
- Addressed issue with slower IntelliSearch performance.
- Addressed bit depth when exporting current frame as TIFF still.
- Addressed inconsistent selection of source tape.
- Addressed inspector unicode display issue with spell check.
- Addressed some Fusion transition defaults for vertical timelines.
- Addressed media pool custom sort persistence issue.
- General performance and stability improvements.

### 21.0.1 — 2026-06-24

- Addressed multiple DNG and Apple ProRAW color issues.
- Addressed issue with automatic smart bins after deleting keywords.
- Addressed issue with multiple linked audio in media management.
- Addressed multiple Resolve FX issues in photo page.
- Addressed issue with key shortcut to switch viewer in photo page.
- More consistent creation of new photo albums.
- Addressed color thumbnail refresh for photo transform indicator.
- Transcription now honors project settings language.
- Addressed exported bins not retaining generator and title properties.
- Addressed ease control display and sensitivity issues.
- Addressed keyframe issue when copying clips with Fusion effects.
- Addressed keyframe refresh for Fusion effects in the edit page.
- Addressed issue with 3D renders in Linux with non-English locales.
- Addressed Fusion viewer color issue for some RCM settings.
- Addressed issue with saturation limits in Fusion gradient controls.
- Addressed Fusion display issues with dual screen layouts.
- Addressed issue with non-English character inputs in Linux.
- Disabling MultiMaster now disables trim blanking controls.
- Addressed crash in some scenarios with CineFocus.
- Addressed lag when toggling bypass grades and Fusion effects.
- Addressed occasional issue with Fairlight loudness meters.
- Addressed data burn display of good take tag in upgraded projects.
- Addressed project manager scroll lag for large project libraries.
- Support for Sony Alpha 7R VI ARW RAW stills.
- Support for decoding Affinity RGB 16-bit formats.
- Addressed a color issue with MainConcept H.265 HDR renders.
- Addressed a color issue with Windows native H.265 HDR renders.
- RemoveMotionBlur API now uses correct encode parameters.
- Addressed character limit consistency in GenerateSpeech API.
- General performance and stability improvements.
