# The 21.1 scripting API gap map: which workaround routes the new calls retire

Research for #264 (part of #263). Answers: against the DaVinci Resolve 21.1
scripting API, which of the routes this repo built around the gaps #9 charted
does the new API **retire**, which need a **live probe** first, and which
**stay**.

**Primary sources.** All API claims are quoted from the installed 21.1 stubs
and docs on the live box:

- `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\DaVinciResolveScript.pyi`
  (2765 lines; cited below as `pyi:<line>`)
- `…\Scripting\CHANGELOG.md` (`Last Updated: 1 Sep 2026`; `## 21.1` at line 8)
- `…\Scripting\README.md`
- the native Resolve MCP server's `search_scripting_api` (same stub corpus,
  used to prove **absences**)

Repo claims cite the module and line. Prior state of the gap map is issue #9's
resolution comment ("0. The actual gap map first (Resolve 21.0 API…)" and
"Decision table: gap → route").

**Nothing here was probed live.** No call in this pass touched the open
project; every verdict marked *probe first* names the probe that would settle
it.

---

## 0. What 21.1 actually added

`CHANGELOG.md:16-32`, verbatim, filtered to what touches this repo:

```
- Multicam APIs - MediaPool.CreateMulticamClip, TimelineItem.FlattenMulticam, PerformMulticamSmartSwitch, Timeline.AutoAlignClips.
- TimelineItem properties - Get/Set Fades, Speed, OutputBlanking, UseTimelineForOutputBlanking and GetType.
- Transition API - TimelineItem.AddTransition.
- Timeline Output blanking - Timeline.Get/SetOutputBlanking.
- Timeline Audio Normalization - Timeline.GetNormalizeAudioModes, NormalizeAudioLevel.
- Media Transcription - MediaPoolItem.GetTranscription.
- Audio Render APIs - Project.GetAudioRenderCodecs, GetAudioRenderFormats.
- Render Presets - Project.UpdateRenderPreset, SetQuickExportEnabledForRenderPreset.
- Project Settings Presets - Set/Delete/Import/Export/Update/Save/Get List for ProjectSettingsPreset.
- Set Audio Mappings - MediaPoolItem.SetAudioMapping, TimelineItem.SetSourceAudioChannelMapping.
```

**Three of #9's five "MISSING" rows close: transitions, fades, speed,
multicam. The fifth — trim/move — does not.** Verified by absence:
`grep "def SetStart\|def SetEnd\|def SetLeftOffset\|def SetRightOffset"` over
the 21.1 stub returns nothing, while the getters are all present
(`pyi:2311` `GetStart`, `pyi:2314` `GetEnd`, `pyi:2332` `GetLeftOffset`,
`pyi:2335` `GetRightOffset`). #9's line "in/out fixed at append time" still
holds in 21.1. **Confidence: high** — this is a proven absence over the
complete stub, not a failed search.

The repo uses **none** of the 21.1 calls today:
`grep -rn "AddTransition\|SetFades\|SetSpeed\|OutputBlanking\|GetSelectedClips\|FlattenMulticam" src docs CONTEXT.md`
returns nothing.

---

## 1. Per-module verdicts

