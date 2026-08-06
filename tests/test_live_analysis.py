"""The one analysis path no fake can stand in for: the real model, actually loaded.

ADR 0002 injects each model at a seam, which makes every decision around them testable and
leaves exactly one thing unverified per model — whether the default detector calls the
installed package correctly. Return arity, units and frame rates are the kind of thing that
is right or wrong on the first real run and nowhere before it, so they get live tests rather
than nothing, in the spirit of ADR 0001's interpreter guard.

Runs only under ``-m live``, and skips even there if the model is not installed — the point
is to be run on the machine that has it, and to say so on the ticket when it has not been.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.analysis import applause, beats

from .fakes import write_clicks, write_sections

CLICK_SECONDS = 20.0
CLICK_BPM = 120.0

APPLAUSE_SECONDS = 8.0
ROOM_SECONDS = 6.0


@pytest.mark.live
def test_the_installed_beat_model_hears_a_click_track(tmp_path: Path) -> None:
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    path = write_clicks(tmp_path / "clicks.wav", beats_per_minute=CLICK_BPM, seconds=CLICK_SECONDS)

    grid = beats.detect(path)
    found = beats.gist(grid, beats.numbered(grid))

    # Times in seconds, not frames or samples: the unit is what a wrong call gets wrong.
    assert all(0.0 <= one <= CLICK_SECONDS for one in grid.beats)
    assert found["tempo_bpm"] == pytest.approx(CLICK_BPM, abs=10.0)
    # Downbeats are the second of the two lists the model returns, and a subset of the first.
    assert grid.downbeats
    assert set(grid.downbeats) <= set(grid.beats)


@pytest.mark.live
def test_the_installed_applause_tagger_returns_a_curve_over_the_whole_file(
    tmp_path: Path,
) -> None:
    """Not whether PANNs is right about a synthetic room — whether the call is right.

    What a wrong call gets wrong is the shape: a curve in frames rather than seconds, a
    curve covering only the first chunk, or probabilities that are logits. Those are all
    visible on any audio at all, and none of them are visible without the model installed.
    """
    pytest.importorskip(applause.MODULE, reason="panns_inference is not installed")
    path = write_sections(
        tmp_path / "room.wav",
        (("tone", APPLAUSE_SECONDS), ("noise", ROOM_SECONDS), ("tone", APPLAUSE_SECONDS)),
        sample_rate=44_100,
    )
    total = APPLAUSE_SECONDS * 2 + ROOM_SECONDS

    curve = applause.tag(path)

    assert curve.seconds and len(curve.seconds) == len(curve.probability)
    assert all(0.0 <= one <= 1.0 for one in curve.probability)
    assert curve.seconds[0] < 1.0
    assert curve.seconds[-1] == pytest.approx(total, abs=1.0)
