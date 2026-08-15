"""Running python-audio-separator, without loading it into this process.

It brings torch and a CUDA runtime with it. Importing that here would cost seconds of
startup on every attach, hold GPU memory for a whole session, and put a model load between
the agent and a Resolve call — so it runs as a subprocess through the CLI it ships, and the
server never imports it at all. That also keeps it out of ``pyproject.toml``: the models
live in whatever environment the director installed them into, and a machine with no GPU
runs every test in this repo.

The shape is the one ``ffmpeg`` already uses: the subprocess call is a parameter
(``runner``), so command construction, progress parsing, the missing-binary case and every
refusal are testable with no models and no GPU present.

Two things here are decisions rather than plumbing:

* **Output is read line by line, not collected at the end.** A concert pass runs for
  minutes and the percentages it prints are the only progress that exists; waiting for the
  process to exit would make the job silent for the whole run.

* **Stems are matched by the label the separator parenthesises, not by predicting
  filenames.** It writes ``<input>_(Drums)_<model>.wav``, and the input to the second pass
  is already a labelled file — so the *last* parenthesised label is this file's own stem.
  Reading the label back is what makes a model that renames its outputs a clear failure
  ("produced no toms stem") instead of a silently missing file.
"""

from __future__ import annotations

import re
import subprocess
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import SeparatorUnavailableError, StemSeparationError
from ..logging_config import get_logger

log = get_logger("audio")

OUTPUT_FORMAT = "WAV"
OUTPUT_TAIL = 200
"""Lines of the separator's own output kept for a failure message.

Deep enough that a traceback survives what is printed under it. A pass prints a progress bar
several times a second, so the forty this used to keep were forty redraws of the bar on any
failure the model took a moment to die of — the account of a pass that died halfway through
writing its output was the one thing worth keeping and the first thing pushed out (#192).
Matched to ``store.WORKER_TAIL_LINES``, which is the same salvage one process further out.
"""

LABEL = re.compile(r"\(([^()]+)\)")
PERCENT = re.compile(r"(\d{1,3})\s*%")
TORCH_BUILD = re.compile(r"PyTorch Version:\s*(\S+)", re.IGNORECASE)
"""What ``--env_info`` prints for its torch build, e.g. ``PyTorch Version: 2.13.0+cpu``."""
DOWNLOAD = re.compile(r"download", re.IGNORECASE)
"""A first run fetches its model and prints a bar for that too. It is not the separation."""

DEVICE_SET = re.compile(r"setting\s+(?:the\s+)?torch\s+device\s+to\s+(\S+)", re.IGNORECASE)
"""The line a pass opens with: ``CUDA is available in Torch, setting Torch device to CUDA``."""
DEVICE_NAMED = re.compile(r"torch\s+device\s*[:=]\s*(\S+)", re.IGNORECASE)
"""The other shape the same fact is printed in, ``Torch device: cuda:0``."""
CPU_MODE = re.compile(r"running\s+in\s+CPU\s+mode", re.IGNORECASE)
"""What it says instead when it configured no acceleration at all — G10's own line."""

CPU_DEVICE = "cpu"
UNKNOWN_DEVICE = "unknown"
"""A banner that named no device. Not a failure: half an hour of GPU is not worth a
wording change."""

FULL = 100.0


Lines = Callable[[str], None]
Runner = Callable[[Sequence[str], Lines], int]
"""Run the argv, hand every output line to the sink, and return the exit code.

Unlike ffmpeg's runner there is nothing else to report: everything the separator says
arrives through the sink as it says it, so the caller already holds the output by the time
the process exits.
"""

Fraction = Callable[[float], None]

Device = Callable[[str], None]
"""Told which device a pass announced, once, as the pass announces it."""


def command(executable: str, model: str, source: Path | str, out_dir: Path | str) -> list[str]:
    """The audio-separator invocation, as a list — never a shell string."""
    return [
        executable,
        str(source),
        "--model_filename",
        model,
        "--output_dir",
        str(out_dir),
        "--output_format",
        OUTPUT_FORMAT,
    ]


def label_of(path: Path | str) -> str | None:
    """The stem this file holds, read off the label the separator wrote into its name."""
    found = LABEL.findall(Path(path).stem)
    return found[-1].strip().lower() if found else None


