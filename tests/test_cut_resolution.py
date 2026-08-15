"""The delivery resolution: what the rules refuse, what the build writes, what it reads back.

One device over four modules. :mod:`resolve_mcp.cut.resolution` is the reading;
``cut/validate`` refuses a block that would not read; :mod:`resolve_mcp.resolve.settings`
puts a timeline on the size and proves it took; and the build applies it before the first
append — and again after a tail round trip, because the import is a new timeline born at
the project's default like any other.

The failure being designed against is quiet at every one of those seams. A timeline created
in the corpus project is 4K; every deliverable is 1080p; ``SetSetting`` answers the same
whether it changed anything or not. So a build that trusted the return value would hand back
"built at 1920x1080" over a 4K timeline, and the only place the difference would surface is
the render — after the file exists (gauntlet G13, #187).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.cut import resolution as cut_resolution
from resolve_mcp.cut.validate import validate_structure
from resolve_mcp.resolve import settings
from resolve_mcp.resolve.settings import CUSTOM_SETTINGS, HEIGHT, WIDTH
from resolve_mcp.tools.cut import build_timeline, validate_cut

from .conftest import Attach
from .cutfile import a_cut, a_pool, built, empty_project, valid_doc
from .fakes import FakeTimeline

HD = {"width": 1920, "height": 1080}
"""What the corpus delivers, against a project that creates timelines at 4K."""


def at(**resolution: Any) -> dict[str, Any]:
    """The shared three-shot cut, stating the size it is for."""
    doc = valid_doc()
    doc["timeline"]["resolution"] = resolution
    return doc


def rules(doc: dict[str, Any]) -> list[str]:
    return [finding.rule for finding in validate_structure(doc)]


def messages(doc: dict[str, Any], rule: str) -> list[str]:
    return [finding.message for finding in validate_structure(doc) if finding.rule == rule]


# --- the rules ------------------------------------------------------------------------------


def test_a_delivery_resolution_validates_clean() -> None:
    assert rules(at(**HD)) == []


def test_a_cut_that_says_nothing_is_still_the_ordinary_shape() -> None:
    """Every cut written before this device stays valid and stays on the project's size."""
    assert rules(valid_doc()) == []
    assert cut_resolution.read(valid_doc()) is None


def test_the_reading_is_the_frame_the_cut_asked_for() -> None:
    assert cut_resolution.read(at(**HD)) == cut_resolution.Resolution(1920, 1080)


def test_a_resolution_that_is_not_an_object_is_unreadable() -> None:
    doc = valid_doc()
    doc["timeline"]["resolution"] = "1920x1080"

    assert rules(doc) == ["E1"]


@pytest.mark.parametrize("side", ["width", "height"])
def test_half_a_frame_size_is_refused(side: str) -> None:
    """A width with no height is a half-stated delivery, and the other half is not guessable."""
    stated = dict(HD)
    del stated[side]

    assert "E1" in rules(at(**stated))
    assert side in messages(at(**stated), "E1")[0]


@pytest.mark.parametrize("value", ["1920", 1920.0, True, None])
def test_a_side_that_is_not_an_integer_is_refused(value: Any) -> None:
    """The API is string-typed, so ``"1920"`` looks harmless — and it is the author's slip.

    Booleans go with it: ``True`` is an ``int`` to Python and 1 pixel to Resolve.
    """
    assert "E1" in rules(at(width=value, height=1080))


@pytest.mark.parametrize("value", [0, -1080, 15, 16385])
def test_an_implausible_side_is_refused_as_a_typo(value: int) -> None:
    assert "E1" in rules(at(width=1920, height=value))


def test_an_unknown_resolution_key_is_refused_rather_than_ignored() -> None:
    """``w``/``h`` would leave the timeline at 4K and report a successful build."""
    doc = at(width=1920, height=1080, pixel_aspect=1.0)

    assert "E1" in rules(doc)
    assert "pixel_aspect" in messages(doc, "E1")[0]


def test_the_dry_run_reports_it_before_resolve_is_touched(
    attach: Attach, tmp_path: Path
) -> None:
    attach(empty_project(a_pool()))

    result = validate_cut(a_cut(tmp_path, at(width=1920, height=0)))

    assert result["ok"] is True
    assert [error["rule"] for error in result["errors"]] == ["E1"]


# --- the settings wrapper -------------------------------------------------------------------


def test_the_flag_goes_first_or_the_size_does_not_land() -> None:
    """``useCustomSettings`` detaches the timeline from the project's settings.

    The resolution keys are inert while it is off — the write is taken and the timeline
    stays where it was — so the order here is the whole call, not a nicety.
    """
    timeline = FakeTimeline("sunset-set v1", resolution=("3840", "2160"))

    settings.apply_resolution(timeline, cut_resolution.Resolution(1920, 1080), "sunset-set v1")

    assert [key for key, _ in timeline.setting_writes] == [CUSTOM_SETTINGS, WIDTH, HEIGHT]
    assert timeline.GetSetting(WIDTH) == "1920"
    assert timeline.GetSetting(HEIGHT) == "1080"


