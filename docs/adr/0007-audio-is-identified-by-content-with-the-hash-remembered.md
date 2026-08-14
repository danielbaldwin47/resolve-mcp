# ADR 0007 — Audio is identified by content, with the hash remembered against a stat

- **Status**: accepted
- **Date**: 2026-08-14
- **Context**: [#193](https://github.com/danielbaldwin47/resolve-mcp/issues/193) — a staged copy of the director's master missed the beats cache during the gauntlet

## Context

`jobs/cache.py` originally split identity two ways: audio this server wrote under
`audio_dir` was hashed, and everything else — the director's master included — was
*fingerprinted* by path, size and mtime. The reason was the starter contract (#22, story
25): a starter returns a job id at once, and reading tens of gigabytes before the first
byte of the response is exactly the stall a fingerprint avoids.

The cost of that split showed up the moment the same audio existed twice. During the
gauntlet an acquisition staged a copy of the director's master into the cache directory;
the copy hashed, the original fingerprinted, so the two identities could not agree and the
beat model ran a second time over byte-identical audio. The same happens for every
workflow that stages or copies audio — renders, per-song excerpts, a delivered mix — and
each copy repays the full analysis cost, which is minutes of model time, not seconds of
I/O.

## Decision

`cache.identity` is the content hash of the file, wherever the file sits.

The starter is protected by a **memo instead of a weaker identity**: `cache.known_hash`
writes one note per file state into `config.identity_dir`, keyed on the fingerprint that
was already cheap to take. The first sight of a given path/size/mtime costs one read; every
later call for that unchanged file costs a `stat`.

A note that cannot be read, cannot be written, or whose recorded size and mtime no longer
match the file is ignored and the bytes are read again. What a note **cannot** do is be more
trusting than the fingerprint guarding it, and that bounds where it is allowed to apply. A
same-size rewrite in place, on a filesystem whose mtime is granular to two seconds — exFAT
and SMB, which is what external concert drives are formatted as — is one file state by a
fingerprint's reading and different audio in fact.

For the director's master that is not a new risk: it is precisely the risk the old rule ran,
since a master's whole identity used to be that same fingerprint with no hash behind it at
all. Under `audio_dir` it *would* be new, on the substrate every later job keys off, where a
false hit attributes one concert's beats to another. So **audio this server wrote is hashed
for real on every call and never read off a note** — `cache.audio_identity` branches there,
and these are files the server sized itself and can afford to read. The branch is about how
the same answer is reached, never about what the answer is: identity is the content hash on
both sides of it, which is what makes a copy hit its original.

`fingerprint` stays, and stays the identity for **video** sources (`video/scenes.py`,
`video/frames.py`, `video/occlusion.py`) and for stems (ADR 0003). Those are the files the
old reasoning was really about: a camera master is tens of gigabytes, and unlike an audio
master the job keying off it never reads it end to end anyway.

## Consequences

- A copy of already-analysed audio hits the cache of its original, which is the whole point
  of #193.
- An audio master is read once per file state. The first analysis of a fresh master pays a
  full sequential read in the starter — seconds for a concert WAV, against the minutes of
  beat, structure and phrase work it is about to save — and no run after it pays anything.
  If a route ever needs audio identity for a file it will *not* then analyse, that trade
  stops holding and the route should say so.
- Audio under `audio_dir` costs a full read on every starter that keys off it. That is
  unchanged — it is what the old rule already did to acquired audio — so nothing regresses,
  but it is also the reason the memo is not simply applied everywhere.
- Entries already on disk need no migration pass. Identity was a `{"sha256"}` dict for
  hashed audio and a `{"path", "size", "mtime_ns"}` dict for everything else, and the dict
  goes into the key verbatim: acquired-audio entries keep hitting under exactly the same
  key, and every path-keyed entry now produces a key nothing asks for. A stale hit is not
  reachable — the two shapes cannot collide — so the dead entries are left to be swept with
  the rest of the cache directory rather than hunted down.
- The identity notes are cache, in the cache directory, and a user who deletes them gets
  rereads rather than errors.