def device_of(line: str) -> str | None:
    """The device this line of a pass's banner names — ``None`` for every other line.

    Read off the pass's own output rather than off ``--env_info``, because the probe answers
    for the *install* and this answers for the *run*: a CUDA build that cannot reach the card
    falls back to the CPU and says so here, in a line that used to scroll past between two
    progress bars. That fallback, unseen, is the whole of G10 (#188).
    """
    if CPU_MODE.search(line):
        return CPU_DEVICE
    found = DEVICE_SET.search(line) or DEVICE_NAMED.search(line)
    if found is None:
        return None
    return found.group(1).strip().strip(".,;:'\"").lower() or None


def collect(out_dir: Path | str) -> dict[str, Path]:
    """Every labelled stem in a directory, keyed by its label."""
    found: dict[str, Path] = {}
    for one in sorted(Path(out_dir).glob("*.wav")):
        label = label_of(one)
        if label is not None:
            found[label] = one
    return found


def environment_command(executable: str) -> list[str]:
    """Ask the separator CLI what it runs on. Prints one fact a line, torch build included."""
    return [executable, "--env_info"]


def environment(
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Which torch build the resolved separator runs on — G10's bug class, made loud (#202).

    ``config.audio_separator`` names a binary, and PATH decides which install — and which
    torch — that is. A CPU-build torch turns a forty-minute separation into a day's, and the
    only symptom used to be that it was slow; here the build goes into the log and the job
    record, and a ``+cpu`` build is a warning. A separator too old to answer ``--env_info``
    still separates: the report says the build is unknown rather than failing the job.
    """
    config = config or get_config()
    lines: list[str] = []
    try:
        returncode = (runner or run)(environment_command(config.audio_separator), lines.append)
    except FileNotFoundError as exc:
        raise SeparatorUnavailableError(
            cause=f"No audio-separator at {config.audio_separator!r}.",
            detail={"executable": config.audio_separator},
        ) from exc

    found = TORCH_BUILD.search("\n".join(lines))
    report: dict[str, Any] = {
        "executable": config.audio_separator,
        "torch": found.group(1) if found else None,
    }
    if returncode != 0:
        log.info("audio-separator --env_info exited %d; reading what it printed", returncode)
    # A parsed build is believed even off a non-zero exit — the +cpu warning matters most
    # on exactly the runs where the CLI also grumbled about something else.
    if found is None:
        report["warning"] = (
            "The separator did not report its torch build (--env_info), so whether this "
            "separation runs on the GPU is unknown."
        )
        log.warning("%s", report["warning"])
    elif "+cpu" in report["torch"]:
        report["warning"] = (
            f"The separator's torch is the CPU build ({report['torch']}): separations run "
            "on the CPU at a fraction of GPU speed. Install CUDA torch into the "
            "environment that owns the audio-separator on PATH, or point "
            "RESOLVE_MCP_AUDIO_SEPARATOR at one that has it."
        )
        log.warning("%s", report["warning"])
    else:
        log.info("audio-separator torch build: %s", report["torch"])
    return report


def record_device(report: dict[str, Any], readings: Sequence[str]) -> dict[str, Any]:
    """Fold what the passes announced into the environment report, as ``device`` (#188).

    The *last* reading wins: each pass is its own process and answers for itself, so a run
    whose second pass could not reach the card ran on the CPU — reporting the first pass's
    ``cuda`` would hide the fallback this record exists to show. No reading at all is
    ``unknown``, never an error.

    A CPU device earns a warning only where nothing has warned already: a ``+cpu`` build is
    the same news with the fix attached, and replacing that message would cost the reader the
    one line that says what to do.
    """
    report["device"] = readings[-1] if readings else UNKNOWN_DEVICE
    if report["device"] == CPU_DEVICE and "warning" not in report:
        report["warning"] = (
            "The separator ran on the CPU although its torch build "
            f"({report.get('torch')}) can use a GPU: separations run at a fraction of GPU "
            "speed. Check that the GPU is visible to the environment that owns the "
            "audio-separator on PATH (drivers, CUDA runtime, another process holding the card)."
        )
        log.warning("%s", report["warning"])
    return report


def separate(
    source: Path | str,
    out_dir: Path | str,
    model: str,
    wanted: Sequence[str],
    progress: Fraction | None = None,
    runner: Runner | None = None,
    config: Config | None = None,
    on_device: Device | None = None,
) -> dict[str, Path]:
    """Run one pass, and return the stems it wrote — or fail naming what it left out."""
    config = config or get_config()
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    argv = command(config.audio_separator, model, source, destination)
    tail: deque[str] = deque(maxlen=OUTPUT_TAIL)
    announced: str | None = None

    def on_line(line: str) -> None:
        nonlocal announced
        text = line.strip()
        if not text:
            return
        tail.append(text)
        # Only until the pass has named its device: it names it once, in the banner, and the
        # lines after that are a progress bar redrawn several times a second.
        if announced is None:
            named = device_of(text)
            if named is not None:
                announced = named
                _report_device(named, model)
        fraction = _percent(text)
        if fraction is not None and progress is not None:
            progress(fraction)

    log.info("Separating %s with %s", Path(source).name, model)
    try:
        returncode = (runner or run)(argv, on_line)
    except FileNotFoundError as exc:
        raise SeparatorUnavailableError(
            cause=f"No audio-separator at {config.audio_separator!r}.",
            detail={"executable": config.audio_separator, "model": model},
        ) from exc

    if announced is None:
        log.info("audio-separator named no device while running %s; it stays unknown", model)
    elif on_device is not None:
        on_device(announced)

    output = "\n".join(tail)
    if returncode != 0:
        raise StemSeparationError(
            cause=(
                f"audio-separator refused {Path(source).name} with {model} (exit {returncode})."
            ),
            detail={"model": model, "exit_code": returncode, "output": output},
        )

    produced = collect(destination)
    missing = [one for one in wanted if one not in produced]
    if missing:
        raise StemSeparationError(
            cause=f"{model} produced no {', '.join(missing)} stem.",
            detail={
                "model": model,
                "expected": list(wanted),
                "produced": sorted(produced),
                "output": output,
            },
        )
    log.info("%s produced %s", model, ", ".join(sorted(produced)))
    return produced


def missing_from(out_dir: Path | str, wanted: Iterable[str]) -> list[str]:
    """Which of these stems are not already sitting in this directory."""
    produced = collect(out_dir)
    return [one for one in wanted if one not in produced]


def _report_device(device: str, model: str) -> None:
    """Say what this pass runs on, at the moment it says so itself.

    A CPU pass is a warning even where the build explains it: this is the line a live failure
    outside a session gets diagnosed from, and "it was slow" is not a diagnosis.
    """
    if device == CPU_DEVICE:
        log.warning(
            "audio-separator is running %s on the CPU: a pass that takes minutes on the GPU "
            "takes hours here. See docs/reference/compute-device-inventory.md",
            model,
        )
    else:
        log.info("audio-separator is running %s on %s", model, device)


def _percent(line: str) -> float | None:
    """The separation bar's own percentage, as a fraction — ``None`` for any other line.

    A model download prints a bar of its own, and it is not progress through this pass: it
    runs to 100% before the separation has started, and reporting it would have the job
    look finished and then apparently restart. Dropping it costs nothing if the wording
    ever changes — the reading is simply reported as the separation's own.
    """
    if DOWNLOAD.search(line):
        return None
    found = PERCENT.search(line)
    if found is None:
        return None
    return min(int(found.group(1)) / FULL, 1.0)


def run(argv: Sequence[str], on_line: Lines) -> int:
    """The real call: stderr folded into stdout, read as it arrives.

    Named rather than private because a ``Runner`` that wants the real thing under it — a live
    test counting the passes a run actually paid for — has nowhere else to reach for it, and a
    second copy of this function is a second chance to get the encoding wrong.

    Universal newlines makes the progress bar's carriage returns line breaks, which is the
    only reason a tqdm bar can be followed line by line at all.

    The encoding is named rather than inherited. Left to the console, the decode is whatever
    codepage the launching process happened to have — cp1252 for a detached process or a
    service, where the first byte outside that page raises out of the read loop and takes a
    twenty-minute job with it (#139). ``replace`` for the same reason: what arrives here is a
    progress bar, and no character in one is worth failing a separation over.
    """
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    ) as process:
        if process.stdout is not None:
            for line in process.stdout:
                on_line(line)
        return process.wait()
