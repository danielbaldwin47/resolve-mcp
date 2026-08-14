# Compute device inventory (#202)

Surveyed 2026-08-14 on the live box (Windows 11, RTX 4080 SUPER, ffmpeg
8.1.2 gyan.dev full build). One row per compute path in `src/`, with the
device it runs on and whether a GPU path exists. The rule this inventory
serves: **if it can use CUDA, it should use CUDA — and any CPU fallback
says so in the log and the job record.**

## The table

| Path | Module(s) | Device after #202 | GPU path | Fallback reporting |
| --- | --- | --- | --- | --- |
| Video decode: scene scan | `video/ffmpeg.scan` → `video/scenes` | **NVDEC** via `ffmpeg_hwaccel=auto` | yes — `-hwaccel cuda` | probe logged once; `decode` block in catalog + gist; software retry is a WARNING + `reason` |
| Video decode: occlusion sampling | `video/ffmpeg.sample` → `video/occlusion` | **NVDEC** (same setting) | yes | same `decode` block in catalog + gist |
| Video decode: frame grabs | `video/ffmpeg.grab` → `video/frames` | **NVDEC** (same setting) | yes | `decode` in the tool payload (`null` when every frame came off the cache) |
| Video post-decode filters (scale, select, JPEG encode) | same commands | CPU (ffmpeg filters) | not worth it | n/a — decode dominates; the scale runs on frames already in RAM, which is the shape the numpy consumers need |
| Audio extraction | `audio/ffmpeg`, `analysis/decode` | CPU | none applicable | n/a — audio demux/PCM decode, no video stream decoded |
| Occlusion arithmetic | `video/blocking` (numpy/scipy) | CPU | none wired | n/a — milliseconds per scan; decode dominates |
| DSP analysis (energy, silence, drums, fills, melody, phrases, solos) | `analysis/*` (numpy) | CPU | none wired | n/a — pure numpy, no torch involved |
| Beat grid | `analysis/beats` (beat_this, torch) | **CPU by policy** — see below | exists upstream (CUDA torch) | `device.announce("beat_this")` at inference; `torch` note in the music/structure job results |
| Applause curve | `analysis/applause` (PANNs, torch) | **CPU by policy** — same decision | exists upstream | `device.announce("PANNs")`; same `torch` note |
| Transcription | `analysis/whisper` (faster-whisper/CTranslate2) | **CUDA** (`whisper_device=auto`, runtime shipped by the analysis extra, #128) | yes — already wired | resolved device logged after model load; `auto`→CPU resolve is a WARNING |
| Stem separation | `audio/separator` (external CLI, its own torch) | whatever the PATH install has — **found `2.13.0+cpu` on the live box, 2026-08-14** | yes — CUDA torch in the separator's env | `--env_info` probed before every fresh separation; build in the log + job record; `+cpu` build is a WARNING |
| Rendering | `resolve/render`, `deliver` | Resolve's own GPU pipeline | Resolve's business | out of scope — Resolve manages its own devices |

## ffmpeg hardware decode (`ffmpeg_hwaccel`)

`RESOLVE_MCP_FFMPEG_HWACCEL` = `auto` (default) / `cuda` / `off`.

- `auto` probes `ffmpeg -hwaccels` once per process and passes
  `-hwaccel cuda` when the binary lists it; a box without a card degrades
  to software decode with the reason recorded. The live box lists `cuda`,
  so the default gets NVDEC there.
- `-hwaccel_output_format cuda` is deliberately **not** passed: every
  consumer (scene select, occlusion numpy, JPEG grabs) needs frames in
  system memory, so decoded frames download either way and keeping the
  filters on the CPU side keeps the transfer identical while the decode —
  the dominant term — moves to the card.
- A hardware decode that exits non-zero retries once in software (NVDEC
  refuses codecs it does not know) and the retry is a WARNING plus a
  `reason` in the record. Forcing `cuda` disables the retry: forcing is a
  claim about the box, and a wrong claim should fail loudly.

## The torch decision: beats and applause stay on the CPU

The analysis extra pins the PyPI torch wheel, which on Windows is the CPU
build (`pyproject.toml`), and beat_this is pinned to the commit the corpus
was measured with. This is a **policy, not an accident**: the corpus —
every `[measured — N projects]` claim in `styles/` — was measured on the
CPU build, and a GPU build that produced even slightly different beat
times would change what the style profiles were learned from without
anyone deciding that. Re-measuring the corpus is a human decision
(director's call), not an agent's; until it is made, the CPU build is the
reference implementation and both models announce
"CPU: the corpus policy" at inference rather than staying silent about it.
What flipping the policy would take: install CUDA torch, re-run the corpus
pass (`docs/agents/style-layer.md`), and diff the beat grids — if they
match to tolerance, the profiles survive; if not, they were learned from
numbers that no longer exist.

Cost of the policy today: beats + applause over a full concert are
minutes-scale on this box's CPU, not the hours-scale the separator's bug
was — the wheel decision is cheap where it sits and expensive only if
copied to the separator, which is exactly what G10 was.

## The separator: GPU-capable, and currently mis-installed on the live box

`config.audio_separator` names a binary and PATH picks the install. The
2026-08-14 probe of the live box's PATH `audio-separator` reported
**PyTorch 2.13.0+cpu** (ONNX Runtime GPU 1.28.0 alongside): the demucs
passes (`htdemucs_ft`, the drum and wind models) run torch, so every
separation on this box is currently CPU-bound — G10's class of bug, live.
The server now probes `--env_info` before each fresh separation, logs the
build, carries it in the job record, and warns on `+cpu`. The fix — CUDA
torch installed into whatever environment owns the PATH
`audio-separator`, or `RESOLVE_MCP_AUDIO_SEPARATOR` pointed at one that
has it — is an install action on the box, recorded on ticket #202.
