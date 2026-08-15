# ADR 0008 — Beats and applause infer on CUDA, and the device is named rather than defaulted

- **Status**: accepted
- **Date**: 2026-08-15
- **Context**: [#245](https://github.com/danielbaldwin47/resolve-mcp/issues/245) — flip the torch build and re-measure the corpus. Reverses the "CPU by corpus policy" decision recorded in `docs/reference/compute-device-inventory.md` under #202, on the director's call on [#244](https://github.com/danielbaldwin47/resolve-mcp/pull/244).

## Context

Two analysis paths run torch: the beat grid (beat_this) and the applause curve
(PANNs). Until now both ran on the CPU, and deliberately.

The mechanism was the wheel. The `analysis` extra pinned torch off PyPI, and
PyPI ships one wheel per platform: the Linux wheel for torch 2.13 depends on
`nvidia-*-cu13` and is the CUDA build, while the Windows wheel is 122 MB with
no CUDA runtime in it at all. The live box is Windows, so the extra installed
a `+cpu` torch there without anyone choosing it.

That accident was then adopted as policy, for a real reason. Every
`[measured — N projects]` claim in `styles/` was derived from beat grids the
CPU build produced. A GPU build that placed beats even slightly differently
would change what the style profiles were learned from, silently — the
profiles would keep their `[measured]` tags while the numbers behind them no
longer existed. So #202 recorded the CPU build as the reference
implementation, had both models announce "CPU: the corpus policy" at
inference, and named re-measuring the corpus as a director's decision rather
than an agent's.

The cost of holding it was low — beats and applause over a full concert are
minutes on this box's CPU, not the hours the separator's CPU bug cost (G10,
#202). But it left one row of the compute inventory reading CPU where a GPU
path existed, which is precisely the shape the GPU-first rule (#244) exists to
forbid, and it made "CPU here is fine" a sentence this repo could say. That is
the sentence G10 hid behind.

## Decision

**Beats and applause infer on CUDA. A `+cpu` torch in the venv is a broken
install, not a policy.**

Three parts, and the third is the one that is easy to miss:

1. **The build moves, the pins do not.** `[tool.uv.sources]` maps torch,
   torchaudio and torchcodec to `https://download.pytorch.org/whl/cu130` for
   `sys_platform == 'win32'` only. The version pins (torch 2.13, torchaudio
   2.11, torchcodec 0.15) and beat_this's commit pin are untouched, so the
   corpus diff has exactly one variable in it. Linux and CI resolve exactly as
   before — their PyPI wheel is already the CUDA one, and a universal lock
   should not carry Windows-only artifacts.

2. **`announce()` treats `+cpu` as a fault.** The "CPU: the corpus policy"
   INFO line is gone; a `+cpu` build now warns and names the command that
   replaces it, the same shape as the separator's `+cpu` warning. The
   CUDA-build-that-sees-no-card case keeps its own separate warning, because
   its fix is a driver, not a wheel.

3. **The device is passed to the models, not left to them.** Neither model
   follows torch. `beat_this.inference.File2Beats` defaults to `device="cpu"`
   whatever wheel is installed; `panns_inference.SoundEventDetection` defaults
   to `"cuda"` and falls back to the CPU without saying so. Under the old
   policy this did not matter — everything was CPU anyway — but under the new
   one, a job record reading `"device": "cuda"` could sit over a grid the CPU
   computed, and the fallback the record exists to expose would be the thing
   it hid. So both sites call `device.inference_device()` and hand the result
   in.

**Flipping the compute path and re-confirming the corpus are separate.** The
flip lands now; the corpus diff — every project's beat grid and applause curve
recomputed on CUDA and diffed against the stored CPU results, against a
tolerance of one frame at the timeline's fps — runs on the director's box,
because the projects live nowhere else, and is recorded on #245. A new job
today should use the card whatever that diff says about claims made a month
ago.

## Consequences

- The live box's beat and applause jobs carry `"device": "cuda"` in the
  `torch` note and log "inference on CUDA". A CPU reading on that box is now
  actionable rather than expected.
- A box with no NVIDIA card still installs, resolves and imports: the cu130
  wheels carry their own runtime, `cuda.is_available()` is simply `False`, and
  the models get `"cpu"` under a WARNING. ADR 0002 still holds — the models
  are injected, the fake tier never loads torch, and no fake-tier test can see
  a CUDA install break.
- **The seam does not cover the attach.** What is testable at the fake tier is
  the decision: which device string the sites ask for, and which branch
  `announce()` takes. That the cu130 wheels actually install and see the card
  is live smoke, and the corpus diff is the human's. Treat a green fake tier
  as proof of the decisions only.
- Moving any of the three pins now means checking the cu130 index for all
  three first. torchaudio 2.11.0 is its last release, so it is the ceiling
  that will bind first.
- The old policy's protection is gone: nothing now stops a torch upgrade from
  moving beat times under the profiles. The corpus diff is what replaces it,
  and it has to be re-run when the model or its pin moves — which is why
  beat_this's commit pin is called out separately above.