def test_the_settings_round_trip_as_the_strings_the_api_speaks() -> None:
    """Written as strings, read back as strings, reported as the integers they mean."""
    timeline = FakeTimeline("sunset-set v1", resolution=("3840", "2160"))

    settings.apply_resolution(timeline, cut_resolution.Resolution(1920, 1080), "sunset-set v1")

    assert timeline.setting_writes == [(CUSTOM_SETTINGS, "1"), (WIDTH, "1920"), (HEIGHT, "1080")]
    assert settings.read_resolution(timeline) == cut_resolution.Resolution(1920, 1080)


@pytest.mark.parametrize("answer", ["  1920  ", "1920"])
def test_a_setting_padded_the_way_a_c_bridge_pads_it_still_reads(answer: str) -> None:
    """The values cross a C bridge, and nothing promises they arrive trimmed."""
    timeline = FakeTimeline("sunset-set v1", resolution=(answer, "1080"))

    assert settings.read_resolution(timeline) == cut_resolution.Resolution(1920, 1080)


@pytest.mark.parametrize("answer", ["", "1920x1080", "auto"])
def test_a_setting_that_is_not_a_number_reads_as_nothing_rather_than_a_guess(
    answer: str,
) -> None:
    """An unreadable size is "will not say" — the build then fails rather than assuming."""
    timeline = FakeTimeline("sunset-set v1", resolution=(answer, "1080"))

    assert settings.read_resolution(timeline) is None


def test_a_timeline_that_will_not_take_the_size_fails_rather_than_reporting_it() -> None:
    """The one outcome worse than not offering the setting: claiming a size nothing has."""
    timeline = FakeTimeline("sunset-set v1", resolution=("3840", "2160"))
    timeline.settings_that_ignore_writes = {WIDTH}

    with pytest.raises(Exception) as raised:  # noqa: PT011 - the message is what is under test
        settings.apply_resolution(timeline, cut_resolution.Resolution(1920, 1080), "sunset-set v1")

    assert "3840x1080" in str(raised.value)


def test_a_timeline_that_says_nothing_about_its_size_reads_as_nothing() -> None:
    assert settings.read_resolution(FakeTimeline("sync reference")) is None


# --- the build ------------------------------------------------------------------------------


def test_the_built_timeline_carries_the_stated_resolution(
    attach: Attach, tmp_path: Path
) -> None:
    """The end of G13: a 1080p cut in a 4K project, with no hand step before the render."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, at(**HD)))

    assert result["ok"] is True, result.get("error")
    assert result["timeline"]["resolution"] == HD
    assert built(resolve, "sunset-set v1").GetSetting(CUSTOM_SETTINGS) == "1"


def test_a_build_that_states_nothing_stays_on_the_project_default(
    attach: Attach, tmp_path: Path
) -> None:
    """v1 behaviour, unchanged: the project decides, and the report says which size that was."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["timeline"]["resolution"] == {"width": 3840, "height": 2160}
    assert built(resolve, "sunset-set v1").setting_writes == []


def test_the_size_is_set_before_the_first_append(attach: Attach, tmp_path: Path) -> None:
    """A timeline that took the shots at 4K and was resized after is not the same edit."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, at(**HD)))

    landed = built(resolve, "sunset-set v1")
    assert landed.setting_writes_at_item_count == [0, 0, 0]


def test_a_round_tripped_cut_is_resized_after_the_import(
    attach: Attach, tmp_path: Path
) -> None:
    """The import is a new timeline, born at the project's 4K like any other.

    The staging timeline's own settings do not travel in the OTIO document, so a build that
    set the size once would round-trip a tail straight back to the project default — and the
    tail is exactly the shape the corpus delivers.
    """
    resolve = empty_project(a_pool())
    attach(resolve)
    doc = at(**HD)
    doc["tail"] = {"type": "dissolve_to_black", "duration_frames": 40, "audio_fade_frames": 35}

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True, result.get("error")
    assert result["tail"]["route"] == "otio_round_trip"
    assert result["timeline"]["resolution"] == HD
    landed = settings.read_resolution(built(resolve, "sunset-set v1"))
    assert landed == cut_resolution.Resolution(1920, 1080)


def test_a_build_whose_size_will_not_land_fails_instead_of_delivering_4k(
    attach: Attach, tmp_path: Path
) -> None:
    pool = a_pool()
    pool.new_timeline_settings_that_ignore_writes = {HEIGHT}
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, at(**HD)))

    assert result["ok"] is False
    assert "1920x2160" in result["error"]["cause"]
