"""The cut tools at the Resolve seam: what the schema serves, what the dry run reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.cut.schema import ANNOTATED_EXAMPLE, SCHEMA_DOC
from resolve_mcp.tools.cut import get_cut_schema, validate_cut

from .conftest import Attach
from .fakes import FakeMediaPoolItem, media_pool, studio


def a_cut(tmp_path: Path, doc: Any, name: str = "sunset-set.cut.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def valid_doc() -> dict[str, Any]:
    return {
        "schema": 1,
        "timeline": {"name": "sunset-set", "fps": 59.94},
        "sources": {
            "gtr_close": {"clip": "C0012.mp4", "bin": "Angles"},
            "master_mix": {"clip": "sunset-master.wav"},
        },
        "audio": {"source": "master_mix", "in": 0, "out": 178},
        "segments": [{"id": "s001", "source": "gtr_close", "in": 14032, "out": 14210}],
    }


def a_pool(**overrides: dict[str, str]) -> Any:
    """The media pool :func:`valid_doc` validates against."""
    angle = FakeMediaPoolItem(
        "C0012.mp4",
        file_path="D:/media/C0012.mp4",
        properties={"Frames": "20000", "Start": "0", "End": "19999", **overrides.get("angle", {})},
    )
    master = FakeMediaPoolItem(
        "sunset-master.wav",
        file_path="D:/media/sunset-master.wav",
        properties={
            "Type": "Audio",
            "FPS": "",
            "Frames": "400",
            "Start": "0",
            "End": "399",
            **overrides.get("master", {}),
        },
    )
    return media_pool({"Angles": [angle], "": [master]})


# --- get_cut_schema ---------------------------------------------------------------------


def test_the_schema_is_served_with_its_annotated_example_verbatim(attach: Attach) -> None:
    attach(studio())

    result = get_cut_schema()

    assert result["ok"] is True
    assert result["schema"] == 1
    assert result["annotated_example"] == ANNOTATED_EXAMPLE
    assert result["document"] == SCHEMA_DOC
    assert ANNOTATED_EXAMPLE in result["document"]


def test_the_served_example_carries_its_annotations() -> None:
    """The comments are the point — a stripped example teaches the agent nothing."""
    assert "// source-clip frames, half-open [in, out)" in ANNOTATED_EXAMPLE
    assert "// optional; same duration as main" in ANNOTATED_EXAMPLE


def test_the_schema_lists_every_rule_with_its_severity(attach: Attach) -> None:
    attach(studio())

    rules = {rule["rule"]: rule["severity"] for rule in get_cut_schema()["rules"]}

    assert [rule for rule, severity in rules.items() if severity == "error"] == [
        f"E{number}" for number in range(1, 13)
    ]
    # W3-W7 are virtual_transcript's over the same document, so this list skips to W8.
    assert [rule for rule, severity in rules.items() if severity == "warning"] == [
        "W1",
        "W2",
        "W8",
    ]


def test_the_schema_serves_without_resolve(attach: Attach) -> None:
    """The contract is a constant; needing a project to read it would be absurd."""
    attach(None)

    result = get_cut_schema()

    assert result["ok"] is True
    assert result["context"]["connected"] is False


# --- validate_cut: the clean case -------------------------------------------------------


def test_a_valid_cut_file_passes_clean(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool()))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["warnings"] == []


def test_a_valid_cut_file_reports_what_it_describes(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool()))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert result["cut"] == {
        "timeline": "sunset-set",
        "segments": 1,
        "gaps": 0,
        "overlays": 0,
        "duration": {
            "frames": 178,
            "seconds": 2.97,
            "timecode": "00:00:02:58",
            "fps": 59.94,
        },
    }


def test_the_report_echoes_the_hash_of_the_bytes_it_validated(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=a_pool()))
    cut_file = a_cut(tmp_path, valid_doc())

    first = validate_cut(cut_file)
    edited = valid_doc()
    edited["segments"][0]["out"] = 14200
    second = validate_cut(a_cut(tmp_path, edited, "edited.cut.json"))

    assert first["content_hash"] != second["content_hash"]
    assert validate_cut(cut_file)["content_hash"] == first["content_hash"]


# --- validate_cut: failures -------------------------------------------------------------


def test_a_missing_clip_is_reported_against_its_alias(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=media_pool({})))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert result["valid"] is False
    reported = {error["id"]: error for error in result["errors"]}
    assert set(reported) == {"gtr_close", "master_mix"}, "every failure, not just the first"
    assert reported["gtr_close"]["rule"] == "E4"
    assert "C0012.mp4" in reported["gtr_close"]["message"]
    assert reported["gtr_close"]["fix_hint"]


def test_a_range_past_the_end_of_the_media_is_reported_against_its_segment(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=a_pool()))
    doc = valid_doc()
    doc["segments"][0]["out"] = 30000

    result = validate_cut(a_cut(tmp_path, doc))

    assert [error["rule"] for error in result["errors"]] == ["E5"]
    assert result["errors"][0]["id"] == "s001"


def test_a_source_at_the_wrong_rate_is_reported(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool(angle={"FPS": "29.97"})))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert [error["rule"] for error in result["errors"]] == ["E6"]


def test_a_master_clip_with_no_audio_is_reported(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool(master={"Audio Ch": "0"})))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert [error["rule"] for error in result["errors"]] == ["E7"]


def test_a_channel_count_resolve_will_not_report_fails_open(
    attach: Attach, tmp_path: Path
) -> None:
    """E7 reads an undocumented property key; an unreadable one must not block a good cut."""
    attach(studio(pool=a_pool(master={"Audio Ch": ""})))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert result["valid"] is True
    assert result["errors"] == []


AUDIO_ONLY_MASTER = {
    "FPS": "",
    "Frames": "",
    "Start": "",
    "End": "",
    "Duration": "01:26:38:09",
    "Sample Rate": "48000",
    "Audio Ch": "2",
}
"""What an audio-only pool clip really reports (#46, live-verified): Start/End/Frames are
empty strings and only Duration carries the length — 01:26:38:09 is 124761 frames at the
timeline's nominal 24. The channel count is present so these tests exercise E7's bounds
leg, not its has-audio fail-open."""


def test_an_audio_clip_reporting_only_a_duration_still_bounds_the_audio_block(
    attach: Attach, tmp_path: Path
) -> None:
    """The bug that motivated the fallback: bounds read as 0-0 failed every valid range."""
    attach(studio(pool=a_pool(angle={"FPS": "23.976"}, master=AUDIO_ONLY_MASTER)))
    doc = valid_doc()
    doc["timeline"]["fps"] = 23.976
    doc["audio"] = {"source": "master_mix", "in": 36439, "out": 47531}
    doc["segments"] = [{"id": "s001", "source": "gtr_close", "in": 2000, "out": 13092}]

    result = validate_cut(a_cut(tmp_path, doc))

    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["valid"] is True


def test_e7_still_catches_an_overrun_against_duration_read_bounds(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=a_pool(angle={"FPS": "23.976"}, master=AUDIO_ONLY_MASTER)))
    doc = valid_doc()
    doc["timeline"]["fps"] = 23.976
    doc["audio"] = {"source": "master_mix", "in": 0, "out": 124762}  # one past the media

    result = validate_cut(a_cut(tmp_path, doc))

    assert [error["rule"] for error in result["errors"]] == ["E7"]
    assert "124761" in result["errors"][0]["message"]


def test_bounds_nothing_could_derive_fail_open_rather_than_read_as_empty_media(
    attach: Attach, tmp_path: Path
) -> None:
    """An unparseable Duration leaves the bounds unknown, and unknown means "cannot
    verify", never "media runs 0-0" (the E7 resurrection this guards against): the
    bounds leg skips the clip, the same fail-open stance as the has-audio leg."""
    master = {**AUDIO_ONLY_MASTER, "Duration": "01:26:38"}  # not a timecode
    attach(studio(pool=a_pool(angle={"FPS": "23.976"}, master=master)))
    doc = valid_doc()
    doc["timeline"]["fps"] = 23.976
    doc["audio"] = {"source": "master_mix", "in": 36439, "out": 47531}
    doc["segments"] = [{"id": "s001", "source": "gtr_close", "in": 2000, "out": 13092}]

    result = validate_cut(a_cut(tmp_path, doc))

    assert result["errors"] == []
    assert result["valid"] is True


def test_a_short_segment_warns_without_blocking(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool()))
    doc = valid_doc()
    doc["segments"][0]["out"] = 14036  # four frames

    result = validate_cut(a_cut(tmp_path, doc))

    assert result["valid"] is True
    assert [warning["rule"] for warning in result["warnings"]] == ["W1", "W2"]


def test_the_flash_frame_threshold_is_tunable(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=a_pool()))

    result = validate_cut(a_cut(tmp_path, valid_doc()), min_segment_frames=200)

    assert result["valid"] is True
    assert [warning["rule"] for warning in result["warnings"]] == ["W1"]


# --- validate_cut: files that are not cut files -----------------------------------------


def test_a_file_that_is_not_json_fails_e1_and_is_still_hashed(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=a_pool()))
    path = tmp_path / "broken.cut.json"
    path.write_text("{ this is not json", encoding="utf-8")

    result = validate_cut(str(path))

    assert result["ok"] is True
    assert result["valid"] is False
    assert [error["rule"] for error in result["errors"]] == ["E1"]
    assert result["content_hash"]
    assert result["cut"] is None


def test_a_schema_invalid_file_never_reaches_the_media_pool(
    attach: Attach, tmp_path: Path
) -> None:
    """A malformed file must not cost a round trip to Resolve."""
    pool = a_pool()
    attach(studio(pool=pool))
    doc = valid_doc()
    doc["schema"] = 2
    pool.calls.clear()

    result = validate_cut(a_cut(tmp_path, doc))

    assert [error["rule"] for error in result["errors"]] == ["E1"]
    assert pool.calls == []


def test_a_cut_file_that_is_not_there_is_a_request_failure(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=a_pool()))

    result = validate_cut(str(tmp_path / "nope.cut.json"))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "nope.cut.json" in result["error"]["cause"]
    assert result["error"]["fix"]


# --- connection behaviour ---------------------------------------------------------------


def test_validation_survives_resolve_dying_mid_call(attach: Attach, tmp_path: Path) -> None:
    dying = studio(pool=a_pool())
    dying.die_after(1)
    attach(dying, studio(pool=a_pool()))

    result = validate_cut(a_cut(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert result["valid"] is True
