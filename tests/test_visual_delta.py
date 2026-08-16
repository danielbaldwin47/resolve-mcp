"""The per-cut visual delta and the 30-degree-rule flag, on synthetic frames.

Real footage is the calibration (``gauntlet/recon/cut_delta_calib.py`` runs the
metric over the five human deliverables); this tier fixes the *decisions* — what
counts as a step across a cut, what counts as too small a one, and that the
numbers stay inside their stated ranges.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from resolve_mcp.analysis import records
from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.video import framing


def stage(
    *,
    center: float = 0.5,
    width: float = 0.3,
    height: float = 0.6,
    level: float = 0.8,
    floor: float = 0.05,
    texture: float = 0.0,
    seed: int = 7,
) -> NDArray[np.uint8]:
    """One synthetic concert frame: a lit block on a dark ground.

    ``center``/``width``/``height`` are fractions of the frame, so a tighter shot of
    the same subject is the same call with a bigger ``width`` — which is what a cut
    that changes shot size looks like to this metric.
    """
    rows, cols = framing.GRID_HEIGHT, framing.GRID_WIDTH
    frame = np.full((rows, cols), floor, dtype=np.float64)
    half = width / 2.0
    left, right = int((center - half) * cols), int((center + half) * cols)
    top = int((1.0 - height) * rows)
    frame[top:, max(0, left) : max(0, right)] = level
    if texture:
        noise = np.random.default_rng(seed).normal(0.0, texture, frame.shape)
        frame = frame + noise
    return np.asarray(np.clip(frame * 255.0, 0, 255).astype(np.uint8))


def test_distinct_angles_score_high() -> None:
    """A cut between two different pictures is a real step, and is not flagged."""
    out = stage(center=0.3, width=0.2, height=0.5, level=0.75, texture=0.04)
    into = stage(center=0.75, width=0.5, height=0.9, level=0.35, floor=0.15, texture=0.01)

    reading = framing.read_pair(out, into)

    assert reading.delta > framing.JUMP_DELTA
    assert reading.jump_cut is False
    assert reading.reason == ""


def test_same_framing_nudged_is_a_jump_cut() -> None:
    """Same subject, a few pixels of reframe: the 30-degree rule's failure case."""
    out = stage(center=0.5, texture=0.03)
    into = stage(center=0.53, texture=0.03)

    reading = framing.read_pair(out, into)

    assert reading.delta < framing.JUMP_DELTA
    assert reading.jump_cut is True
    assert reading.reason
    assert reading.shift_x != 0


def test_identical_frames_are_a_jump_cut_with_no_step() -> None:
    """The degenerate case — the same angle twice — scores at the floor."""
    frame = stage(texture=0.02)

    reading = framing.read_pair(frame, frame)

    assert reading.delta == pytest.approx(0.0, abs=0.02)
    assert reading.jump_cut is True
    assert reading.shift_x == 0
    assert reading.shift_y == 0


def test_a_big_size_change_rescues_the_same_axis() -> None:
    """Cutting along the same axis is legal when the shot size really changes.

    The 30-degree rule's own escape clause: angle *or* size. A wide and a tight of
    the same subject share a centre line, so the layout term stays modest — the
    size term is what has to carry the cut.
    """
    wide = stage(center=0.5, width=0.18, height=0.35, texture=0.05)
    tight = stage(center=0.5, width=0.85, height=0.95, texture=0.01)

    reading = framing.read_pair(wide, tight)

    assert reading.scale > 0.3
    assert reading.delta > framing.JUMP_DELTA
    assert reading.jump_cut is False


def test_two_pictures_with_the_same_marginals_are_still_a_step() -> None:
    """The failure the structure term exists for.

    Bright patches at opposite corners give identical row profiles and identical
    column profiles, so a measurement made of marginals alone calls these two frames
    the same picture. They share no arrangement at all, and a club stage is the worst
    case for this — every camera in the room shares one bright band across the middle.
    """
    rows, cols = framing.GRID_HEIGHT, framing.GRID_WIDTH
    out = np.zeros((rows, cols), dtype=np.uint8)
    into = np.zeros((rows, cols), dtype=np.uint8)
    half_r, half_c = rows // 2, cols // 2
    out[:half_r, :half_c] = 200
    out[half_r:, half_c:] = 200
    into[:half_r, half_c:] = 200
    into[half_r:, :half_c] = 200

    reading = framing.read_pair(out, into)

    assert reading.layout == pytest.approx(0.0, abs=0.05)
    assert reading.structure > 0.9
    assert reading.jump_cut is False


