"""``FakeSeparator`` — a stand-in for the stem-separation backend."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .fixtures import write_wav

PREFIX = "2026-01-01 00:00:00,000 - INFO - separator"
"""How the real CLI stamps every line it prints."""

CUDA_BANNER = (f"{PREFIX} - CUDA is available in Torch, setting Torch device to CUDA",)
CPU_BANNER = (f"{PREFIX} - No hardware acceleration could be configured, running in CPU mode",)
"""The two device lines a pass opens with — the second one is G10's failure, spelled out."""


class FakeSeparator:
    """Stand in for the audio-separator CLI, one pass per call.

    It writes real WAVs under the naming convention the real thing uses —
    ``<input>_(Label)_<model>.wav`` — because the stems are matched by reading that label
    back, so a fake that wrote arbitrary names would prove nothing about the mapping. Each
    positional argument is one pass's labels, and the sequence starts over on the call
    after the last: a second separation of the same audio is another first pass, not a
    seventh one.

    ``banners`` cycles the same way, one entry per pass, because the device is read off what
    each pass prints and each pass is its own process: a run that reached the GPU once and
    then could not is the case the reading exists for. ``banners=()`` prints no device line
    at all — a version too old to name one.
    """

    def __init__(
        self,
        *passes: Sequence[str],
        returncode: int = 0,
        output: Sequence[str] = (),
        torch_build: str = "2.13.0+cu126",
        banners: Sequence[Sequence[str]] = (CUDA_BANNER,),
    ) -> None:
        self.passes = [tuple(one) for one in passes]
        self.returncode = returncode
        self.output = tuple(output)
        self.torch_build = torch_build
        self.banners = [tuple(one) for one in banners]
        self.calls: list[list[str]] = []
        self.probes: list[list[str]] = []

    def __call__(self, argv: Sequence[str], on_line: Callable[[str], None]) -> int:
        if "--env_info" in argv:
            # The build report (#202), answered the way the real CLI prints it and kept off
            # ``calls`` so the pass cycle still counts separations alone.
            self.probes.append(list(argv))
            on_line(f"{PREFIX} - PyTorch Version: {self.torch_build}")
            return 0
        self.calls.append(list(argv))
        for line in self._banner():
            on_line(line)
        for line in self.output:
            on_line(line)
        if self.returncode == 0:
            for label in self._labels():
                write_wav(self._target(argv, label), seconds=0.2)
        return self.returncode

    def _banner(self) -> tuple[str, ...]:
        if not self.banners:
            return ()
        return self.banners[(len(self.calls) - 1) % len(self.banners)]

    def _labels(self) -> tuple[str, ...]:
        if not self.passes:
            return ()
        return self.passes[(len(self.calls) - 1) % len(self.passes)]

    def _target(self, argv: Sequence[str], label: str) -> Path:
        out_dir = Path(argv[list(argv).index("--output_dir") + 1])
        model = Path(argv[list(argv).index("--model_filename") + 1]).stem
        return out_dir / f"{Path(argv[1]).stem}_({label.title()})_{model}.wav"
