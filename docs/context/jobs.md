# `jobs/` — background compute

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`jobs/` — `cache` (hash-keyed results; `audio_identity` is the content hash
wherever the file sits, read off a `known_hash` note remembered against a
stat, except under `audio_dir` where it is always read for real;
`fingerprint` is path+size+mtime and stays the identity for video sources
and stems — ADR 0007, ADR 0003), `runner` (start heavy work without
stalling stdio), `lifecycle` (the job states and
`verdict(record, now, alive)` — whether anything is still running a job and
the sentence for it if not, decided from the record alone with liveness
injected, so the truth table is testable in memory; the pid/session/silence
reading inside it is `lease.liveness`, shared with the stems claim, and the
process identity `SESSION` is `lease`'s too — #217), `store` (one JSON record
per job on disk; `load` is read file → verdict → maybe write the failure
back), `detached` (hand a job to a process that outlives this one — flags,
command, environment), `worker` (that process's entry point: `python -m
resolve_mcp.jobs.worker <job-id>`). A worker returning `runner.Detached`
instead of a result moves the rest of its job into that process;
`separate_stems` does, once the audio is acquired, so a half-hour separation
survives the server exiting. A detached record is judged by its pid rather
than by its session, and only the worker writes it — the launcher's reading
of the worker pid goes to a `<job-id>.launcher` note beside the record,
folded in by readers only while the record has no pid of its own, so a
launcher can never overwrite a result.
