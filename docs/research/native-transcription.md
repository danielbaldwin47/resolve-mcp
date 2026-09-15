# Native transcription vs. the faster-whisper job (#267)

Researched 2026-09-15 against DaVinci Resolve Studio **21.1.0.17** on the
live box, the 21.1 scripting stubs, and this repo at `origin/main`. Part of
the native-server prior-art sweep (#263).

**Recommendation up front: keep `transcribe_audio` (faster-whisper) as the
rough-cut source.** Native transcription carries no per-word confidence,
which is the single field the P4 self-review is built on. Take native as an
optional *speaker* sidecar if multi-person rough cuts ever need one — never
as the word source. The trade-off is stated in full at the end.

## Sources

- Stubs: `C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/DaVinciResolveScript.pyi`
  — `Transcription`, `TranscriptionSegment`, `TranscriptionWord` (one block,
  quoted below); `MediaPoolItem.TranscribeAudio` / `GetTranscription` /
  `ClearTranscription` (lines 2059-2100); `Folder` equivalents (2617-2628);
  `ProjectSettings.transcriptionLanguage` / `speakerDetection` (718-721).
- Docs: Resolve `README.md` § *Studio and AI Scripting APIs* (the Extras
  list, line 433).
- Changelog (`get_whats_new`): 21.0 — "Background analysis for transcription
  and audio classification", "Scripting API support for speaker detection in
  audio transcription", "Scripting API to disable all background tasks for
  current session"; 20.x — "Transcription engine now offers extended language
  support", "Faster audio transcription and improved subtitle word timing",
  "Transcription now honors project settings language".
- **Live read-only probe** of the open project `2026-06-27_Dave_Ads (Copy)`
  (23.976, non-drop): eight media pool items already carry a transcription.
  Nothing was written — no `TranscribeAudio`, no `ClearTranscription`.
- This repo: `analysis/transcript.py`, `analysis/transcribe.py`,
  `analysis/whisper.py`, `analysis/virtual.py`, `timing.py`,
  `docs/agents/rough-cut.md`.

## What native actually returns

The whole type surface, verbatim from the 21.1 stub:

```python
class Transcription(TypedDict, total=False):
    language: str          # Transcription language code
    segments: list[TranscriptionSegment]

class TranscriptionSegment(TypedDict, total=False):
    start: str             # Start timecode, e.g. '01:00:02:05'
    end: str
    text: str              # Concatenated text of all words; '(...)' denotes silence
    speaker: str | None    # Speaker name if detected, None otherwise
    words: list[TranscriptionWord]

class TranscriptionWord(TypedDict, total=False):
    start: str             # Start timecode
    end: str
    text: str              # Word text; '(...)' denotes silence
```

Live shape, from the probe (`Video - Ad 4.MP4`, first two segments):

```json
{"start": "00:00:00:00", "end": "00:00:15:22", "text": "(...)",
 "words": [{"start": "00:00:00:00", "end": "00:00:15:22", "text": "(...)"}]}
{"start": "00:00:15:22", "end": "00:00:17:01", "text": " Are you tired of learning how to\u2026",
 "words": [{"start": "00:00:15:22", "end": "00:00:16:01", "text": " Are"},
           {"start": "00:00:16:01", "end": "00:00:16:04", "text": " you"}, ...]}
```

Five things the probe settles that the stub does not:

1. **`speaker` is absent, not `None`, when detection is off.** Every observed
   segment's key set is exactly `{start, end, text, words}`. `total=False`
   means a reader must use `.get("speaker")`, never `segment["speaker"]`.
   Project setting `speakerDetection` read `"0"` on this project.
2. **Silence is a word.** A gap is emitted as a whole segment whose single
   word is `"(...)"`, spanning the gap. On `Video - Ad 4.MP4`, 18 segments,
   of which the long leader is one 15.9-second `"(...)"` word.
3. **Word text keeps faster-whisper's leading space** — `" Are"`, `" you"`.
   Non-speech is annotated inline too: `Video - Sax.MP4`'s only content is
   one word `" ( Music )"` spanning 10 seconds.
4. **Hallucinations arrive unmarked.** `Video - Ad 1.MP4` segment 2 is the
   single word `" SACS]"` occupying one frame. With no confidence field there
   is nothing to flag it by.
5. **An untranscribed clip returns `None`**, not `{}` — the usual Resolve
   getter quirk the fakes already mimic.

## Comparison table

| Field / property | Native `GetTranscription` | Ours (`transcribe_audio`) |
| --- | --- | --- |
| Record | `Transcription{language, segments[]}` → `segments[].words[]` | `Transcription(words, language)`; segments flattened away at `whisper.py:43` |
| Word text | `text`, **leading space kept**; `"(...)"` for silence | `word`, stripped (`whisper.py:135`) |
| Word time unit | **Timecode string** `HH:MM:SS:FF`, whole frames | **float seconds**, rounded to 3 places (`PLACES = 3`) |
| Time origin | **Absolute source timecode** — see the hour-offset hazard below | **0 = start of the acquired WAV**, i.e. clip-relative already |
| Resolution | 1 frame = **41.708 ms** at 23.976 | 1 ms — ~42× finer |
| Word confidence | **none** | `confidence` 0-1 from faster-whisper `word.probability` |
| Speaker | `segment.speaker`, opt-in via `TranscribeAudio(useSpeakerDetection=True)` or project `speakerDetection` | **none** — no diarization anywhere in `src/` |
| Segments / phrase breaks | yes, with `text` per segment | **no** — only a flat word stream |
| Language | `Transcription.language` (probe: `"en"`); project `transcriptionLanguage` (probe: `"auto"`) | `Transcription.language`, detected or forced by the `language` param |
| Silence | inline `"(...)"` pseudo-words, model-derived | separate `silence` array, measured **off the waveform** (`-40 dB`, `0.35 s` min) — words stay pure speech |
| Model choice | none exposed; Resolve's engine, Extras-dependent | `model` param, default `large-v3`; device + compute type via `RESOLVE_MCP_WHISPER_*` |
| Where it runs | inside Resolve, its own scheduler, GPU unobservable | our process, CUDA-first, device named in the log and WARNed on CPU (#202) |
| Audio it hears | the clip as Resolve mixes it | ffmpeg extract (clip) or rendered timeline mix (`audio/acquire.py`), 48 kHz / 24-bit |
| Cache / invalidation | persists in the project; **nothing dates or hashes it** | keyed on the audio's `content_sha256` + params; a re-edit misses the cache |
| Completion | `TranscribeAudio() -> bool`, **no status API** | job id, progress bands, terminal state, poll with `get_job` |
| Cost to run | free, no GPU minutes, no ffmpeg pass, no model download | a GPU pass per source, plus the extract/render |
| Studio / Extras | Studio only; extended languages need an Extras download | none |

## Timecode-to-frame hazards, with the formulas

`timing.py` is the module that would have to absorb all of this. Its current
surface: `frames_from_timecode(value, fps) -> int | None`,
`frames_from_seconds(seconds, fps, snap)`, `timecode(frames, fps)`,
`dual_time`, `_nominal_rate(fps) = max(round(fps), 1)`.

### H1 — Parse straight to frames; never route native timecode through seconds

A native timecode counts at the **nominal** rate, which `_nominal_rate`
already implements: 23.976 counts 0-23, 29.97 counts 0-29. The parse is

```
frames = ((hh * 60 + mm) * 60 + ss) * nominal + ff        # nominal = round(fps)
```

which is exactly what `frames_from_timecode` does today — so native word
times need **no new math**, only the existing parser. The hazard is the
detour: converting the label to wall-clock seconds and back through
`frames_from_seconds` mixes the counting rate (24) with the media rate
(23.976) and drifts by `1 - fps/nominal` ≈ 0.1 %.

Worked, from the probe — `Video - Ad 1.MP4`, last word `" workshop."` ending
`00:02:16:23`:

```
frames_from_timecode("00:02:16:23", 23.976)      -> 3287            # correct
3287 / 24 = 136.9583 s ; frames_from_seconds(..) -> 3283            # 4 frames late
```

One frame of drift per ~1000 frames (~42 s); **~86 frames (3.6 s) over an
hour-long source**. An adapter must hand `virtual_transcript` the integer
frame, not a reconstructed `seconds`.

### H2 — Clip start TC: the one-hour offset is real, and it is in this project

The probe found it. Five camera-original clips have `Start TC 00:00:00:00`
and word timecodes starting at `00:00:00:00`. Three timeline-derived items
(`Ads_Horizontal_Main`, `Ads_Veritcal`, `Ads_Square`) have
`Start TC 01:00:00:00` and their first word at **`01:00:07:06`**. Native
timecodes are absolute source timecode, not clip-relative.

`virtual_transcript` compares words against `segment["in"]`/`["out"]`, which
are **source frames counted from zero** (`virtual.py:401`). So:

```
source_frame = frames_from_timecode(word["start"], fps)
             - frames_from_timecode(item.GetClipProperty("Start TC"), fps)
```

Skip the subtraction on an `01:00:00:00` camera and every word lands
**86 400 frames** (one hour at nominal 24) past the end of the clip — not a
subtle drift, but the failure mode is silent: `_within` finds no word inside
any span, `counts.words` reads 0, and the cut reviews clean. Our whisper path
has no such term because the WAV starts at the clip's first frame.

The timeline rebase after that is unchanged from `virtual.py:129`:
`at + source_frame - span[0]`.

### H3 — Drop-frame is refused, and refused silently

`frames_from_timecode` returns **`None`** for any `HH:MM:SS;FF` string
(`_DROP_FRAME`, `timing.py:67`), logging at debug — by design, "drop-frame
notation is not v1". Native transcription on a 29.97 DF project would
therefore produce a transcript in which every word's time is `None`, with no
exception raised. This project is 23.976 / `timelineDropFrameTimecode = "0"`,
so the hazard is latent, not live — but `timelineFrameRate` accepts
`'29.97 DF'` (Resolve README), and concert footage off 29.97 cameras is
exactly where it would land.

The formula `timing.py` would need, if native became the source:

```
total_minutes = 60 * hh + mm
dropped       = drop_per_minute * (total_minutes - total_minutes // 10)
frames        = nominal * (3600 * hh + 60 * mm + ss) + ff - dropped
# drop_per_minute: 2 at 29.97 (nominal 30), 4 at 59.94 (nominal 60)
```

Our seconds-based path needs none of this: it never sees a timecode string.

### H4 — Frame quantisation collapses short words to zero length

Native word bounds are whole frames, so a word shorter than 41.7 ms gets
`start == end`. The probe caught one: `{"start": "00:02:16:02",
"end": "00:02:16:02", "text": " See"}`.

`_within` (`virtual.py:222-226`) drops a word whose `end <= span[0]`. Our
seconds path never produces `end == start` because the out point *ceils*
(`OUT_POINT = "ceil"`), so a word at the segment's in point survives. A
native word does not: at `span[0] == 3278` the word `" See"` above is
discarded, and the missing word never surfaces as a finding. An adapter must
force `end_frame = max(end_frame, start_frame + 1)`.

### H5 — Sub-frame resolution is gone, not merely coarser

`dual_time` keeps frames as the authoritative number and derives seconds from
the true fractional fps, so a native-sourced transcript still round-trips.
What is lost is the distinction `virtual_transcript` uses at a seam: with 1 ms
words you can tell "this word begins 5 ms after the cut" (a clipped
consonant) from "38 ms after" (clean). With frame-quantised words both read
as the same frame, and the W3 clipped-word rule fires on frame equality
alone. 21.1's "improved subtitle word timing" improves the estimate; it does
not add a sub-frame field.

### H6 — Nothing invalidates a native transcription

The probe's sharpest result. `Ads_Veritcal` ends at `01:02:49:03`
(2 min 49 s), but the transcription attached to it runs to
**`01:07:49:12`** — five minutes past the item's own end. All three
timeline-derived items return an identical 92-segment transcript. The
transcription is a stale artifact of an earlier, longer edit, and the API
offers no timestamp, hash or dirty flag to tell you so. `ClearTranscription`
is the only remedy, and it is a write.

Ours is keyed on the acquired audio's `content_sha256`: a re-edit changes the
bytes, the cache misses, the job re-runs. A native adapter would have to
range-check every word against the item's `Frames` property and refuse
out-of-range words — a check that flags staleness only when the edit got
*shorter*.

## Languages and Extras

- `ProjectSettings.transcriptionLanguage` is a language code, e.g. `'en'`;
  the probe read `"auto"`, and 21.x changed transcription to honour the
  project setting. `TranscribeAudio` takes no language argument — the
  language is project state, so transcribing two clips in two languages means
  mutating a project setting between calls.
- `ProjectSettings.speakerDetection` is `'0'` or `'1'`;
  `TranscribeAudio(useSpeakerDetection=None)` defers to it.
- Resolve README § *Studio and AI Scripting APIs*: "Transcription workflows
  with extended language models" require an Extras download, and "Languages
  from built in models will be used as a fallback if unavailable." So the
  language quality is a property of the **box**, not of the call, and a call
  that quietly falls back to a built-in model returns no signal that it did.
- Studio only. A free-Resolve box returns `False` from `TranscribeAudio` with
  no distinguishing error.

Ours: `model` is a call parameter (`large-v3` default), `language` is a call
parameter, and the device is logged and WARNed on a CPU fallback.

## Observing completion

**There is no status API.** The 21.1 stub has no `IsTranscriptionAvailable`,
no progress getter, no callback; `search_scripting_api` for
`transcri|background|status` returns only `TranscribeAudio`,
`ClearTranscription`, `GetTranscription`, the two project settings, and
`Resolve.DisableBackgroundTasksForCurrentResolveSession`.

The 21.0 changelog lists "Background analysis for transcription and audio
classification" as a general improvement, and ships the API to disable
background tasks for a session. Taken together, `TranscribeAudio() -> bool`
reads as *accepted*, not *finished*. (Unverified: confirming it would mean
calling `TranscribeAudio`, a write, which this probe deliberately did not
do.)

That leaves polling `GetTranscription` until it stops returning `None` — and
the three states an agent needs to tell apart are indistinguishable:

| State | What `GetTranscription` returns |
| --- | --- |
| still running | `None` |
| failed (Studio/Extras/system requirement) | `None` |
| finished, no speech in the clip | `None`, or one `"(...)"` segment |

Against that, our job record gives a job id, the progress bands
(`ACQUIRING 0.05` → `READING 0.2` → `MEASURING 0.85` → `WRITING 0.95`), a
terminal state, and an error when it fails. This alone disqualifies native as
an unattended source.

## How `virtual_transcript` would consume each

`virtual_transcript` reads the document generically (`_words_of`,
`virtual.py:329-339`): a top-level `words` list, each row indexed on
`"start"`, `"end"`, `"word"`, plus `float(word.get("confidence", 1.0))`.

**Ours** feeds it directly — that is what `transcript.document()` writes.

**Native** would need an adapter that:

1. flattens `segments[].words[]` into one `words` list;
2. drops every `"(...)"` word (else each silence becomes a delivered word: it
   joins into `text`, inflates `counts.words`, and a leader spanning a seam
   trips W3 on every cut);
3. strips the leading space from `text` and writes it under the key `word`;
4. parses each timecode to a frame with `frames_from_timecode`, subtracts the
   item's `Start TC` frame (H2), and widens zero-length words (H4);
5. either emits frames directly — which `_within` does not currently accept,
   it calls `frames_from_seconds` on `word["start"]` — or emits seconds
   computed at the **true** fps from the frame number, `frame / fps`, never
   from the timecode label (H1).

And then the rule that matters breaks anyway. `confidence` defaults to `1.0`
when absent, so a native transcript does not error — **W6 silently reports
zero unsure words**. `docs/agents/rough-cut.md` makes W6 a deliverable of the
cut report ("each one is a word you are delivering that the transcriber was
unsure of, at a frame the director can jump to"); on native it becomes an
always-empty section. A capability that disappears without an error is the
worst failure shape available, and `whisper.py:3-5` already recorded this
decision under #10.

What native would *add*: `segment.speaker`. `docs/agents/rough-cut.md` never
mentions speakers — the pillar keys transcripts by source alias, one per
camera or take, and the alias already carries the identity a speaker label
would. So the gain lands on a problem P4 does not currently have.

## Recommendation

**Keep `transcribe_audio` / faster-whisper as the rough-cut word source.**

What that costs, stated plainly: a GPU pass and an ffmpeg extract or timeline
render per source, where native is already sitting in the project for free;
a model download; and no speaker labels, ever, until something diarizes.

What it buys, and why it wins: per-word confidence — the field W6 and the
whole "read the words back before the director does" loop are built on;
millisecond word bounds instead of 41.7 ms ones at a seam; a clip-relative
origin with no hour-offset trap; a content-hash cache that a re-edit
invalidates, against a native transcript that the probe caught running five
minutes past its own clip; and an observable job instead of polling a getter
whose `None` means *running*, *failed* and *silent* at once.

Two narrower uses of native are worth keeping on the table, neither of which
displaces the word source:

- **A speaker sidecar.** If a multi-speaker rough cut ever needs real
  diarization, run native with `useSpeakerDetection=True` and merge
  `segment.speaker` onto our words by frame overlap. One field, one join, and
  our timing and confidence survive.
- **A free pre-pass for triage.** Native costs nothing to read on clips that
  already have it (eight in the probed project), so "which of these 40 clips
  has speech at all" can be answered before spending a GPU minute.

Both are additive. Neither changes the answer to the ticket's question: the
rough-cut source stays ours.
