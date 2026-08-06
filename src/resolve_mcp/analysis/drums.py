"""What the drummer hit, and when — one record per hit, per decomposed stem.

Separation (#36) already did the hard part. Once kick, snare and toms are three files
rather than one kit, "transcribe the drums" stops being a model problem and becomes onset
detection on near-clean audio: a transient in the toms file is a tom, because nothing else
is in that file. So the default transcriber is the spectral-flux detector the energy curve
already uses, run per stem and labelled by the stem it came from — no new model, no new
dependency, and the numbers are checkable on fixture audio.

The seam is kept anyway (ADR 0002 shape). Onsets on a stem are a *reading*, and a better
reading is a plausible upgrade — a real drum transcriber that separates rimshot from
centre, or one that reports velocity properly. Callers pass a ``Transcriber`` and the rule
layer downstream never knows the difference.

Strength is peak amplitude just after the onset, not velocity. It is enough for "was that
a ghost note or a hit" and deliberately not called velocity, which is a MIDI number this
does not have.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from ..errors import AnalysisFailedError, ResolveMcpError
from ..logging_config import get_logger

log = get_logger("analysis")

STRENGTH_SECONDS = 0.05
"""How much audio after an onset the strength is read off — a drum's attack, not its tail."""


class Hit(NamedTuple):
    """One drum stroke: when it landed, which stem it landed in, how hard it read."""

    seconds: float
    stem: str
    strength: float


Transcriber = Callable[[Mapping[str, Path]], "tuple[Hit, ...]"]
"""Turn a mapping of stem label to WAV into every hit across them, in any order."""


def transcribe(
    stems: Mapping[str, Path],
    transcriber: Transcriber | None = None,
) -> tuple[Hit, ...]:
    """Every hit across the stems, earliest first.

    A transcriber that falls over is an ``analysis_failed`` rather than an internal error,
    for the same reason a beat model that falls over is: the agent can act on it, and it is
    not a bug in this server.
    """
    chosen = transcriber or onset_transcriber
    try:
        hits = chosen(dict(stems))
    except ResolveMcpError:
        raise
    except Exception as exc:
        raise AnalysisFailedError(
            cause=f"Drum transcription failed: {type(exc).__name__}: {exc}.",
            detail={"stems": sorted(stems)},
        ) from exc
    return tuple(sorted(hits, key=lambda hit: (hit.seconds, hit.stem)))


def onset_transcriber(stems: Mapping[str, Path]) -> tuple[Hit, ...]:
    """The default: transients per stem, each labelled with the stem it was found in."""
    import numpy as np

    from . import decode, energy

    hits: list[Hit] = []
    for label, path in sorted(stems.items()):
        audio = decode.read(path)
        mono = np.abs(audio.mono())
        reach = max(int(STRENGTH_SECONDS * audio.sample_rate), 1)
        times = energy.onsets(audio)
        log.info("Found %d onsets in the %s stem", times.size, label)
        for seconds in times:
            first = min(int(seconds * audio.sample_rate), max(mono.size - 1, 0))
            window = mono[first : first + reach]
            strength = float(window.max()) if window.size else 0.0
            hits.append(Hit(seconds=float(seconds), stem=label, strength=min(strength, 1.0)))
    return tuple(hits)
