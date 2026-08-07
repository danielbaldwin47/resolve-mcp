"""PNG title cards: what stands behind an event on disk, and the two rules about it.

The disk is the seam here — there is no fake for it and none is wanted, because the thing
under test *is* the reading of real files. Every card in this file is written into
``tmp_path``, so a sequence with a hole in it or a card that was never exported is an
arrangement of actual files rather than a mocked answer about them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from resolve_mcp.findings import Finding
from resolve_mcp.titles.assets import bin_for, frames_on_disk, resolve_asset
from resolve_mcp.titles.validate import validate_assets

PATTERN = "cards/sunset-boulevard/personnel_%04d.png"


def a_card(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": "p01",
        "kind": "personnel",
        "route": "png",
        "asset": PATTERN,
        "in": 0,
        "out": 240,
    }
    event.update(overrides)
    return event


def a_doc(*events: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": 1,
        "songs": [{"key": "sunset-boulevard", "events": list(events)}],
    }


def bake(base: Path, pattern: str, indices: Sequence[int]) -> None:
    """Write the frames of a sequence. Content is irrelevant; only the names are read."""
    for index in indices:
        frame = base / (pattern % index)
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"\x89PNG")


def rules_of(findings: Sequence[Finding]) -> list[str]:
    return [finding.rule for finding in findings]


# --- counting frames off disk -------------------------------------------------------------


def test_a_sequence_is_counted_from_its_lowest_frame(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 49))
    assert frames_on_disk(tmp_path / PATTERN) == (1, 48)


def test_a_sequence_numbered_from_zero_starts_at_zero(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(0, 10))
    assert frames_on_disk(tmp_path / PATTERN) == (0, 10)


def test_a_sequence_is_counted_only_as_far_as_it_is_contiguous(tmp_path: Path) -> None:
    """Resolve imports a start and an end index and reads everything between them, so a
    run with a hole in it is a broken sequence, not a shorter one."""
    bake(tmp_path, PATTERN, [1, 2, 3, 7, 8])
    assert frames_on_disk(tmp_path / PATTERN) == (1, 3)


def test_a_sequence_with_no_frames_on_disk_counts_none(tmp_path: Path) -> None:
    assert frames_on_disk(tmp_path / PATTERN) == (None, 0)


def test_frames_of_another_sequence_in_the_same_folder_are_not_counted(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 25))
    bake(tmp_path, "cards/sunset-boulevard/title_%04d.png", range(1, 200))
    assert frames_on_disk(tmp_path / PATTERN) == (1, 24)


def test_a_still_counts_one_frame_when_it_is_there(tmp_path: Path) -> None:
    card = tmp_path / "cards" / "title.png"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"\x89PNG")
    assert frames_on_disk(card) == (None, 1)


def test_a_still_that_is_not_there_counts_none(tmp_path: Path) -> None:
    assert frames_on_disk(tmp_path / "cards" / "title.png") == (None, 0)


# --- resolving an event's card ------------------------------------------------------------


def test_a_relative_asset_resolves_against_the_titles_file_not_the_cwd(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 25))
    card = resolve_asset(a_card(), "sunset-boulevard", base=tmp_path)
    assert card.path == tmp_path / PATTERN
    assert card.frames == 24


def test_an_absolute_asset_is_left_alone(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 25))
    absolute = str(tmp_path / PATTERN)
    card = resolve_asset(a_card(asset=absolute), "sunset-boulevard", base=Path("/elsewhere"))
    assert card.path == Path(absolute)
    assert card.frames == 24


def test_a_sequences_import_request_names_the_range_it_really_has(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 25))
    card = resolve_asset(a_card(), "sunset-boulevard", base=tmp_path)
    assert card.request() == {
        "FilePath": str(tmp_path / PATTERN),
        "StartIndex": 1,
        "EndIndex": 24,
    }


def test_a_stills_import_request_is_just_its_path(tmp_path: Path) -> None:
    card = tmp_path / "title.png"
    card.write_bytes(b"\x89PNG")
    resolved = resolve_asset(a_card(asset="title.png"), "sunset-boulevard", base=tmp_path)
    assert resolved.request() == str(card)
    assert not resolved.is_sequence


def test_a_sequences_identity_is_its_first_frame(tmp_path: Path) -> None:
    """The pattern is not a file and Resolve re-paths the clip to a folded label, so the
    first frame is the only address that is the same string on both sides of the import."""
    bake(tmp_path, PATTERN, range(7, 31))
    card = resolve_asset(a_card(), "sunset-boulevard", base=tmp_path)
    assert card.first_frame() == str(tmp_path / "cards/sunset-boulevard/personnel_0007.png")


def test_a_card_lands_in_the_song_bin_by_convention() -> None:
    assert bin_for(a_card(), "sunset-boulevard") == "04_Assets/Text/sunset-boulevard"


def test_an_explicit_bin_always_wins() -> None:
    assert bin_for(a_card(bin="Concert/Cards"), "sunset-boulevard") == "Concert/Cards"


# --- T10 and T11 --------------------------------------------------------------------------


def test_a_card_that_was_never_exported_is_t10(tmp_path: Path) -> None:
    findings, _ = validate_assets(a_doc(a_card()), base=tmp_path)
    assert rules_of(findings) == ["T10"]
    assert "personnel_%04d.png" in findings[0].message


def test_a_sequence_shorter_than_its_event_is_t11(tmp_path: Path) -> None:
    bake(tmp_path, PATTERN, range(1, 100))
    findings, _ = validate_assets(a_doc(a_card()), base=tmp_path)
    assert rules_of(findings) == ["T11"]
    assert "99 frame(s)" in findings[0].message and "asks for 240" in findings[0].message


def test_a_sequence_longer_than_its_event_is_t11_because_trimming_cuts_the_fade(
    tmp_path: Path,
) -> None:
    bake(tmp_path, PATTERN, range(1, 400))
    assert rules_of(validate_assets(a_doc(a_card()), base=tmp_path)[0]) == ["T11"]


def test_a_sequence_of_exactly_the_events_length_has_nothing_said_about_it(
    tmp_path: Path,
) -> None:
    bake(tmp_path, PATTERN, range(1, 241))
    findings, cards = validate_assets(a_doc(a_card()), base=tmp_path)
    assert findings == []
    assert cards["p01"].frames == 240


def test_a_still_is_freeze_extended_so_any_duration_fits(tmp_path: Path) -> None:
    (tmp_path / "title.png").write_bytes(b"\x89PNG")
    doc = a_doc(a_card(asset="title.png", out=9000))
    findings, cards = validate_assets(doc, base=tmp_path)
    assert findings == []
    assert cards["p01"].frames == 1


def test_a_still_asked_to_fade_is_t11_because_freezing_it_shows_no_ramp(
    tmp_path: Path,
) -> None:
    (tmp_path / "title.png").write_bytes(b"\x89PNG")
    doc = a_doc(a_card(asset="title.png", fade={"in": 24, "out": 24}))
    findings, _ = validate_assets(doc, base=tmp_path)
    assert rules_of(findings) == ["T11"]
    assert "one image" in findings[0].message


def test_a_textplus_event_is_not_an_asset_at_all(tmp_path: Path) -> None:
    doc = a_doc({"id": "t01", "kind": "title", "text": "Sunset", "in": 0, "out": 240})
    assert validate_assets(doc, base=tmp_path) == ([], {})
