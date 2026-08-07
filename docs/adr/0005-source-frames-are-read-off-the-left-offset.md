# ADR 0005 — A shot's source frames are read off its left offset, not its source start

- **Status**: accepted
- **Date**: 2026-08-07
- **Context**: [#120](https://github.com/danielbaldwin47/resolve-mcp/issues/120), found during
  the #119 Section B clean-baseline run against Resolve Studio 21.0.3.7

## Context

`swap_take` reported that it had put a shot on source frame 108 and the timeline read back
107. The ticket opened on the theory that `AddTake` reads `endFrame` inclusive where
`AppendToTimeline` reads it half-open — an off-by-one on the *write*.

It is not. `AddTake` stores exactly the half-open range it is given, and Resolve plays it:
a take sent `(108, 156)` reads back `GetTakeByIndex → {startFrame: 108, endFrame: 156}` and
`GetLeftOffset → 108`. The write was never wrong. **`GetSourceStartFrame` misreports.**

### The sweep

One shot on a 23.976 timeline, the same range placed at consecutive source starts, each read
back through both getters:

| requested | 100 | 101 | 102 | 103 | 104 | 105 | 106 | 107 | 108 | 109 | 110 | 111 | 112 | 113 | 114 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `GetLeftOffset` | 100 | 101 | 102 | 103 | 104 | 105 | 106 | 107 | 108 | 109 | 110 | 111 | 112 | 113 | 114 |
| `GetSourceStartFrame` | 100 | 101 | 102 | **102** | **103** | 105 | 106 | **106** | **107** | **108** | 110 | 111 | **111** | **112** | 114 |

Three things make the reading trustworthy:

- **It is deterministic.** Re-running the sweep reproduced the sequence value for value.
- **It is bounded.** Extending the sweep to source frames 0, 1, 2, 3, 47, 48, 72, 500, 1000,
  5000, 20000, 40000, 60000 and 69001 — the far end of a 69952-frame clip — the slip is
  always 0 or −1. It never accumulates and never runs the other way.
- **Takes have nothing to do with it.** Plain shots appended at the same source starts, with
  no take selector anywhere on the timeline, drift identically. Any shot whose source in
  point lands on a bad frame is misreported, swapped or not.

`GetLeftOffset` was exact in all 41 rows.

The live test passed before only by coincidence: its shots start at source 0, 48 and 72,
which are fixed points of the drift. The alternate at 108 is not.

### The end getter is exclusive, and the wrapper added one to it

Separately, `GetSourceEndFrame` returns **one past** the last frame — a shot cut from source
0..47 reports 48, one cut from 100 over 48 frames reports 148. The wrapper read it as the
last frame and added one, so `source.out` overshot by a frame wherever the getter was not
also losing one. It carries the same ±1 slip, and it is not even stable across contexts: the
same source range read 149 in one timeline and 150 in another.

Note that the #84 sweep in [ADR 0004](0004-editor-state-getters-only-answer-for-the-current-timeline.md)
lists source bounds among the getters "proven stable". That was true on the axis it measured
— these getters do not drift with which timeline is current. Stable is not the same as
accurate.

## Decision

`source_bounds` reads the start off **`GetLeftOffset`**, and falls back to
`GetSourceStartFrame` only when the two disagree by more than the measured slip of one
frame.

The fallback is not hypothetical. A still is where the left offset stops being a source
frame: media that is source frame 27 alone reports `GetSourceStartFrame → 27` against
`GetLeftOffset → 86313`, which is the timeline's clock, not the media's. A disagreement that
large means the two getters are no longer describing the same thing, and the absolute one —
lossy as it is — is the only one still answering the question. The routes are never mixed:
an in point counted from the media start against an out point in absolute source frames is a
span that means nothing.

The end comes from the **record duration**, which is exact, rather than from the lossy end
getter. A retime is the one shot whose source span is not its record duration, so the getter
is still read to notice one: a span differing from the duration by more than the slip is a
real retime and is believed.

## Consequences

- `inspect_timeline` reports `source.in`, `source.out` and the `sync_offset` derived from
  them correctly for any shot, not just ones that happen to start on a fixed point.
  `correlate_timeline` reads through the same function and is fixed with it.
- **The fakes were carrying the bug.** `FakeTimelineItem.GetSourceEndFrame` returned
  `source_start + duration - 1` and its docstring called the value inclusive — the same
  belief the wrapper held. Fake and wrapper agreed with each other and neither agreed with
  Resolve, which is why the fake tier was green through all of it. It now returns the
  exclusive end, and a test encodes the slip by setting `source_start` and `left_offset` a
  frame apart.
- **The retime branch has no seam.** `SetProperty("Speed", 50.0)` returns `False` on
  21.0.3.7, so a retimed shot cannot be created through the scripting API at all; only a
  human retiming in the GUI produces one. The branch is held by a fake alone, and the fake's
  numbers are reasoned from the measured exclusivity rather than measured directly.
- The slip's arithmetic is unexplained. It is not a cumulative timecode drift — it does not
  grow over 69001 frames — and pinning the exact function was not needed to stop trusting the
  getter. If a future build changes the bound, `SOURCE_SLIP` is the one number to re-measure.