| Module | What it works around | 21.1 call(s) | Verdict |
|---|---|---|---|
| `resolve/tail.py` | "The scripting API cannot cut a transition… `Timeline` has no transition call at all, `TimelineItem` exposes `SetProperty` for a *static* `Opacity` and nothing whatsoever for audio level" (`tail.py:3-6`). Plus: no transition **getter**, so the confirm is a second OTIO export (`tail.py:294-296`). | `TimelineItem.SetFades(FadeInfo)` (`pyi:2575`), `GetFades()` (`pyi:2572`), `AddTransition(TransitionOptions)` (`pyi:2302`), `GetType()` (`pyi:2299`) | **probe first** → retire on a pass |
| `cut/otio.py` | The document-surgery half of the same route: "Nothing here talks to Resolve" (`otio.py:3`); its only callers are `tail.py` `inject`/`transitions`. | same as above | **probe first** → retire with `tail.py` |
| `resolve/takes.py` | Not a missing-call workaround. It drives the real Take API (`AddTake` `pyi:2452`, `SelectTakeByIndex` `pyi:2467`). Its *actual* workaround is ADR 0004 / #84: "Both take getters answer for the current timeline only (#84)" (`takes.py:177-182`). | none. 21.1 adds no fix for the current-timeline getter drift; `CHANGELOG.md:8-32` has no entry touching it. Multicam (`pyi:1954`, `2566`, `2569`) is a *different* architecture, not a replacement. | **keep** |
| `resolve/build.py` | `AppendToTimeline`'s silent failures — "An append onto a locked track returns TimelineItems and places nothing; an append that overlaps existing media slides to the next free frame and reports success; a still ignores `endFrame`…" (`build.py:30-37`) — plus "A tail is built twice, because the API cannot cut a transition" (`build.py:23`). | none for the appends. The tail double-build retires with `tail.py`. | **keep**, minus the tail staging path |
| `deliver.py` | "Resolve does not reliably overwrite; a name already taken can come back as `name_0.mp4` beside the old file" → `_clear()` (`deliver.py:19-24`, `:356`). And `MarkOut` inclusive-vs-half-open (`deliver.py:16-18`). | Nothing **new in 21.1**. But `RenderSettings.ReplaceExistingFilesInPlace: bool` — "Replace existing files in place" (`pyi:884-885`) — already exists, is absent from `CHANGELOG.md`, and the repo never sets it. | **probe first** (one setting), else keep |
| `resolve/render.py` | The WAV dead end (#32/#131): "Resolve 21.0.3 lists Wave among its render formats, returns an empty codec map for it, and refuses every (\"wav\", …) pair, so audio is reachable only through the stock preset" (`render.py:161-166`). Plus the wedged engine (#92) and the bare-`False` preset ambiguity. | `Project.GetAudioRenderFormats()` — "Returns a dict (format -> file extension) of available audio render formats" (`pyi:1825-1826`); `GetAudioRenderCodecs(audioRenderFormatFileExtension)` — "Returns a dict (codec description -> codec name) of available audio codecs for the given format" (`pyi:1831-1832`) | **probe first** for the WAV route; **keep** everything else |

### `resolve/tail.py` + `cut/otio.py` — the evidence, and the probe

The tail is a fade-out on the picture and a fade-out on the mix. 21.1 gives
that directly:

> `def SetFades(self, fades: FadeInfo) -> bool:`
> `"""Sets the fade durations (in frames) for the item's video or audio fader from a dict {FadeIn, FadeOut}"""` — `pyi:2575-2576`
>
> `class FadeInfo(TypedDict, total=False): FadeIn: int  """Duration in frames"""  FadeOut: int  """Duration in frames"""` — `pyi:290-294`

Three things make this a strong retire candidate rather than a lateral move:

1. **The fader needs no neighbouring clip.** `cut/otio.py:13-17` exists
   entirely because "Resolve pads every exported track with a trailing `Gap`
   out to the length of the *timeline*… A transition appended to the end of
   that track would dissolve black into black." A fader is a property of the
   one item; the trailing gap is irrelevant to it. The whole `_last_clip` /
   `_span` / `_timeline_rate` apparatus (`otio.py:151-153`, `:271-275`) loses
   its reason to exist.
2. **There is now a getter.** `tail.py:294-296` — "There is no getter for a
   transition anywhere in the scripting API, so 'did the dissolve land' can
   only be asked by exporting the timeline again and looking." `GetFades()`
   (`pyi:2572`) is that getter. The export-again confirm collapses to a
   read-back, which is the discipline the rest of the repo already uses.
3. **It kills the name-laundering problem.** `tail.py:19-21` — Resolve renames
   what it accepts (`Fade to Black` → `Cross Dissolve` on video,
   `Cross Fade 0 dB` on audio), "so the check counts transitions and never
   trusts a name." A fade duration in frames has no name to launder.

It also removes `tail.py`'s file-handle workaround (#26: "Resolve holds what
it exported open for the life of the process… so a name is spent once used",
`tail.py:148-151`) and `build.py:250-252` ("the staging timeline's setting
does not travel with the OTIO document" — re-applying resolution after the
round trip).

