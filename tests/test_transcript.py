"""The reading half of transcription: silence off the waveform, and the document on disk.

Both are pure — a WAV in, numbers out — so they are tested without a job, a thread or a
model anywhere near them. What is under test is the part an agent will act on: that a gap
in the audio comes back as a span with the right edges, that a run of unsure words is one
region rather than five, and that the file written is greppable line-by-line while still
parsing as JSON.
"""

from __future__ import annotations

import contextlib
import json
import wave
from pathlib import Path

import pytest

from resolve_mcp.analysis import silence, transcript
from resolve_mcp.analysis.transcript import Word
from resolve_mcp.errors import AudioExtractionError

from .fakes import write_wav

WINDOW = 0.05
TOLERANCE = 2 * WINDOW


def _words(*spec: tuple[str, float, float, float]) -> list[Word]:
    return [Word(text=one[0], start=one[1], end=one[2], confidence=one[3]) for one in spec]


# --- levels off the waveform -------------------------------------------------------------


def test_a_continuous_tone_has_no_window_anywhere_near_silence(tmp_path: Path) -> None:
    audio = write_wav(tmp_path / "tone.wav", seconds=1.0)

    measured = silence.levels(audio, window_seconds=WINDOW)

    assert len(measured) == pytest.approx(1.0 / WINDOW, abs=1)
    assert max(measured) < 0.0
    assert min(measured) > silence.DEFAULT_THRESHOLD_DB


def test_a_zeroed_stretch_reads_at_the_floor_and_the_tone_around_it_does_not(
    tmp_path: Path,
) -> None:
    audio = write_wav(tmp_path / "gap.wav", seconds=1.0, silence=[(0.4, 0.9)])

    measured = silence.levels(audio, window_seconds=WINDOW)

    middle = measured[int(0.5 / WINDOW) : int(0.8 / WINDOW)]
    assert all(level == silence.SILENT_FLOOR_DB for level in middle)
    assert measured[0] > silence.DEFAULT_THRESHOLD_DB


def test_every_supported_bit_depth_is_read_as_signed_pcm(tmp_path: Path) -> None:
    """A width read the wrong way round reports a loud tone as silence, or the reverse."""
    for depth in (16, 24, 32):
        audio = write_wav(tmp_path / f"tone{depth}.wav", seconds=0.4, bit_depth=depth)

        measured = silence.levels(audio, window_seconds=WINDOW)

        assert min(measured) > silence.DEFAULT_THRESHOLD_DB, depth


def test_eight_bit_audio_is_refused_rather_than_read_as_if_it_were_signed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "eight.wav"
    with contextlib.closing(wave.open(str(path), "wb")) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(48_000)
        handle.writeframes(b"\x80" * 4800)

    with pytest.raises(AudioExtractionError) as raised:
        silence.levels(path, window_seconds=WINDOW)

    assert "8-bit" in raised.value.cause


# --- spans out of levels -----------------------------------------------------------------


def test_neighbouring_quiet_windows_become_one_span(tmp_path: Path) -> None:
    levels = [-10.0, -80.0, -80.0, -80.0, -80.0, -10.0]

    found = silence.spans(levels, window_seconds=0.1, threshold_db=-40.0, min_seconds=0.2)

    assert found == [{"start": 0.1, "end": 0.5, "duration": 0.4}]


def test_a_gap_shorter_than_the_minimum_is_not_breathing_room(tmp_path: Path) -> None:
    levels = [-10.0, -80.0, -10.0, -80.0, -80.0, -80.0]

    found = silence.spans(levels, window_seconds=0.1, threshold_db=-40.0, min_seconds=0.2)

    assert found == [{"start": 0.3, "end": 0.6, "duration": 0.3}]


def test_a_span_running_to_the_end_is_clamped_to_the_audio(tmp_path: Path) -> None:
    levels = [-10.0, -80.0, -80.0]

    found = silence.spans(
        levels, window_seconds=0.1, threshold_db=-40.0, min_seconds=0.1, limit=0.25
    )

    assert found == [{"start": 0.1, "end": 0.25, "duration": 0.15}]


def test_the_whole_route_finds_the_gap_that_was_written_into_the_fixture(
    tmp_path: Path,
) -> None:
    audio = write_wav(tmp_path / "gap.wav", seconds=2.0, silence=[(0.6, 1.4)])

    found = silence.silence(audio, window_seconds=WINDOW, min_seconds=0.35)

    assert len(found) == 1
    assert found[0]["start"] == pytest.approx(0.6, abs=TOLERANCE)
    assert found[0]["end"] == pytest.approx(1.4, abs=TOLERANCE)


