"""Reading a cut back as the words it will contain.

The measurement is a pure reading of two documents, so almost everything here runs with no
Resolve in sight; the two tool-level tests exist because the envelope still echoes project
context, and that is the only part of this that needs a handle at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import virtual
from resolve_mcp.analysis.transcript import Word
from resolve_mcp.config import Config
from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.tools import cut as cut_tools

from .conftest import Attach
from .fakes import studio
from .roughcut import (
    CLOSE,
    FPS,
    GOOD_TAKE,
    SPOKEN,
    a_cut,
    a_transcript,
    both_takes,
    delivered,
)


def a_reading(tmp_path: Path, doc: Any, **kwargs: Any) -> dict[str, Any]:
    """The cut read back, with cam_a's transcript supplied unless a test says otherwise."""
    transcripts = kwargs.pop("transcripts", None)
    if transcripts is None:
        transcripts = {"cam_a": a_transcript(tmp_path)}
    return virtual.virtual_transcript(a_cut(tmp_path, doc), transcripts, **kwargs)


def rules(reading: dict[str, Any]) -> list[str]:
    return [finding["rule"] for finding in reading["warnings"]]


def messages(reading: dict[str, Any], rule: str) -> list[str]:
    return [one["message"] for one in reading["warnings"] if one["rule"] == rule]


def test_the_cut_reads_back_as_the_words_it_kept(tmp_path: Path) -> None:
    """The whole point: what does the assembly say, now that the takes are chosen?"""
    reading = a_reading(tmp_path, delivered())

    assert reading["text"] == "we start here and finish"


def test_each_segment_reads_back_on_its_own(tmp_path: Path) -> None:
    """Per shot, so a line that reads wrong can be traced to the segment that carries it."""
    reading = a_reading(tmp_path, delivered())

    assert [(one["id"], one["text"]) for one in reading["segments"]] == [
        ("s001", "we start here"),
        ("s002", "and finish"),
    ]


def test_every_word_lands_at_the_frame_the_build_will_put_it_at(tmp_path: Path) -> None:
    """Source frames in, timeline frames out — the conversion nobody should do by hand.

    ``we`` opens the good take, so it lands at 0 even though it is frame 48 of the camera;
    ``and`` opens the second segment, so it lands at the seam, 44 frames in.
    """
    reading = a_reading(tmp_path, delivered())

    assert [(one["word"], one["at"]["frames"]) for one in reading["words"]] == [
        ("we", 0),
        ("start", 12),
        ("here", 28),
        ("and", 44),
        ("finish", 56),
    ]


def test_the_reading_echoes_the_cut_it_read(tmp_path: Path) -> None:
    """The content hash is how a self-review is pinned to the exact cut state it reviewed."""
    path = a_cut(tmp_path, delivered())

    reading = virtual.virtual_transcript(path, {"cam_a": a_transcript(tmp_path)})

    assert reading["cut_file"] == path
    assert reading["content_hash"]
    assert reading["timeline"] == {"name": "interview", "fps": FPS}
    assert reading["total"]["frames"] == 74


def test_a_word_the_cut_chops_in_half_is_reported(tmp_path: Path) -> None:
    """``here`` runs to frame 92; a cut at 90 takes two thirds of it and leaves a fragment."""
    doc = delivered()
    doc["segments"][0]["out"] = 90

    reading = a_reading(tmp_path, doc)

    assert "W3" in rules(reading)
    assert "'here'" in messages(reading, "W3")[0]


def test_a_chopped_word_names_the_frame_that_would_have_spared_it(tmp_path: Path) -> None:
    """A finding that only says "too early" costs a round trip to find out by how much."""
    doc = delivered()
    doc["segments"][0]["out"] = 90

    reading = a_reading(tmp_path, doc)
    hint = next(one["fix_hint"] for one in reading["warnings"] if one["rule"] == "W3")

    assert "frame 92" in hint


def test_a_chopped_word_is_not_counted_as_delivered(tmp_path: Path) -> None:
    """A fragment is not a word the cut says, so it stays out of the read-back."""
    doc = delivered()
    doc["segments"][0]["out"] = 90

    reading = a_reading(tmp_path, doc)

    assert reading["segments"][0]["text"] == "we start"


def test_the_same_line_surviving_twice_is_reported(tmp_path: Path) -> None:
    """The false start left in front of the good take — the failure this pillar exists to catch."""
    reading = a_reading(tmp_path, both_takes())

    assert "W4" in rules(reading)
    assert "'we start'" in messages(reading, "W4")[0]


