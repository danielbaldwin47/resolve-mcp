# ADR 0004 — Editor-state getters answer only for the current timeline, and are reported as unknown

- **Status**: accepted
- **Date**: 2026-08-06
- **Context**: [#84](https://github.com/danielbaldwin47/resolve-mcp/issues/84), found during
  the #79 live pass against Resolve Studio 21.0.3.7; the getter sweep is the outstanding
  half of [#68](https://github.com/danielbaldwin47/resolve-mcp/issues/68)

## Context

On Resolve Studio 21.0.3.7, a small set of `Timeline` and `TimelineItem` getters answer
from the editor's state rather than from timeline data. Asked about a timeline that is
**not** the project's current one, each returns the falsy value of its own type — no
exception, no `None`:

| getter | non-current timeline | current timeline |
|---|---|---|
| `GetTakesCount` | `0` | true count |
| `GetIsTrackEnabled` | `False` | true state |
| `GetIsTrackLocked` | `False` | true state |

`GetIsTrackLocked` was found by the sweep below; the ticket opened on the other two.

This is the silent-wrong-value shape the wrappers in this repo exist to prevent, and it is
worse than a refusal because the answer is *plausible*. It has cost real time twice: a live
sweep read `takes: 0` across every smoke timeline and nearly recorded that a take selector
had not survived a swap, and a `False` from `GetIsTrackEnabled` was written into #32 as the
explanation for a WAV that turned out to be fine (the mix measures −17.0 dB mean).

### The sweep

Every `Get*`/`Is*` getter on `Timeline` and `TimelineItem` was read three times against the
same objects — target current, another timeline current, target current again — and the
readings diffed. Two things made the result trustworthy:

- **The fixture was built so each true value was non-falsy.** A getter whose real answer is
  already `0` or `False` cannot be *seen* to lie; `0 → 0` proves nothing. The probe reports
  those keys as **vacuous** rather than safe, and the fixture was extended — a take
  selector, a locked track, a clip with a source offset, a marker — until every getter this
  repo reads had a non-falsy true value.
- **The third read** catches a probe that disturbs what it measures.

Result: 3 getters drift, 90 are proven stable — frames, names, source bounds,
`GetClipEnabled`, `GetMarkers`, `GetTrackName`, `GetTrackCount`, `GetMediaPoolItem` among
them. (`GetCurrentTimecode` also moves, but it is the viewer playhead and nothing reads it.)

**A stale handle was ruled out.** A freshly fetched timeline object, and even one fetched
from a freshly fetched project object, still read `False`/`0`. There is no cheap re-fetch:
switching the current timeline is the only route to the real values.

## Decision

`inspect_timeline` reports the three affected fields as **`null`** on any timeline that is
not current, and says so in a `currency` block naming them and how to get them. `null` is
the honest reading — not "zero takes", but "not asked in a state where Resolve will say".

`make_current=true` is the opt-in: it wraps the read in `current_timeline(project, timeline)`,
switching for the read and switching back.

Switching is **not** the default, for a reason the probe turned up: `SetCurrentTimeline`
moves the viewer playhead and does not reliably put it back (`01:00:00:00 → 00:59:59:23`
across a switch-and-restore). A read that silently moves the GUI of whoever is sitting at
the machine is a side effect a *read* has no business having.

Whether a field was withheld is asked of the `Reader`, never inferred from the value.
Resolve returns `None` from `GetTakesCount` for its own unrelated reasons — a clip kind with
no selector — and that has always meant zero. Reading "unknown" out of the value puts the
two back together one line after the reader separated them; doing exactly that broke the
live tier once while this fix was being written.

## Consequences

- An agent surveying several timelines loses three numbers it was previously given wrongly.
  That is the trade: the reply now says which three and how to get them.
- The fakes model the defect behind one opt-in knob (`getters_need_current`), because the
  three getters share one cause. A test that turns it on is saying "this is read the way an
  agent surveying a project reads it".
- **No fake can establish the premise** — the fakes hand back the same object whether or not
  a timeline is current, so left to themselves they would agree with any wrapper. Two live
  tests hold it: one asserts Resolve really does answer falsely and the wrapper turns that
  into `null`, one asserts the switch reads the real flags and restores the timeline.
- Any getter added to `UNKNOWN_OFF_CURRENT` needs the same evidence — read on a non-current
  timeline with a non-falsy true value, then read again while current. The list is a
  measurement, not a rule to reason from.
- Callers that already switch are unaffected: `Reader` defaults to trusting, and the export
  and render paths do their reads inside `current_timeline`.