# --- low-confidence regions --------------------------------------------------------------


def test_a_run_of_unsure_words_is_one_region_carrying_its_text() -> None:
    words = _words(
        ("the", 0.0, 0.2, 0.99),
        ("bridge", 0.2, 0.5, 0.31),
        ("was", 0.5, 0.7, 0.22),
        ("fine", 0.7, 1.0, 0.98),
    )

    found = transcript.regions(words, below=0.5)

    assert found == [
        {"start": 0.2, "end": 0.7, "duration": 0.5, "words": 2, "text": "bridge was"}
    ]


def test_unsure_words_with_a_confident_one_between_them_stay_separate() -> None:
    words = _words(
        ("uh", 0.0, 0.2, 0.10),
        ("yes", 0.2, 0.4, 0.99),
        ("mm", 0.4, 0.6, 0.20),
    )

    found = transcript.regions(words, below=0.5)

    assert [one["text"] for one in found] == ["uh", "mm"]


# --- the document ------------------------------------------------------------------------


def _document() -> dict[str, object]:
    return transcript.document(
        audio={"path": "C:/cache/audio/set.wav", "content_sha256": "abc", "duration_seconds": 4.0},
        params={"scope": "clip", "clip": "C0012.mp4", "model": "large-v3", "low_confidence": 0.5},
        words=_words(
            ("one", 0.0, 0.5, 0.9),
            ("two", 0.5, 1.0, 0.3),
            ("three", 3.0, 3.5, 0.7),
        ),
        silence=[{"start": 1.0, "end": 3.0, "duration": 2.0}],
        language="en",
    )


def test_the_gist_stats_answer_what_the_agent_asks_before_reading_the_file() -> None:
    stats = _document()["stats"]
    assert isinstance(stats, dict)

    assert stats["duration_seconds"] == 4.0
    assert stats["word_count"] == 3
    assert stats["low_confidence_words"] == 1
    assert stats["low_confidence_regions"] == 1
    assert stats["silence_spans"] == 1
    assert stats["silence_seconds"] == 2.0
    assert stats["speech_seconds"] == 2.0
    assert stats["mean_confidence"] == pytest.approx(0.633, abs=0.001)


def test_every_word_carries_its_own_timestamps_and_confidence() -> None:
    words = _document()["words"]
    assert isinstance(words, list)

    assert words[0] == {"start": 0.0, "end": 0.5, "word": "one", "confidence": 0.9}
    assert [one["start"] for one in words] == [0.0, 0.5, 3.0]


def test_the_file_is_valid_json_with_one_word_per_line_so_it_can_be_grepped(
    tmp_path: Path,
) -> None:
    target = tmp_path / "set.transcript.json"

    transcript.write(target, _document())

    text = target.read_text(encoding="utf-8")
    assert json.loads(text) == _document()
    assert len([line for line in text.splitlines() if '"word":' in line]) == 3
    assert len([line for line in text.splitlines() if '"duration":' in line]) == 2


def test_the_inline_result_previews_the_unsure_regions_without_carrying_the_words(
    tmp_path: Path,
) -> None:
    target = tmp_path / "set.transcript.json"
    document = _document()

    gist = transcript.gist(document, target)

    assert gist["path"] == str(target)
    assert gist["stats"] == document["stats"]
    assert gist["language"] == "en"
    assert [one["text"] for one in gist["low_confidence_regions"]] == ["two"]
    assert gist["low_confidence_truncated"] is False
    assert "words" not in gist


def test_a_transcript_full_of_unsure_regions_previews_a_capped_number_of_them(
    tmp_path: Path,
) -> None:
    # Alternating, so each unsure word is its own region rather than one long run.
    many: list[tuple[str, float, float, float]] = []
    for index in range(40):
        many.append(("um", index * 1.0, index * 1.0 + 0.4, 0.1))
        many.append(("yes", index * 1.0 + 0.4, index * 1.0 + 0.8, 0.99))
    document = transcript.document(
        audio={"path": "a.wav", "content_sha256": "abc", "duration_seconds": 40.0},
        params={"scope": "clip", "clip": "a", "model": "large-v3", "low_confidence": 0.5},
        words=_words(*many),
        silence=[],
        language="en",
    )

    gist = transcript.gist(document, tmp_path / "a.transcript.json")

    assert len(gist["low_confidence_regions"]) == transcript.PREVIEW_REGIONS
    assert gist["low_confidence_truncated"] is True