def test_one_word_repeating_across_a_seam_is_not_reported(tmp_path: Path) -> None:
    """"and" ending one shot and opening the next is ordinary English, not a doubled take."""
    words = (
        Word("so", 0.0, 0.4, 0.9),
        Word("and", 0.5, 1.0, 0.9),
        Word("and", 2.0, 2.4, 0.9),
        Word("then", 2.5, 3.0, 0.9),
    )
    doc = delivered()
    doc["segments"] = [
        {"id": "s001", "source": "cam_a", "in": 0, "out": 26},
        {"id": "s002", "source": "cam_a", "in": 48, "out": 74},
    ]
    doc["overlays"] = []

    reading = a_reading(tmp_path, doc, transcripts={"cam_a": a_transcript(tmp_path, words)})

    assert reading["text"] == "so and and then"
    assert "W4" not in rules(reading)


def test_a_source_with_no_transcript_is_reported_not_guessed_at(tmp_path: Path) -> None:
    """Silence in the read-back could mean "said nothing" or "nobody looked" — say which."""
    reading = a_reading(tmp_path, delivered(), transcripts={})

    assert rules(reading).count("W5") == 2
    assert reading["text"] == ""


def test_a_segment_with_no_transcript_still_holds_its_place(tmp_path: Path) -> None:
    """Its words are unknown, but its frames are not: everything after it still lands right."""
    reading = a_reading(tmp_path, delivered(), transcripts={})

    assert [one["at"]["frames"] for one in reading["segments"]] == [0, 44]
    assert reading["total"]["frames"] == 74


def test_a_word_the_transcriber_was_unsure_of_is_flagged_where_it_is_delivered(
    tmp_path: Path,
) -> None:
    """The uncertainty the cut report carries: it survived the edit, so it needs a listen."""
    reading = a_reading(tmp_path, delivered())

    assert messages(reading, "W6") == ["'finish' is delivered at confidence 0.3"]


def test_an_unsure_word_left_out_of_the_cut_is_not_flagged(tmp_path: Path) -> None:
    """Only delivered words are uncertainties; one that was cut is not the director's problem."""
    doc = delivered()
    doc["segments"] = [doc["segments"][0]]
    doc["overlays"] = []

    reading = a_reading(tmp_path, doc)

    assert "W6" not in rules(reading)


def test_the_confidence_floor_moves(tmp_path: Path) -> None:
    """A noisy shoot flags everything at the default; the floor is the agent's to set."""
    reading = a_reading(tmp_path, delivered(), below=0.2)

    assert "W6" not in rules(reading)


def test_a_cut_from_one_camera_back_to_itself_is_a_jump_cut(tmp_path: Path) -> None:
    reading = a_reading(tmp_path, delivered())

    assert [one["jump_cut"] for one in reading["seams"]] == [True]
    assert reading["seams"][0]["at"]["frames"] == 44


def test_a_cut_between_two_cameras_is_not(tmp_path: Path) -> None:
    """Covering an ordinary A-to-B edit would be covering nothing."""
    doc = delivered()
    doc["segments"][1]["source"] = "broll_hands"
    doc["segments"][1]["in"] = 0
    doc["segments"][1]["out"] = 30

    reading = a_reading(tmp_path, doc)

    assert [one["jump_cut"] for one in reading["seams"]] == [False]
    assert "W7" not in rules(reading)


def test_an_overlay_riding_across_the_seam_covers_it(tmp_path: Path) -> None:
    """b01 opens at 36 and closes at 52; the seam is at 44, so the jump is hidden."""
    reading = a_reading(tmp_path, delivered())

    assert reading["seams"][0]["covered_by"] == "b01"
    assert "W7" not in rules(reading)


def test_an_uncovered_jump_cut_is_reported(tmp_path: Path) -> None:
    doc = delivered()
    doc["overlays"] = []

    reading = a_reading(tmp_path, doc)

    assert "W7" in rules(reading)
    assert reading["seams"][0]["covered_by"] is None


def test_an_overlay_that_stops_short_of_the_seam_does_not_cover_it(tmp_path: Path) -> None:
    """Anchored coverage is a span, not an intention — 36 to 44 ends exactly at the join."""
    doc = delivered()
    doc["overlays"][0]["out"] = 8

    reading = a_reading(tmp_path, doc)

    assert reading["seams"][0]["covered_by"] is None
    assert "W7" in rules(reading)


def test_the_counts_summarise_what_the_findings_say(tmp_path: Path) -> None:
    """The line a cut report opens with, so nobody counts findings by hand."""
    reading = a_reading(tmp_path, delivered())

    assert reading["counts"] == {
        "words": 5,
        "unsure": 1,
        "clipped": 0,
        "jump_cuts": 1,
        "uncovered": 0,
    }