**Probe (live smoke, `mcp-tests-zinc`, not the client project), three
questions:**

- **P1.** On the last V1 item of a built cut, does
  `SetFades({"FadeOut": n})` produce the picture fade the tail means — a fade
  *to black* — and does `GetFades()` read `n` back? The stub says "the item's
  video or audio fader" (`pyi:2573`) but never says the video fader fades to
  black rather than to transparent-over-what-is-below. On a V1 item with
  nothing underneath those are the same picture; on an overlay they are not.
  **Confidence in the reading: medium** — this is the one ambiguity that
  decides the verdict.
- **P2.** Does `SetFades` on the audio item of the master mix fade the mix,
  and is the *audio* item reachable? `TimelineItem.GetType()` returns
  `'video' | 'audio' | 'generator' | 'transition'` (`pyi:2300`), and
  `GetLinkedItems()` exists, so the mix item is addressable — but the tail
  fades a mix that outlives the picture, so the two calls take different
  durations and different items.
- **P3.** Does `Resolve trims a dissolve to the handles the shot actually has`
  (`tail.py:302-304` — "a 40-frame fade can come back as a 12-frame one with
  the count still matching") also apply to a fader? A fader is not a
  dissolve and should need no handles; `GetFades()` read-back settles it.

**If P1 and P2 pass**, `resolve/tail.py` and `cut/otio.py` both retire, the
`(tail staging)` timeline disappears, and `build.py`'s tail route becomes a
`SetFades` call after the append read-back. `resolve/interchange.py` is *not*
retired: it is the generic OTIO export/import surface behind
`tools/timeline.py` and #9's fast read channel, and it has callers of its own.

**If P1 fails**, `AddTransition` is the fallback — but see §2, it is the more
constrained of the two.

### `resolve/render.py` — the WAV probe

`render.py:161-166` records a live 21.0.3 finding: Wave appears in
`GetRenderFormats()` but `GetRenderCodecs("wav")` returns `{}` and every
`SetCurrentRenderFormatAndCodec("wav", …)` pair is refused, so audio export
runs preset-first and "asking for the pair first spent one guaranteed failure
on every export there (#131)".

21.1 splits audio out into its own namespace: `GetAudioRenderFormats()`
(`pyi:1825`) and `GetAudioRenderCodecs(ext)` (`pyi:1831`). **This is exactly
the shape of an answer to #32** — the empty codec map was the video codec
lookup being asked an audio question.

**Probe (live smoke):** `Project.GetAudioRenderFormats()`, then
`GetAudioRenderCodecs("wav")`, then
`SetCurrentRenderFormatAndCodec("wav", <codec name from that dict>)`.

**The known limit:** there is **no** `SetCurrentAudioRenderFormatAndCodec` —
`grep "def SetCurrent"` over the stub yields only
`SetCurrentRenderFormatAndCodec` (`pyi:1837`), `SetCurrentRenderMode`,
`SetCurrentTimeline`, `SetCurrentTimecode`. So the new getters may *describe*
the audio codecs without there being any setter that accepts them.
**Confidence: medium** — the setter's docstring ("Sets given render format and
render codec as options for rendering", `pyi:1838`) does not distinguish audio
from video, which is weak evidence that one setter serves both. Until the
probe runs, keep the preset-first ordering; it costs nothing and is already
correct.

### `deliver.py` — the overwrite probe

`RenderSettings.ReplaceExistingFilesInPlace: bool` (`pyi:884-885`) is not in
`CHANGELOG.md` for 21.1, 21.0 or any 20.x entry — it predates the window this
changelog covers. The repo has never set it (`deliver.py:232-236` sets only
`TargetDir`, `CustomName`, `SelectAllFrames`/`MarkIn`+`MarkOut`, deliberately:
"Nothing else is set: forcing `ExportVideo`/`ExportAudio` here would silently
contradict an audio-only or video-only preset the director saved
deliberately").

**Probe:** set `ReplaceExistingFilesInPlace: True` and render over an existing
file. If Resolve replaces rather than writing `name_0.mp4`, `_clear()`
(`deliver.py:356`) and the `refresh` refusal lose their reason. **Confidence
the stub means what the repo needs: low** — "Replace existing files in place"
is one line with no statement about the `_0` rename behaviour, and
`UseUniqueFilenames` (`pyi:840`) / `UniqueFilenameStyle` (`pyi:842`) may be
the real lever. Do not touch `_clear()` without the probe; the failure mode is a
job reporting a path that holds yesterday's export, which is silent.

**Not retired regardless:** `MarkOut` is still inclusive
(`RenderSettings.MarkOut`, `pyi:834`) and the half-open conversion at
`deliver.py:16-18` stays.

---

## 2. What the new calls cannot do

Answers to the ticket's three questions, plus what the stubs and README
actually constrain.

**`AddTransition` on a gap edge — unanswerable from the stub; assume it
fails.** The full signature is:

```python
def AddTransition(self, transitionOptions: TransitionOptions) -> TimelineItem | None:
    """Adds a transition of the given type/category to the start or end of this item.
       Returns the created transition item or None on failure."""   # pyi:2302-2303

class TransitionOptions(TypedDict, total=False):   # pyi:1232-1242
    type: str        # "Transition type name, e.g. 'Cross Dissolve'"
    category: str    # "Transition category: 'simple', 'fusion', 'ofx' or 'audio'"
    position: str    # "Edge of the item to attach the transition to: 'start' or 'end'"
    alignment: str   # "Placement relative to the edge: 'left', 'center' or 'right'"
    duration: int | None  # "Duration in frames (default: automatically calculated)"
```

The stub is silent on whether the edge needs a neighbour. A `Cross Dissolve`
at `alignment: 'center'` structurally requires media on both sides, so a gap
edge should refuse; `'left'` (entirely inside the outgoing clip) is the
plausible fade-to-black shape. This is the same `Fade to Black` question
`tail.py:19-21` already answered empirically for the OTIO route. **Confidence:
low — probe it.** Note the failure mode is quiet: `None`, not an exception.

**Transitions cannot be removed or enumerated by any dedicated call.**
`grep "def Delete\|def Remove"` over the whole stub returns no
`RemoveTransition` / `DeleteTransition`. The only route is that
`AddTransition` returns a `TimelineItem` and `GetType()` reports `'transition'`
(`pyi:2299-2300`), so a transition is presumably deletable via
`Timeline.DeleteClips([item])` (`pyi:2182`) and enumerable via
`GetItemListInTrack` + `GetType()`. **Confidence: medium** — the type system
says a transition *is* a `TimelineItem`, but `DeleteClips`' docstring says
"Deletes specified TimelineItems from the timeline, performing ripple delete
if second argument is True" and a rippling transition delete is not obviously
meaningful.

**Fades on audio-only items: yes, one call for both.** `GetFades` returns
"the fade durations (in frames) for the item's **video or audio** fader"
(`pyi:2573`, emphasis added) — the item's own type selects which fader. There
is no separate audio-fade call and no way to set both from one item; the mix
item must be addressed separately (via `GetLinkedItems()` or
`GetItemListInTrack("audio", n)`).
**What `FadeInfo` cannot express:** a fade *shape*. It is two integers
(`pyi:290-294`), so there is no curve, ease or keyframe — anything beyond a
linear fader duration still needs the Fusion/OTIO route.

**`SetSpeed` with ripple: yes, and it is opt-in.**

```python
class SpeedOptions(TypedDict, total=False):   # pyi:947-955
    Percentage: float          # "Speed in percentage, e.g. 110.0 (0.0 = freeze frame)"
    PitchCorrection: bool      # "Pitch correction of linked audio (default: clip's existing state)"
    StretchKeyframesToFit: bool  # "Stretch keyframes to fit (default: False)"
    RippleTimeline: bool       # "Ripple timeline (default: False)"
```

`RippleTimeline` defaults to `False`, which for this repo is the safe default:
a sequential V1 with anchored overlays (`build.py:11-16`) would have every
overlay's record frame invalidated by a ripple, and the cut file — not the
timeline — is the source of truth for those positions. **What it cannot do:**
a speed *ramp*. `Percentage` is one scalar for the whole item; there is no
keyframe array, so #9's "Speed ramps → rendered intermediate" row survives
unchanged. `StretchKeyframesToFit` stretches keyframes that already exist; it
does not create retime keyframes.

**Multicam cannot be created from timeline items.**
`MediaPool.CreateMulticamClip(clips: list[MediaPoolItem], multicamOptions)`
(`pyi:1954`) takes **media pool** items, so it is a pre-build operation, not
something applicable to an existing stacked-track cut. `MulticamOptions`
(`pyi:357-379`) syncs by `MULTICAM_ANGLE_SYNC_TIMECODE` (default),
`_AUDIO`, `_IN`, `_OUT` or `_MARKER`; `channelConfig` and `splitAtGaps` are
documented as `"(angleSyncMode=MULTICAM_ANGLE_SYNC_AUDIO only)"`.

**`PerformMulticamSmartSwitch` is AI and its constraints are real.**
`SmartSwitchSettings` (`pyi:903-923`) caps `minEditDuration` at
`"0.5 to 10.0 (default: 1.0)"` and `editChangeDelay` at `"0.0 to 2.0"`, and
`switchOnVideoOnly` is `"not supported in adaptive/source mode"`. It decides
angles itself — it is a competitor to the concert pillar's style-driven
angle choice, not a tool for it. README.md:421-426 adds that any Studio/AI
call "can return with a False status" when Extras are not downloaded;
SmartSwitch is not in the README's named Extras list (`README.md:428-435`),
so whether it needs one is **unconfirmed**.

**Nothing addresses the two limitations that cost this repo the most.**
Neither ADR 0004 (getters answer only for the current timeline — `takes.py:177-182`)
nor the `AppendToTimeline` silent-failure set (`build.py:30-37`) has any 21.1
entry. The read-back-everything discipline is not retired by anything here.

**Two calls the ticket named do not exist.** `search_scripting_api` over
`DaVinciResolveScript.pyi` for `GetTimelineFromMediaPoolItem` and
`GetTimelineClipSelection` returns neither. The real names are:

- `Timeline.GetSelectedClips() -> list[TimelineItem]` — "Returns the currently
  selected timeline items" (`pyi:2122-2123`), added in **21.0.4**
  (`CHANGELOG.md:38`), not 21.1.
- `MediaPool.GetSelectedClips() -> list[MediaPoolItem]` (`pyi:1960`).
- There is **no** media-pool-item → timeline lookup at all. Going from a
  `MediaPoolItem` to the timeline built from it is still name matching.
  **Confidence: high** (proven absence over the full stub).

**README-level caveats that apply to everything above.**
`README.md:414`: "Keys marked [Active Timeline Only] are only supported when
the timeline item is from the active timeline" — the 21.1 `TimelineItem`
properties inherit the ADR 0004 hazard.
`README.md:408-410`: `SetProperties` is all-or-nothing — "either all the
properties are set or none of them is, with the returned status being False
and an error message naming the offending key". The new
`SetFades`/`SetSpeed`/`SetOutputBlanking` are *separate* calls, not property
keys, so they do **not** get that atomicity.
`CHANGELOG.md:14`: "Overloaded function signatures are deprecated" in 21.1 —
every new call takes a single options dict.

---

## 3. 21.1 calls with no counterpart here that the pillars would want

Ranked by what the concert (P3) and rough-cut (P4) pillars actually do.

**1. `MediaPoolItem.GetTranscription(useNestedClipTranscription=False)`
(`pyi:2098`) — P4, high value.** Returns word-level timings with speaker
labels:

```python
class TranscriptionSegment(TypedDict, total=False):   # pyi:1212-1222
    start: str    # "Start timecode, e.g. '01:00:02:05'"
    end: str
    text: str     # "Concatenated text of all words in segment; '(...)' denotes silence"
    speaker: str | None  # "Speaker name if detected, None otherwise"
    words: list[TranscriptionWord]   # "Individual words with timing"
```

The rough-cut pillar is transcript-driven and this repo transcribes with its
own CUDA whisper path (`src/resolve_mcp/analysis/whisper.py`,
`analysis/transcribe.py`). **This does not retire that** — the local path is
GPU-first by policy (CLAUDE.md "Compute device") and produces the word timings
`virtual_transcript` needs. Its value is as a *second reading*: Resolve's
transcription carries `speaker`, which is speaker **diarisation** the repo
does not compute, and it is already aligned to the media pool item. Caveat:
README.md:433-434 — "Transcription workflows with extended language models"
need an Extras download, with built-in models as fallback.

**2. `Timeline.GetSelectedClips()` (`pyi:2122`) — both pillars, high value.**
The review round is a director pointing at a shot. Today every
"try the other angle on *that* shot" has to be translated into a segment index
by hand before `swap_take` can run (`takes.py` takes a `segment` argument).
Reading the selection turns a click into the argument. Note it is a
**21.0.4** call, so it needs no 21.1 to adopt.

**3. `Timeline.NormalizeAudioLevel(items, options)` + `GetNormalizeAudioModes()`
(`pyi:2188`, `2194`) — P3, medium.** `NormalizeAudioOptions.normalizationMode`
is documented as a `"Mode name from GetNormalizeAudioModes() (default: 'Sample
Peak Program')"` (`pyi:383`) with
`NORMALIZE_AUDIO_SET_LEVEL_RELATIVE | _INDEPENDENT`. A concert deliverable is
one mix at one level; this is the first scripted loudness control the repo
could have. Today nothing in `src/` sets audio level at all beyond
`TimelineItemProperties.AudioVolume`.

**4. `Timeline.AutoAlignClips(items, AutoAlignOptions)` (`pyi:2191`) — P3,
medium.** `AutoAlignOptions.SyncUsing` is
`AUTO_ALIGN_CLIPS_USING_WAVEFORM | _USING_TIMECODE` (default timecode),
`UseTrack` is a track index for waveform mode (`pyi:128-132`). The concert
pillar's whole premise is multiple angles against one master mix; waveform
alignment of angles to the mix is currently the human's job in the GUI before
the pool is ever read.

**5. `Timeline.SetOutputBlanking` / `TimelineItem.SetOutputBlanking` /
`SetUseTimelineForOutputBlanking` (`pyi:2290`, `2554`, `2560`) — P3, medium.**
Four integers in pixels (`OutputBlanking`, `pyi:391-399`). The per-item
version plus `GetUseTimelineForOutputBlanking` is the letterbox-one-shot
control a vertical-Instagram deliverable would want; `deliver.py:30-33`
already says "a vertical Instagram deliverable is a timeline and a cut, not a
preset", and blanking is the missing third thing.
Caveat: `GetOutputBlanking` on an item "will be empty if the timeline's output
blanking is used" (`pyi:2558`) — so the getter conflates "no blanking" with
"inherited", and `GetUseTimelineForOutputBlanking` must be read alongside it.

**6. `TimelineItem.SetSpeed` (`pyi:2386`) — P3, medium.** Not a workaround
retirement (the repo has no speed feature to retire), but a new device: a
constant-speed shot with `RippleTimeline: False` fits the sequential-V1 model
without invalidating a single overlay anchor. `0.0 = freeze frame`
(`pyi:949`) is a still-frame device with no current equivalent.

**7. `Project.UpdateRenderPreset(name)` +
`SetQuickExportEnabledForRenderPreset(name, enabled)` (`pyi:1777`, `1780`) —
low.** `deliver.py:6-11` deliberately refuses to own preset contents
("the settings belong to the project, not to the server"), so
`UpdateRenderPreset` is *against* that decision and should not be adopted
without revisiting it.

**8. `Project` settings-preset family — `GetProjectSettingsPresetList`,
`SetProjectSettingsPreset`, `UpdateProjectSettingsPreset`,
`DeleteProjectSettingsPreset` (`pyi:1741-1753`) — low, but note the
deprecation.** `README.md:458-466` marks `Project.GetPresetList()` and
`SetPreset(presetName)` **deprecated**, along with `GetPresets()`,
`GetRenderPresets()` and `GetRenderJobs()`. The repo uses none of them
(verified by grep over `src/`), and `resolve/render.py:96,121` already uses
the surviving `GetRenderPresetList` / `LoadRenderPreset`. **No migration
needed** — recorded so a future session does not reach for the deprecated
names.

**9. `TimelineItem.SetSourceAudioChannelMapping` (`pyi:2530`) and
`MediaPoolItem.SetAudioMapping` (`pyi:2074`) — low.** The repo reads
`GetAudioMapping` nowhere; concert kit audio arrives as a separate master mix.
Listed for completeness.

**Explicitly not wanted:** `TimelineItem.PerformMulticamSmartSwitch`
(`pyi:2566`). It is an AI that picks angles. The concert pillar's product *is*
the angle choice, made from the style layer against analysis; handing that to
SmartSwitch would replace the pillar, not serve it. `FlattenMulticam`
(`pyi:2569`) and `CreateMulticamClip` (`pyi:1954`) are only interesting if
#4's stacked-track decision is ever revisited — and per §1 they do not retire
`takes.py`, which is not a multicam workaround.

---

## 4. Summary

| | Modules |
|---|---|
| **Retire** (conditional on one probe) | `resolve/tail.py`, `cut/otio.py` — both on P1/P2 passing |
| **Probe first** | `resolve/render.py` (WAV via `GetAudioRenderCodecs`), `deliver.py` (`ReplaceExistingFilesInPlace`) |
| **Keep** | `resolve/takes.py`, `resolve/build.py` (minus the tail double-build) |

**The single most consequential finding:** #9's five-row gap map is now a
one-row gap map. Transitions, fades, speed and multicam all closed in 21.1;
**trim/move did not**, and it was always the row that forced the OTIO
round-trip for structural work. So the tail — the *one* thing the repo
actually built the round trip for — can likely leave, while the round trip
itself (`resolve/interchange.py`) keeps its other reason to exist: #9's
"Splits, reordering, multi-track surgery → OTIO round trip" row is unchanged.

**Two probes, both live smoke, both on `mcp-tests-zinc`:**
`SetFades`/`GetFades` on a built cut's last V1 item and its mix item (P1/P2),
and `GetAudioRenderCodecs("wav")` → `SetCurrentRenderFormatAndCodec` (#32).
Neither was run in this pass.

*Convention note: `docs/research/` did not exist before this file. The repo's
existing note kinds are `docs/adr/` (decisions), `docs/context/` (area
narrative) and `docs/reference/` (standing tables). This is none of those — it
is a dated finding against an external API — so it takes the path #264 named.*
