# Compute device inventory (#202)

Surveyed 2026-08-14 on the live box (Windows 11, RTX 4080 SUPER, ffmpeg
8.1.2 gyan.dev full build). One row per compute path in `src/`, with the
device it runs on and whether a GPU path exists. The rule this inventory
serves: **if it can use CUDA, it should use CUDA — and any CPU fallback
says so in the log and the job record.**

## The table

| Path | Module(s) | Device after #202 | GPU path | Fallback reporting |
| --- | --- | --- | --- | --- |
| Video decode: scene scan | `video/ffmpeg.scan` → `video/scenes` | **NVDEC where the profile allows** via `ffmpeg_hwaccel=auto` — this box's 4:2:2 sources decode in software, recorded (see the measurements below) | yes — `-hwaccel cuda` | probe logged once; `decode` block in catalog + gist; software retry and ffmpeg's internal fallback are each a WARNING + `reason` |
| Video decode: occlusion sampling | `video/ffmpeg.sample` → `video/occlusion` | same as the scene scan | yes | same `decode` block in catalog + gist |
| Video decode: frame grabs | `video/ffmpeg.grab` → `video/frames` | same as the scene scan | yes | `decode` in the tool payload (`null` when every frame came off the cache) |
| Video post-decode filters (scale, select, JPEG encode) | same commands | CPU (ffmpeg filters) | not worth it | n/a — decode dominates; the scale runs on frames already in RAM, which is the shape the numpy consumers need |
| Audio extraction | `audio/ffmpeg`, `analysis/decode` | CPU | none applicable | n/a — audio demux/PCM decode, no video stream decoded |
| Occlusion arithmetic | `video/blocking` (numpy/scipy) | CPU | none wired | n/a — milliseconds per scan; decode dominates |
| DSP analysis (energy, silence, drums, fills, melody, phrases, solos, correlate) | `analysis/*` (numpy) | CPU | none wired | n/a — pure numpy, no torch involved |
| Beat grid | `analysis/beats` (beat_this, torch) | **CPU by policy** — see below | exists upstream (CUDA torch) | `device.announce("beat_this")` at inference; `torch` note in the music/structure job results |
| Applause curve | `analysis/applause` (PANNs, torch) | **CPU by policy** — same decision | exists upstream | `device.announce("PANNs")`; same `torch` note |
| Transcription | `analysis/whisper` (faster-whisper/CTranslate2) | **CUDA** (`whisper_device=auto`, runtime shipped by the analysis extra, #128) | yes — already wired | resolved device logged after model load; `auto`→CPU resolve is a WARNING |
| Stem separation | `audio/separator` (external CLI, its own torch) | whatever the PATH install has — **found `2.13.0+cpu` on the live box, 2026-08-14** | yes — CUDA torch in the separator's env | `--env_info` probed before every fresh separation; build in the log + job record; `+cpu` build **refuses the job** (opt into the CPU run with `RESOLVE_MCP_SEPARATOR_ALLOW_CPU=1`, then a WARNING); each pass's own device read off its banner into `separator.device` (#188), CPU under a GPU build is a WARNING |
| Rendering | `resolve/render`, `deliver` | Resolve's own GPU pipeline | Resolve's business | out of scope — Resolve manages its own devices |

Deliberately out of scope: the gauntlet A/B pack's decodes
(`gauntlet/tools/ab_pack.py`) run bare software ffmpeg — a dev-only
evaluation tool, not a server compute path, and its runs are attended.
`video/framing.py` (#184) is not on this branch; when it lands it takes
its own row here.

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
- **ffmpeg's own internal fallback is detected off stderr.** When NVDEC
  lacks the stream's codec profile, ffmpeg prints
  `Failed setup for format cuda`, decodes in software and exits 0 — so the
  exit-code retry never fires and the record would have claimed a decode
  the card never did. `_decoded` scans stderr for that line and rewrites
  the record to `cpu` with the reason, at a WARNING; the decode commands
  hold their log level at `warning` or louder so the line stays visible.
  This fires on a forced `cuda` too — the frames arrived, so failing would
  discard good work; there, the loudness *is* the record.

### Measured on the live box, 2026-08-14 (AC 3)

Full-file decode to a null sink (no encode, no scale), second-of-two runs
so the OS file cache is warm both ways. RTX 4080 SUPER (Ada), ffmpeg 8.1.2:

| Stream | Software | `-hwaccel cuda` | Verdict |
| --- | --- | --- | --- |
| 4K HEVC 4:2:2 10-bit — real A7IV concert clip, 1.1 GB / 85 s | 17.1 s | 17.3 s | **NVDEC cannot engage**: Ada has no 4:2:2 decode (Blackwell adds it). ffmpeg fell back internally; wall clock identical; the record now says `cpu` with the reason |
| 1080p H.264 4:2:0 High — real screen recording, 8.5 min | 10.4 s | 28.6 s | NVDEC engages and **loses 2.8×**: one decode engine (~530 fps) against a many-core software decode (~1460 fps), plus the PCIe download |
| 4K HEVC 4:2:0 — generated, 60 s @ 60 Mbps | 13.0 s | 5.5 s | NVDEC wins 2.35× — the profile the card is built for |

Why `auto` stays the default despite the mixed table: the box's real
concert footage is all 4:2:2 (FX6 XAVC Intra H.264 4:2:2, A7IV H.265
4:2:2 10-bit), where the flag costs nothing and the fallback is recorded;
4K 4:2:0 — phone footage, most delivered renders — is the win case. The
losing case is long 1080p H.264, rare in a 4K pipeline — a box that works
mostly on those should set `RESOLVE_MCP_FFMPEG_HWACCEL=off`.

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
build, carries it in the job record, and refuses a `+cpu` build (a
WARNING only under the `RESOLVE_MCP_SEPARATOR_ALLOW_CPU=1` opt-in). The fix — CUDA
torch installed into whatever environment owns the PATH
`audio-separator`, or `RESOLVE_MCP_AUDIO_SEPARATOR` pointed at one that
has it — is an install action on the box. Still `+cpu` on 2026-08-15 (a
live run was on the CPU as this was written); the exact command is in
CLAUDE.md, "Compute device". Since then a `+cpu` build refuses the job
(`RESOLVE_MCP_SEPARATOR_ALLOW_CPU`, README) and the live separator test
fails on a CPU device, so the state cannot go unnoticed again.

The build says what the install *can* do; it does not say what a run
*did*. Each pass announces its own device in its opening banner, and that
line is read back into `separator.device` on the job record — whatever the
banner named, lowercased (`cuda`, `cuda:0`, `mps`, `cpu`), or `unknown`
where it named nothing (#188). A reader after the GPU wants "not `cpu`",
not `== "cuda"`: the device is a reading, not an enum. The last pass's
reading is the one recorded, because a run that reached the card once and
then could not is a CPU run. A CPU device under a GPU-capable build is the
fallback G10 hid behind "it was slow": it is a WARNING in the log at the
pass that announced it, and a `warning` on the record — except under a
`+cpu` build, which only gets this far on the opt-in box, where the
build's own warning already carries the same news with the fix. A
build the probe could not read is not that case: its warning says whether
this ran on the GPU is unknown, which a CPU reading has just answered.
