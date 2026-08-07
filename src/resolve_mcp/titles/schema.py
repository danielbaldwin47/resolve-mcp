"""The titles-file schema v1, served verbatim by ``get_titles_schema``.

The text below is the contract settled by the titling workflow spec (issue #14) and the
title-control research (issue #5). It is data, not documentation: Claude authors
``titles.json`` against exactly this, so the example stays annotated and is served
character-for-character as written here. Editing it changes the contract — do it in a
ticket, not in passing.

Two decisions in it are worth naming here, because they are what make titling
re-runnable rather than merely automatic:

* **Event times are offsets from the song's blue marker, never absolute frames.** The
  marker name is the song key, which is the explicit join between the file and the
  timeline (#14 §1). A rebuild makes a new timeline version; re-marking the songs on it
  is all that is needed for the same titles file to land correctly again. Absolute
  frames would silently point at the wrong music the moment a segment's length changed.
* **Titles are not in the cut file, and the cut file is not read here.** The two files
  together describe a deliverable, and each owns its own tracks — ``build_timeline``
  never touches the Titles track, and ``apply_titles`` never touches V1.
"""

from typing import Final

SCHEMA_VERSION: Final = 1
"""The only titles-file ``schema`` value this server accepts."""

TRACK_NAME: Final = "Titles"
"""The track ``apply_titles`` owns. Everything on it is the server's to clear and re-place."""

KINDS: Final = ("title", "personnel", "custom")
"""What an event is for. ``kind`` picks the template unless ``template`` overrides it."""

ROUTES: Final = ("textplus", "png")
"""How an event is rendered: a Text+ template instance, or a designed PNG card."""

DEFAULT_ROUTE: Final = ROUTES[0]
"""The route an event that names none is placed by — Text+, the cheapest to iterate on."""

ASSET_BIN: Final = "04_Assets/Text"
"""Where a PNG card lands unless the event names a bin: ``04_Assets/Text/<song key>`` (#57)."""

ANNOTATED_EXAMPLE: Final = """\
{
  "schema": 1,                       // supported-version check
  "timeline": "sunset-set v4",       // optional; default = the current timeline

  // GUI-authored Text+ templates, already in the media pool. Identity = clip name
  // + optional bin, resolved to exactly one media-pool clip at apply. Omit the whole
  // block in a file whose events are all PNG.
  "templates": {
    "title":     { "clip": "Song Title", "bin": "Titles/Templates" },
    "personnel": { "clip": "Personnel" }
  },

  "songs": [
    {
      "key": "sunset-boulevard",     // the name of a blue marker on the timeline (§2)
      "events": [
        {
          "id": "t01",               // required, unique across the whole file
          "kind": "title",           // title | personnel | custom
          "route": "textplus",       // optional; textplus | png, default textplus
          "template": "title",       // textplus only; optional, default = kind
          "text": "Sunset Boulevard",     // textplus only; the final string, verbatim
          "in": 240, "out": 720,     // frames from the song's marker, half-open [in, out)
          "fade": { "in": 24, "out": 36 },  // optional; frames of opacity ramp (§3)
          "note": "let four bars play"      // free text; server ignores; feeds the report
        },
        {
          "id": "t02",
          "kind": "personnel",
          "route": "png",            // a designed card, exported to frames (§6)
          "asset": "cards/sunset-boulevard/personnel_%04d.png",  // png only; relative to
                                     // this file. A %0Nd run, or one still image.
          "bin": "04_Assets/Text/sunset-boulevard",  // optional; this is also the default
          "in": 960, "out": 1440,
          "fade": { "in": 24, "out": 24 }   // describes the ramp already in the frames
        }
      ]
    }
  ]
}"""
"""The annotated schema example, verbatim. Comments are ``//`` — strip before parsing."""

_TRACK: Final = """\
## 1. One track, owned outright

`apply_titles` owns the topmost video track named `Titles`, and creates it if the
timeline has none. The name is not configurable: one track is owned outright, and
a tool that could be pointed at any track could clear one it does not own. Every
re-run clears that track completely and re-places from the file, so it always
holds exactly what the file says and nothing else. Put nothing there by hand that
you want to keep. No other track is read or written — the cut on V1 is never
touched, which is what makes "rebuild the cut, re-apply the titles" safe in
either order."""