def test_a_picture_slid_further_than_a_reframe_is_not_matched() -> None:
    """Beyond the search, two pictures are scored where they sit, not where they'd fit."""
    rows, cols = framing.GRID_HEIGHT, framing.GRID_WIDTH
    out = np.zeros((rows, cols), dtype=np.uint8)
    out[5:25, 10:26] = 220
    into = np.asarray(np.roll(out, (rows // 2, cols // 2), axis=(0, 1)))

    reading = framing.read_pair(out, into)

    assert reading.layout > 0.9
    assert reading.jump_cut is False


def test_a_best_match_at_the_limit_of_the_search_is_refused_rather_than_reported() -> None:
    """A lag pegged at the boundary means the search ran out of room, not that it won.

    The bar moves exactly ``MAX_SHIFT`` of the width, so the horizontal peak sits on
    the edge of the search and is refused; the vertical profiles are identical, so
    that axis reports its real zero. Reporting the pegged lag would publish a shift
    the module's own docstring calls the signature of a jump cut.
    """
    rows, cols = framing.GRID_HEIGHT, framing.GRID_WIDTH
    step = int(round(cols * framing.MAX_SHIFT))
    out = np.zeros((rows, cols), dtype=np.uint8)
    out[5:60, 10 : 10 + step] = 220
    into = np.zeros((rows, cols), dtype=np.uint8)
    into[5:60, 10 + step : 10 + 2 * step] = 220

    reading = framing.read_pair(out, into)

    assert reading.shift_x is None
    assert reading.shift_y == 0


def test_every_term_stays_inside_its_range() -> None:
    """Each term is a fraction, and the composite is one too."""
    pairs = [
        (stage(), stage(center=0.8)),
        (stage(level=0.0, floor=0.0), stage(level=1.0, floor=1.0)),
        (stage(texture=0.2, seed=1), stage(texture=0.2, seed=2)),
    ]
    for out, into in pairs:
        reading = framing.read_pair(out, into)
        for term in (
            reading.content,
            reading.layout,
            reading.structure,
            reading.scale,
            reading.delta,
        ):
            assert 0.0 <= term <= 1.0


def test_flat_frames_do_not_divide_by_zero() -> None:
    """Two frames with no picture in them at all: no step, no crash."""
    black = np.zeros((framing.GRID_HEIGHT, framing.GRID_WIDTH), dtype=np.uint8)

    reading = framing.read_pair(black, black)

    assert reading.layout == 0.0
    assert reading.delta == pytest.approx(0.0, abs=1e-6)
    assert reading.jump_cut is True


def test_a_cut_to_black_is_not_a_jump_cut() -> None:
    """A blackout is a different picture, however textureless it is."""
    lit = stage(texture=0.03)
    black = np.zeros((framing.GRID_HEIGHT, framing.GRID_WIDTH), dtype=np.uint8)

    reading = framing.read_pair(lit, black)

    assert reading.content > 0.5
    assert reading.jump_cut is False


def test_read_boundary_skips_the_transition_frames() -> None:
    """The boundary read takes its frames clear of the blend, not against it."""
    out = stage(center=0.3, width=0.2, texture=0.03)
    into = stage(center=0.75, width=0.5, level=0.35, texture=0.03)
    blend = np.asarray(((out.astype(np.float64) + into) / 2.0).astype(np.uint8))

    window = np.stack([out] * 6 + [blend] + [into] * 6)
    reading = framing.read_boundary(window, 7)

    direct = framing.read_pair(out, into)
    assert reading.delta == pytest.approx(direct.delta, abs=0.05)


def test_read_across_reads_a_dissolve_by_its_endpoints() -> None:
    """A ramp knows where its own ends are; the reading uses them, not a fixed guard.

    Read with the guard alone, a twelve-frame dissolve puts blend frames on both
    sides and the two shots read as versions of each other. Read across the ramp,
    the same cut reads as the step it is.
    """
    out = stage(center=0.25, width=0.2, texture=0.03)
    into = stage(center=0.8, width=0.55, level=0.4, texture=0.03)
    ramp = [
        np.asarray(((1 - w) * out.astype(np.float64) + w * into).astype(np.uint8))
        for w in np.linspace(0.1, 0.9, 8)
    ]
    window = np.stack([out] * 5 + ramp + [into] * 5)

    across = framing.read_across(window, 5, 13)
    inside = framing.read_boundary(window, 9)

    assert across.delta == pytest.approx(framing.read_pair(out, into).delta, abs=0.05)
    assert across.delta > inside.delta


def test_read_boundary_refuses_a_window_without_room() -> None:
    """Too few frames either side is a refusal, not a reading off one frame."""
    window = np.stack([stage()] * 3)

    with pytest.raises(ValueError):
        framing.read_boundary(window, 1)


def test_a_reading_serialises_to_plain_json_types() -> None:
    """The reading travels in a manifest and a cut record, so it has to be plain."""
    reading = framing.read_pair(stage(), stage(center=0.8))

    record = reading.as_record()

    assert set(record) == {
        "delta",
        "content",
        "layout",
        "structure",
        "scale",
        "shift_x",
        "shift_y",
        "jump_cut",
        "reason",
    }
    assert all(one is None or isinstance(one, float | int | bool | str) for one in record.values())


def a_catalog(tmp_path: Path, *rows: dict[str, Any], name: str = "hand.json") -> Path:
    """A catalog written past the interface, the way a hand-edited file arrives."""
    return records.write(
        tmp_path / name, {"kind": framing.CATALOG_KIND, "count": len(rows)}, "cuts", list(rows)
    )


def test_a_catalog_round_trips_through_write_and_read(tmp_path: Path) -> None:
    """The record is the seam: what the writer measured is what the reader gets back."""
    reading = framing.read_pair(stage(), stage(center=0.8))
    path = framing.write_catalog(
        tmp_path / "cuts.json",
        [framing.Cut(t=1.033, reading=reading), framing.Cut(t=2.483, reading=None)],
    )

    back = framing.read_catalog(path)

    assert [one.t for one in back] == [1.033, 2.483]
    assert back[0].reading == reading
    assert back[1].reading is None


def test_a_catalog_is_read_back_in_time_order(tmp_path: Path) -> None:
    """A catalog is a timeline, so the reader sorts it rather than trusting the writer."""
    reading = framing.read_pair(stage(), stage(center=0.8))
    path = framing.write_catalog(
        tmp_path / "cuts.json",
        [framing.Cut(t=4.0, reading=reading), framing.Cut(t=1.0, reading=reading)],
    )

    assert [one.t for one in framing.read_catalog(path)] == [1.0, 4.0]


def test_a_catalog_row_carries_the_columns_its_writer_added(tmp_path: Path) -> None:
    """The pack writes a transition typing and a filmstrip reference on the same row.

    Those belong to the writer, not to this measurement, and the interface has to carry
    them through untouched or the pack cannot use it for its own file.
    """
    reading = framing.read_pair(stage(), stage(center=0.8))
    path = framing.write_catalog(
        tmp_path / "cuts.json",
        [framing.Cut(t=1.0, reading=reading, extra={"index": 1, "transition": {"type": "hard"}})],
        header={"label": "human"},
    )

    back = framing.read_catalog(path)
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert back[0].extra == {"index": 1, "transition": {"type": "hard"}}
    assert doc["kind"] == framing.CATALOG_KIND
    assert doc["count"] == 1
    assert doc["label"] == "human"


def test_the_two_columns_a_row_owns_are_written_over_its_writer_s(tmp_path: Path) -> None:
    """A writer that already keeps its own ``t`` or ``delta`` does not get to mean by them.

    Silently letting either through would put a number this module never measured under the
    name every reader of a catalog trusts.
    """
    reading = framing.read_pair(stage(), stage(center=0.8))
    path = framing.write_catalog(
        tmp_path / "cuts.json",
        [framing.Cut(t=1.0, reading=reading, extra={"t": 9.0, "delta": 0.99})],
    )

    back = framing.read_catalog(path)

    assert back[0].t == 1.0
    assert back[0].reading == reading
    assert back[0].extra == {}


def test_a_row_that_spreads_the_reading_across_itself_is_refused(tmp_path: Path) -> None:
    """One shape, said out loud.

    A bare number under ``delta`` is the flat catalog a hand-rolled writer used to leave,
    and reading it as an unread boundary would report a measured cut as a hole nobody
    notices. The refusal names the writer that fixes it.
    """
    path = a_catalog(tmp_path, {"t": 1.0, "delta": 0.62, "jump_cut": False})

    with pytest.raises(InvalidRequestError, match="bare float") as refused:
        framing.read_catalog(path)

    assert "write_catalog" in refused.value.fix


def test_a_reading_missing_a_term_reads_as_a_hole_rather_than_a_crash(tmp_path: Path) -> None:
    """An older or hand-edited record answers with what it holds, from the tool call.

    A ``KeyError`` out of a worker two seconds later is the one answer nobody can act on.
    """
    path = a_catalog(tmp_path, {"t": 1.0, "delta": {"delta": 0.08, "jump_cut": True}})

    reading = framing.read_catalog(path)[0].reading

    assert reading is not None
    assert (reading.delta, reading.jump_cut) == (0.08, True)
    assert (reading.layout, reading.shift_x, reading.reason) == (0.0, None, "")


def test_an_unread_boundary_is_written_as_one(tmp_path: Path) -> None:
    """A boundary the reader refused is a null on disk, not an absent row.

    The count of cuts is the pack's own; dropping the row would report a shorter edit.
    """
    path = framing.write_catalog(tmp_path / "cuts.json", [framing.Cut(t=1.0, reading=None)])

    doc = json.loads(path.read_text(encoding="utf-8"))

    assert doc["cuts"] == [{"t": 1.0, "delta": None}]
