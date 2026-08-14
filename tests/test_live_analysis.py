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

from resolve_mcp.analysis import applause, bars, beats

from .fakes import write_clicks, write_hits, write_sections

CLICK_SECONDS = 20.0
CLICK_BPM = 120.0

APPLAUSE_SECONDS = 8.0
ROOM_SECONDS = 6.0

BAR_METER = 4
BAR_SECONDS = 60.0 / CLICK_BPM * BAR_METER


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
def test_a_bar_map_over_the_real_model_and_the_real_accents_finds_the_downbeat(
    tmp_path: Path,
) -> None:
    """The two seams the fake tier cannot join: the installed model, and RMS off real audio.

    Every decision in ``bars`` is checked against grids and readings written by hand. What
    that cannot show is whether the accent reading, run over a file the *model* also read,
    ranks the same beats the arithmetic was tested on — a window off by a beat, or a decoder
    handing back a different length than the grid was measured in, is right or wrong on the
    first real run and nowhere before it.

    A click track with a loud one, which is the meter no ear has to arbitrate.
    """
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    step = 60.0 / CLICK_BPM
    times = [round(index * step, 6) for index in range(int(CLICK_SECONDS / step))]
    path = write_hits(
        tmp_path / "accented.wav",
        times,
        seconds=CLICK_SECONDS,
        accents=[1.0 if index % BAR_METER == 0 else 0.3 for index in range(len(times))],
    )

    grid = beats.numbered(beats.detect(path))
    salience = bars.accents(path, [float(row["t"]) for row in grid])
    found = bars.mapped(grid, salience)

    assert found.source in (bars.MODEL, bars.INFERRED), found.reasons
    assert found.meter == BAR_METER
    # The loud clicks are two seconds apart; the map has to put its bar lines on them.
    assert [one.t for one in found.bars][:3] == [
        pytest.approx(index * BAR_SECONDS, abs=0.1) for index in range(3)
    ]
    assert [one.in_group for one in found.bars][:5] == [1, 2, 3, 4, 1]


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
