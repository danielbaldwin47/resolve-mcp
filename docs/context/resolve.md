# `resolve/` — connection management + thin scripting-API wrappers

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`resolve/` — connection management + thin scripting-API wrappers: `apply`
(titles file → owned track), `build` (materialise cut file), `connection`
(**the seam**: lazy singleton, probe, one auto reconnect), `cut` (cut-file
contract), `fusion` (Text+ node, text, opacity fade spline), `interchange`
(timeline export/import), `loader` (import DaVinciResolveScript +
direct-attach), `markers` (read/write, review-loop transport), `media` (the
six media operations: import, list, inspect, metadata, organize, relink —
all of them callers of `pool`), `pool` (**the media pool adapter**: reaching
the pool, bin addressing, clip lookup, clip reading, frame bounds, offline
and still handling — what `cut`, `build`, `apply`, `titles`, `audio/acquire`
and `video/source` consume; import it from here, never through `media`),
`mix` (where the master
mix sits under a timeline — the one axis a rebuild does not move; read by
`build`'s marker carry and by `analysis/correlate`), `render` (render
queue), `camera_sidecar` (camera model off the card's own XML, for media
Resolve reports no camera metadata for — #94; not an **angle sidecar**),
`scripting` (`run_python` with handles pre-bound), `session`
(session/project wrappers), `settings` (the timeline settings the server
*writes*, through the string-typed `GetSetting`/`SetSetting` pair: resolution,
which needs `useCustomSettings` first and is judged by the read-back, never by
the return value — #187), `tail` (**the tail's round trip**: the export/import
`build` takes when a tail has a transition to cut in — a hard out that fades
nothing builds directly — because the scripting API cannot cut a transition at
all; the document edit itself is `cut/otio`. Takes one `Staging` — the project,
pool, timeline and name the shots are on until the import lands — and returns a
`Landed`: the imported cut plus `release`/`refuse`, so the staging timeline
outlives the import until the caller has read the shots back on it, #221),
`takes`
(take selectors + in-place swap), `timeline` (timeline read wrappers),
`titles` (titles file against a project + dry run).
