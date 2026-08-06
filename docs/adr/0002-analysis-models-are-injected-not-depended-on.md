# ADR 0002 — Analysis models are injected at a seam, not depended on

- **Status**: accepted
- **Date**: 2026-08-05
- **Context**: P1 ([#37](https://github.com/danielbaldwin47/resolve-mcp/issues/37)) — the first analysis worker that wants a model

## Context

The build spec ([#22](https://github.com/danielbaldwin47/resolve-mcp/issues/22)) names the
models: beat_this for beats and downbeats, PANNs for applause, python-audio-separator for
stems. Each is a torch stack of a gigabyte or more, installed from a git URL rather than a
release on PyPI, and each wants a GPU to be worth running.

Two things follow that pull in opposite directions. The workers are the point of the
analysis pillar, so they have to be built and tested. But #22's testing decision is
explicit that **model quality is not under test** — that was settled by the research
tickets — and no seam in this repo can check whether beat_this heard the right beat
anyway. Meanwhile CI runs on a Linux runner with no GPU, and a hard torch dependency would
make `uv sync` a multi-gigabyte download for a repo whose default test run touches no model
at all.

## Decision

A model is reached through an injectable callable, imported when a job runs and never at
module import:

```python
Detector = Callable[[Path], BeatGrid]

def detect(path: Path, detector: Detector | None = None) -> BeatGrid: ...
```

- The **default** detector imports the model inside itself. A missing model is an
  `analysis_dependency_missing` failure naming the install command, not an ImportError at
  startup and not a crashed server.
- The model package is **not a project dependency**. numpy and scipy are — they are the
  substrate every worker's own arithmetic runs on, they are small, and they carry no
  hardware assumption.
- **Everything downstream of the model is ordinary code under test**: bar numbering, tempo
  and meter stats, what lands on disk, what comes back inline, cache behaviour. Tests
  inject a grid and assert on all of it.

## Consequences

- The fake tier verifies every decision the worker makes on fixture audio of a few seconds,
  with no torch installed and no GPU. CI stays a normal Python test run.
- What is **not** verified anywhere is that the model is installed on the machine the
  server runs on, and that it hears what a musician would. The first is a live concern like
  the interpreter in ADR 0001 — it fails loudly with a fix, rather than silently. The
  second is not a testable claim; it was settled by measurement in the research tickets and
  is re-checked by ear on real concerts.
- Later analysis tickets (#38 drum fills, #39 solos, #40 applause) inherit the shape: one
  seam per model, the same missing-dependency error, the same split between what the model
  says and what the worker does with it.
- The arithmetic that *is* a published standard gets held to it rather than to a fixture:
  the BS.1770 loudness filter is checked against the coefficients the standard tabulates,
  so "the numbers look plausible" is never the assertion.