def test_nothing_here_blocks_a_cut(tmp_path: Path) -> None:
    """Every rule is a warning: this measures a cut, it never refuses one."""
    doc = both_takes()
    doc["segments"][1]["out"] = 90

    reading = a_reading(tmp_path, doc)

    assert set(rules(reading)) >= {"W3", "W4", "W7"}
    assert all(rule.startswith("W") for rule in rules(reading))
    assert "errors" not in reading


def test_a_long_reading_goes_to_disk(tmp_path: Path) -> None:
    """Past the inline cap the words are a file to grep, not a tool result to truncate."""
    words = tuple(
        Word(f"word{index}", index * 0.5, index * 0.5 + 0.4, 0.9)
        for index in range(virtual.INLINE_WORDS + 50)
    )
    doc = delivered()
    doc["segments"] = [{"id": "s001", "source": "cam_a", "in": 0, "out": 3100}]
    doc["overlays"] = []
    config = Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path / "cache")})

    reading = a_reading(
        tmp_path,
        doc,
        transcripts={"cam_a": a_transcript(tmp_path, words)},
        config=config,
    )

    assert reading["truncated"] is True
    assert len(reading["words"]) == virtual.INLINE_WORDS
    assert Path(reading["spilled_to"]).is_file()


def test_a_short_reading_stays_inline(tmp_path: Path) -> None:
    reading = a_reading(tmp_path, delivered())

    assert reading["truncated"] is False
    assert reading["spilled_to"] is None


def test_a_transcript_that_is_not_there_says_so(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        a_reading(tmp_path, delivered(), transcripts={"cam_a": str(tmp_path / "nope.json")})

    assert "No transcript" in raised.value.cause
    assert "transcribe_audio" in (raised.value.fix or "")


def test_a_document_that_is_not_a_transcript_says_so(tmp_path: Path) -> None:
    """A beats file and a transcript are both analysis JSON; only one has words in it."""
    other = tmp_path / "beats.json"
    other.write_text('{"kind": "beats", "beats": []}', encoding="utf-8")

    with pytest.raises(InvalidRequestError) as raised:
        a_reading(tmp_path, delivered(), transcripts={"cam_a": str(other)})

    assert "no words" in raised.value.cause


def test_a_cut_file_with_no_segments_is_sent_to_validate_cut(tmp_path: Path) -> None:
    """This is a reading, not a second validator — one place owns what a cut file must be."""
    doc = delivered()
    doc["segments"] = []

    with pytest.raises(InvalidRequestError) as raised:
        a_reading(tmp_path, doc)

    assert "validate_cut" in (raised.value.fix or "")


def test_a_cut_file_that_is_not_json_is_sent_to_validate_cut(tmp_path: Path) -> None:
    path = tmp_path / "broken.cut.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(InvalidRequestError) as raised:
        virtual.virtual_transcript(str(path), {})

    assert "validate_cut" in (raised.value.fix or "")


def test_a_cut_file_with_no_fps_is_sent_to_validate_cut(tmp_path: Path) -> None:
    """Without a frame rate a word in seconds has no frame to land on."""
    doc = delivered()
    doc["timeline"] = {"name": "interview"}

    with pytest.raises(InvalidRequestError) as raised:
        a_reading(tmp_path, doc)

    assert "timeline.fps" in raised.value.cause


def test_the_tool_returns_the_reading_in_an_envelope(attach: Attach, tmp_path: Path) -> None:
    """Through the tool layer: the payload is the reading, and context rides along as always."""
    attach(studio())

    result = cut_tools.virtual_transcript(
        a_cut(tmp_path, delivered()),
        {"cam_a": a_transcript(tmp_path)},
    )

    assert result["ok"] is True
    assert result["text"] == "we start here and finish"
    assert "context" in result


def test_the_tool_reports_a_missing_transcript_as_a_failed_envelope(
    attach: Attach, tmp_path: Path
) -> None:
    """Never an exception across the tool boundary, never a traceback in the payload."""
    attach(studio())

    result = cut_tools.virtual_transcript(
        a_cut(tmp_path, delivered()),
        {"cam_a": str(tmp_path / "nope.json")},
    )

    assert result["ok"] is False
    assert "transcribe_audio" in result["error"]["fix"]


def test_the_good_take_is_the_one_the_fixture_delivers() -> None:
    """Guards the fixture itself: the frames below are what every assertion above reads."""
    assert GOOD_TAKE == (48, 92)
    assert CLOSE == (120, 150)
    assert len(SPOKEN) == 8
