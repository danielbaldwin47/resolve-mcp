# ADR 0003 — Stems are fingerprinted, because their path already is a content hash

- **Status**: accepted
- **Date**: 2026-08-05
- **Context**: P1 ([#39](https://github.com/danielbaldwin47/resolve-mcp/issues/39)) — the first job that keys off separated stems rather than off a mix

## Context

The build spec ([#22](https://github.com/danielbaldwin47/resolve-mcp/issues/22), story 26)
says job results are "cached on disk keyed by content hash + parameters", and
`jobs/cache.py` splits that into a rule with two halves: media the director handed over is
fingerprinted (path, size, mtime), because a concert master is tens of gigabytes that would
take minutes to read on every run; audio this server wrote is hashed for real, because it
is the substrate later analysis keys off and a false hit there would attribute one
concert's beats to another.

Drum-fill detection is the first job whose inputs are neither. Its three drum stems were
written by this server, which puts them on the hashing side of the rule — but there are
three of them, each as long as the concert, and they are hashed in the *starter*, whose
entire contract is to return a job id at once (#22, story 25: "heavy compute … returns a
`job_id` immediately … so that the stdio connection never stalls"). Reading three
concert-length WAVs before the first byte of the response is exactly the stall the rule's
fingerprint half exists to avoid.

## Decision

Stems are fingerprinted, not hashed. The reason it is safe here and nowhere else is the
path: `audio/stems.py` writes a separation into

```
<stems_dir>/<slug>-<key[:12]>/{mix,drums}/
```

where `key` is derived from the **content hash of the audio that was separated** plus the
separation parameters. A stem's path therefore already carries the content identity of
everything upstream of it, and size and mtime catch a rewrite in place. Fingerprinting
these files is not a weaker identity than hashing them — it is the same identity, read off
a name instead of off two gigabytes.

`cache.identity(path, written_under)` holds the general rule, so two jobs keying off the
same master agree about what it is; `analysis/fills.py:_key` is the one caller that departs
from it, and says so.

## Consequences

- The fill job's starter returns in the time it takes to stat three files, on a concert or
  on a fixture.
- The exemption is **tied to how stems are named**, not to stems as a category. If a future
  route ever writes stems into a directory whose name is not derived from a content hash,
  this reasoning lapses and that route must hash them. A test that asserts the directory
  layout (`tests/test_stem_separation.py`, and the layout the fill fixture mimics) is what
  keeps the premise honest.
- Stems copied somewhere else by hand and passed as an explicit mapping get fingerprint
  identity without the naming guarantee. That is the same trade the director's own master
  already gets, and the cost of being wrong is one redundant rerun, not a wrong answer.
