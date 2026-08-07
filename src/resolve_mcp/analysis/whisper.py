"""The default backend: faster-whisper, large-v3, word timestamps on.

External to Resolve on purpose (#10). Resolve's own transcription writes end-user subtitles
— no per-word confidence, no way to get the words out except as a subtitle track — and
confidence is the whole reason an agent reads a transcript instead of watching the take.

The import is inside the function because the CUDA runtime behind it costs seconds to load
and the server must start in the time a tool call takes. It is also the reason this is a
seam: everything above it is tested with the model substituted, and this module is the only
thing that faster-whisper's absence can break.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import DEFAULT_WHISPER_COMPUTE_TYPE, DEFAULT_WHISPER_DEVICE, get_config
from ..errors import TranscriberUnavailableError, TranscriptionError
from ..logging_config import get_logger
from . import cuda
from .transcript import Transcription, Word

log = get_logger("analysis")

DEFAULT_MODEL = "large-v3"


def transcribe(audio: Path, params: Mapping[str, Any]) -> Transcription:
    """Word-level transcription of one WAV. Slow, GPU-bound, and always inside a job."""
    model_name = str(params.get("model") or DEFAULT_MODEL)
    language = params.get("language") or None
    model = _model(model_name)

    log.info("Transcribing %s with %s", audio.name, model_name)
    try:
        segments, info = model.transcribe(
            str(audio),
            word_timestamps=True,
            language=str(language) if language else None,
        )
        words = tuple(_word(one) for segment in segments for one in (segment.words or ()))
    except Exception as exc:  # noqa: BLE001 - the backend raises its own unrelated types
        raise TranscriptionError(
            cause=f"faster-whisper failed on {audio.name}: {type(exc).__name__}: {exc}",
            fix="Check the WAV plays, and that the GPU is free; then start the job again.",
            detail={"path": str(audio), "model": model_name},
        ) from exc

    return Transcription(words=words, language=_language(info))


def _model(name: str) -> Any:
    config = get_config()
    # Before the model is built, never after: CTranslate2 loads its CUDA libraries at the
    # first allocation on the device, and by then there is nothing left to prepare (#128).
    cuda.prepare()
    device, compute_type = config.whisper_device, config.whisper_compute_type
    try:
        return _build(name, device, compute_type)
    except TranscriberUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - the backend raises its own unrelated types
        # Since #128 these two are typed by whoever set the variable, so a typo reaches
        # here as a raw backend error. Say which value was refused and where it came from.
        raise TranscriptionError(
            cause=(
                f"faster-whisper would not load {name} on device={device!r} "
                f"compute_type={compute_type!r}: {type(exc).__name__}: {exc}"
            ),
            fix=_load_fix(device, compute_type),
            detail={"model": name, "device": device, "compute_type": compute_type},
        ) from exc


def _load_fix(device: str, compute_type: str) -> str:
    """Advice that matches who is likely at fault: the settings, or the install.

    Blaming the env variables when nobody set them is how #128 itself would read — a
    missing CUDA runtime on a stock config, told to go check its device string.
    """
    if device == DEFAULT_WHISPER_DEVICE and compute_type == DEFAULT_WHISPER_COMPUTE_TYPE:
        return (
            "Both transcriber settings are at their defaults, so this is the install "
            "rather than a setting: check that `uv sync --extra analysis` has run against "
            "the venv this server is using — the CUDA runtime ships with that extra."
        )
    return (
        "RESOLVE_MCP_WHISPER_DEVICE takes 'auto', 'cuda' or 'cpu'; "
        "RESOLVE_MCP_WHISPER_COMPUTE_TYPE takes what CTranslate2 accepts (e.g. 'default', "
        "'float32', 'float16', 'int8'). Unset both to use the defaults."
    )


def _build(name: str, device: str, compute_type: str) -> Any:
    """The one import of faster-whisper, and the only line an absent backend can break."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriberUnavailableError(
            cause="faster-whisper is not installed in this environment.",
            detail={"model": name},
        ) from exc
    log.info("Loading %s on device=%s compute_type=%s", name, device, compute_type)
    return WhisperModel(name, device=device, compute_type=compute_type)


def _word(word: Any) -> Word:
    """One word as faster-whisper reports it: leading space on the text, probability 0-1."""
    return Word(
        text=str(word.word).strip(),
        start=float(word.start),
        end=float(word.end),
        confidence=float(word.probability),
    )


def _language(info: Any) -> str | None:
    detected = getattr(info, "language", None)
    return str(detected) if detected else None
