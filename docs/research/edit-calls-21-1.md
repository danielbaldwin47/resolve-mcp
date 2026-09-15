# The 21.1 edit calls, probed live

Wayfinder ticket #268 (map #263). Resolve Studio 21.1.0.17, Windows 11 box, project
`mcp-tests-zinc`, via the native server's `run_script` / `run_script_unsafe` (ResolvePython
3.14), 2026-09-15. Source timeline `Taurus People Opening v1` (23.976 fps, 13 hard-cut
shots on V1 from two cameras, one continuous mix on A1, a 12-frame leading gap).
Scratch timelines: `PROBE 268 edit calls` (every probe stacked), `PROBE 268 tail dissolve`,
`PROBE 268 tail fader`. Probe: [`probes/edit_calls_21_1_probe.py`](probes/edit_calls_21_1_probe.py)
— a consolidation of the interactive calls below, not itself re-run end to end.

## Verdict for the map

The tail's OTIO round trip is replaceable by one native call. `AddTransition` at the last
shot's end with `alignment: 'left'` lands a dissolve whose OTIO export is byte-for-shape what
`cut/otio.inject` writes (`SMPTE_Dissolve`, `in_offset` = frames, `out_offset` = 0), and the
call returns the transition item, so landing is observable without a second export. Two
traps any adopter must carry: omitting `alignment` fails silently, and `SetSpeed` with
`RippleTimeline` cuts the concert mix.

## Findings

| Call | Result |
| --- | --- |
| `Timeline.GetTimelineClipSelection` | **Does not exist.** The attribute resolves to `None` (hence `'NoneType' object is not callable`); `hasattr` still says True, as for any name on the proxy. |
| `Timeline.GetSelectedClips()` | Works; `[]` with nothing selected. (`MediaPool.GetSelectedClips()` returns `None`, not `[]`, when empty.) |
| `AddTransition` — stub's example options, no `alignment` | Returns `None`, nothing changes, at a hard cut with handles both sides. Same for `{type}` alone. **`alignment` is effectively required.** |
| `AddTransition` — `end` + `center`, 12 f, hard cut 86851 | Lands 86845–86857. Returns a `TimelineItem` of `GetType() == 'transition'`. |
| … at the gap edge (`start` of first shot, 12 f gap before it) | `center` lands 86406–86418, half over the gap. A second (`right`) on the same edge returns `None`. |
| … at the timeline tail (`end` + `left`, last shot) | Lands 88546–88558, ending on the last frame — a dissolve to black. |
| `AddTransition` on audio | `type 'Cross Fade 0 dB', category 'audio'` lands (at an end, and at the start 86400–86412). `Cross Dissolve / simple` on audio returns `None`. |
| Transitions in track reads | `GetItemListInTrack` now **includes transition items** (13 → 14 entries). Any reader that treats every entry as a clip miscounts. |
| `SetFades` / `GetFades` | Works on audio and video items; returns `True`; reads back as floats (`12.0`). A partial dict leaves the other side alone (`FadeIn 6` survived `{FadeOut: 12}`). Fader fades do **not** export as OTIO transitions (only opaque "Resolve Effect" entries), so an OTIO-count check cannot see them — `GetFades` can. |
| `SetSpeed({Percentage: 50})` | `True`; the item keeps its span, nothing moves, timeline end unchanged. |
| `SetSpeed({Percentage: 200, RippleTimeline: True})` | `True`; the 194 f shot becomes 97 f, every later item shifts −97, timeline end 88558 → 88461, transitions move with their cuts. **It also cuts the unlinked mix on A1 at 87771 and deletes 97 frames of it** (second piece's left offset 87026 where continuity needs 86929): the music skips at the edit. |
| Offsets on a sped item | `GetLeftOffset`/`GetRightOffset` are scaled by the speed (55657 → 27828, 15025 → 7512 at 200%). |

## Against `resolve/tail`

| | OTIO round trip (today) | Native |
| --- | --- | --- |
| Video tail | inject `SMPTE_Dissolve` in/out 12/0, import, Resolve renames to `Cross Dissolve` | `AddTransition` end/left → exports as `Cross Dissolve`, `SMPTE_Dissolve`, 12/0 |
| Audio tail | `Cross Fade 0 dB` transition on each audio track's last clip | `AddTransition` audio category, or `SetFades` (fader — not an OTIO transition) |
| Landing check | a second export, count transitions | the call's return value, or `GetItemListInTrack` type `transition` |
| New timeline | staging timeline + `<base> vN` import | edits in place |

Not probed: a tail shot with no handle past its out point; `StretchKeyframesToFit`,
`PitchCorrection`; multi-track video tails; what the fader fade vs dissolve looks like — the
human's eye on the UI decides that (`PROBE 268 tail dissolve` vs `PROBE 268 tail fader`).