_ANCHORS: Final = """\
## 2. Songs are anchored to blue markers, not to frames

A song's `key` is the *name* of a blue marker on the target timeline; every
event's `in`/`out` counts frames forward from that marker. So the join between
this file and the timeline is explicit and survives a rebuild: build the new
version, mark the songs on it, re-apply this file unchanged.

Exactly one blue marker must carry each key (T7). A blue marker with no matching
song is reported as a warning, never an error — a set list is often titled a song
at a time."""

_FADES: Final = """\
## 3. Fades never use the clip's fade handles

Resolve's clip-level fade handles are not reachable from the scripting API at
all, so each route fades some other way, and `fade` means the same thing to
both: frames of opacity ramp at the head and at the tail. Omit it for a hard cut
on and off. `fade.in + fade.out` must fit inside the event's duration (T4).

* **Text+** — a BezierSpline on the placed instance's `Opacity1`, keyframed
  0 -> 1 over `fade.in` and 1 -> 0 over `fade.out`. Written per instance, so a
  re-fade is one re-apply. The report says per event whether the written spline
  read back, because a Resolve build that will not answer for an animated input
  is the one thing here no test can check.
* **PNG** — already in the pixels (§6). Nothing is written at apply, and `fade`
  is a record of what the exporter baked. A one-image card is held by freezing
  that image, which no ramp can survive, so a fade on one is a T11 error.

Fade timing is a judgement call, not a default: fast tune roughly 0.5-1 s,
ballad roughly 2-4 s."""

_TIME: Final = """\
## Time

Frames authoritative, half-open `[in, out)` — duration = out - in. Offsets are
from the song's marker, so `in: 0` is the marker frame itself. Seconds are not
accepted here: the fps that would convert them belongs to the timeline, and a
titles file outlives any one version of it."""

_RERUN: Final = """\
## 4. Re-running

`apply_titles` is declarative and idempotent: same file, same timeline, same
result. Nothing is validated against the previous run, and no state is kept
between runs — the timeline *is* the state. Edit the file and re-apply to move,
retime or re-word titles: re-applying costs one clear and one append.

A typo in a title already placed does not need that. `edit_title` writes new
words, or new exposed template params, into that one instance in place and
leaves the rest of the track alone. It edits the timeline and not this file, so
the next apply puts the old wording back — change both when the fix is one to
keep."""

_RULES: Final = """\
## 5. Validation

11 hard errors (T1-T11) block an apply; 2 warnings (W1-W2) never do. A single
error aborts before the Titles track is touched, so a refused apply always
leaves the timeline exactly as it was. Every finding is
`{rule, id, message, fix_hint}` — see the `rules` field of this result."""

_PNG: Final = """\
## 6. The PNG route

A `route: "png"` event carries `asset` instead of `text` and `template`: a path
to a designed card, exported to frames with an alpha channel at the timeline's
resolution and frame rate. Relative paths are relative to the titles file, so a
project folder moves in one piece. Two forms:

* **A `%0Nd` frame run** — `cards/<song>/personnel_%04d.png`. This is the full
  event: the fade-in ramp at the head, the hold in the middle, the fade-out ramp
  at the tail, all baked. Its frame count must equal `out - in` exactly (T11).
  Both directions are refused for the same reason: the fade-out lives in the last
  frames, so trimming a long card cuts it off, and a short one cannot be held out
  to length either — the hold belongs *before* the ramp, and there is no way to
  freeze a frame in the middle of a sequence from the API. So the length is the
  exporter's job, and re-timing an event means re-baking the card. Baking the hold
  on disk is what the titling spec (#14 §6) asks the exporter for.
* **One still image** — `cards/<song>/title.png`. Freeze-extended to whatever
  duration the event asks for, so it retimes freely, but it carries no ramp: its
  fades must be zero (T11).

Cards are consumed, never generated: designing one and baking its ramps happen
outside this server. A designated card that is not on disk is a T10 error, named
before Resolve is touched at all.

The card is imported once per apply into `04_Assets/Text/<song key>`, or into the
event's own `bin`, and a card already in that bin is reused rather than imported
twice. Every imported image gets a one-time out-point write, which is what makes
`endFrame` respected: without it Resolve ignores the requested length and lands
every image at the project's default still duration."""

SCHEMA_DOC: Final = "\n\n".join(
    [
        "# Titles-file schema v1",
        _TRACK,
        _ANCHORS,
        "## Schema\n\n```jsonc\n" + ANNOTATED_EXAMPLE + "\n```",
        _TIME,
        _FADES,
        _RERUN,
        _RULES,
        _PNG,
    ]
)
"""The full schema document: prose contract with the annotated example embedded."""
