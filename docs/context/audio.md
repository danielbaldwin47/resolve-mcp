# `audio/` — concert audio out of Resolve onto disk

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`audio/` — concert audio out of Resolve onto disk: `acquire` (both routes),
`ffmpeg` (per-source-clip route), `riff` (the WAV container itself: PCM,
IEEE float and extensible headers, because stdlib `wave` opens PCM only),
`separator` (python-audio-separator out of process; its torch build probed
before a fresh separation and the device each pass announces read off that
pass's banner, both onto the job record — #202, #188), `stems` (two passes —
mix into four, then the drum stem into the kit — plus an opt-in third,
`split_wind`, splitting `other` into `wind` and `comp`; `comp` is
accompaniment, never a piano stem. A directory is judged pass by pass and
only the passes it owes are run, so turning the third one on costs that pass
alone — but a missing first pass redoes all three, since the later two are cut
from what it wrote, #192. `claimed` is the policy over `lease.claim` — how long
a claim may go quiet, how long a run waits for the separation already under
way, and what the agent is told when it is refused — #217), `wav` (header facts + the one
unreadable-WAV error).
