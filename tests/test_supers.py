"""Burned-in graphics: what carries across a picture change, and what a cut lands on.

The fixtures build the two things this measurement has to tell apart — a caption drawn on
the frame, and a stage that happens to hold still — rather than decoding anything. What a
real render looks like through this path is a calibration, not a test:
``gauntlet/recon/super_scan.py``, receipt in ``super_scan.json``.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from resolve_mcp.video import framing, supers

HEIGHT, WIDTH = supers.GRID_HEIGHT, supers.GRID_WIDTH

FRAMINGS = ((0.25, 0.20), (0.70, 0.65), (0.20, 0.75), (0.75, 0.25), (0.50, 0.45))
"""Where the lit subject sits, cycled by seed. Five of them, and five is prime, so any two
seeds a test puts either side of a cut land on different framings — a run of frames that
quietly repeated a framing would be a run the reading is right to refuse."""


def picture(seed: int) -> NDArray[np.uint8]:
    """One camera's frame: a dim room with a lit subject somewhere in it.

    Deliberately smooth, and deliberately *composed*. Smooth because a super is found by
    standing out from its surroundings, so a frame of white noise would be a frame of
    supers. Composed because the reading refuses any pair whose picture did not really
    change, and two frames have to differ the way two angles differ — a moved subject —
    rather than the way two exposures do.
    """
    rows = np.linspace(0.0, 1.0, HEIGHT)[:, None]
    cols = np.linspace(0.0, 1.0, WIDTH)[None, :]
    down, across = FRAMINGS[seed % len(FRAMINGS)]
    subject = np.exp(-(((cols - across) ** 2) / 0.02 + ((rows - down) ** 2) / 0.04))
    field = 22 + 130 * subject + 12 * np.sin(rows * 6.0 + seed)
    frame: NDArray[np.uint8] = np.clip(field, 0, 255).astype(np.uint8)
    return frame


def caption(
    frame: NDArray[np.uint8],
    top: int = 344,
    left: int = 120,
    level: int = 235,
    strokes: int = 26,
) -> NDArray[np.uint8]:
    """The same frame with lettering burned into it — thin bright strokes on one line."""
    out = frame.copy()
    for stroke in range(strokes):
        at = left + stroke * 9
        out[top : top + 16, at : at + 3] = level
    return out


def held_still(frame: NDArray[np.uint8], count: int) -> NDArray[np.uint8]:
    return np.repeat(frame[None, :, :], count, axis=0)


def test_the_fixtures_are_different_pictures_and_not_merely_different_frames() -> None:
    """The premise every test below rests on. The reading refuses a pair whose composition
    barely moved, so a fixture that quietly stopped moving it would turn these into tests
    of the refusal — all still green, all measuring nothing."""
    steps = [
        framing.read_pair(picture(one)[::6, ::6], picture(one + gap)[::6, ::6]).delta
        for gap in (1, 2, 3)
        for one in range(12)
    ]

    assert min(steps) > supers.STEP


def test_a_caption_carries_across_a_picture_change() -> None:
    before = caption(picture(0))
    after = caption(picture(2))

    reading = supers.read_pair(before, after)

    assert reading.kind == supers.OVERLAY
    assert reading.found
    assert len(reading.regions) == 1
    box = reading.regions[0]
    assert 0.80 < box.top < 0.88
    assert 0.86 < box.bottom < 0.94


def test_a_picture_change_with_nothing_burned_in_carries_nothing() -> None:
    reading = supers.read_pair(picture(0), picture(2))

    assert reading.kind == supers.ABSENT
    assert not reading.found
    assert reading.regions == ()


def test_a_still_picture_is_refused_rather_than_read() -> None:
    """The failure this refusal exists for: on locked-off cameras in a dark room every
    static highlight agrees with itself, and a reading taken there reports the furniture."""
    frame = picture(0)
    nudged = frame.copy()
    nudged[:20] = np.clip(nudged[:20].astype(np.int16) + 30, 0, 255).astype(np.uint8)

    reading = supers.read_pair(frame, nudged)

    assert reading.kind == supers.UNREAD
    assert not reading.found
    assert supers.CHANGED < reading.agreement < supers.HELD


def test_a_frame_held_whole_with_lettering_on_it_is_a_card() -> None:
    card = caption(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), top=180, left=200, strokes=14)

    reading = supers.read_pair(card, card)

    assert reading.kind == supers.CARD
    assert reading.found


def test_a_frozen_picture_with_fine_detail_in_it_reads_as_a_card_too() -> None:
    """A known confusion, pinned rather than described. A freeze frame is held, and any
    bright fine detail in it — a lit music stand, a cymbal edge — is detailed in exactly the
    way lettering is. Nothing in this reading separates the two, so a deliverable that
    freezes on a picture says ``card``, and whoever reads that has to know it can mean
    either. A frozen picture with nothing fine in it stays ``absent``."""
    frozen = caption(picture(0), top=100, left=300, strokes=8)

    assert supers.read_pair(frozen, frozen).kind == supers.CARD
    assert supers.read_pair(picture(0), picture(0)).kind == supers.ABSENT


def test_a_held_black_frame_with_nothing_on_it_is_not_a_card() -> None:
    """A gap is not a title card, and the difference is whether anything is written on it."""
    black = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)

    reading = supers.read_pair(black, black)

    assert reading.kind == supers.ABSENT
    assert reading.agreement >= supers.HELD


def test_a_scatter_of_coincidences_is_not_a_graphic() -> None:
    """Two shots agreeing on a handful of specular highlights corner to corner: too small
    to be lettering and too spread out to be a caption."""
    before, after = picture(0), picture(2)
    for row, col in ((10, 10), (12, 700), (390, 20), (395, 690)):
        for frame in (before, after):
            frame[row : row + 3, col : col + 3] = 240

    reading = supers.read_pair(before, after)

    assert reading.regions == ()


def test_a_run_reports_the_span_the_caption_is_up_for() -> None:
    frames = np.stack(
        [picture(i) for i in range(4)]
        + [caption(picture(i)) for i in range(4, 12)]
        + [picture(i) for i in range(12, 16)]
    )

    spans = supers.read_run(frames, lags=(3,))

    assert len(spans) == 1
    assert spans[0].kind == supers.OVERLAY
    assert (spans[0].first, spans[0].last) == (4, 11)


def jittered(frame: NDArray[np.uint8], seed: int) -> NDArray[np.uint8]:
    """The same shot again, a second later: the same picture, none of the same pixels.

    What a locked-off camera in a dark room actually hands over. Pixel agreement collapses
    while the composition does not move at all, which is why agreement alone cannot be
    allowed to prove anything.
    """
    noise = np.random.default_rng(seed).integers(-5, 6, size=frame.shape)
    shot: NDArray[np.uint8] = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return shot


def test_a_bright_still_thing_in_a_still_shot_is_not_a_super() -> None:
    """The measurement's whole reason for the composition test. Here the "caption" is the
    lit keyboard of a piano with a maker's name across it: bright, static, in the same
    place in every reading, inside one shot that never changes. It persists perfectly, and
    it is still not a graphic."""
    frames = np.stack([caption(jittered(picture(0), seed)) for seed in range(8)])

    reading = supers.read_pair(frames[0], frames[3])
    assert reading.kind == supers.OVERLAY  # the pair alone is fooled...
    assert supers.read_run(frames, lags=(3,)) == ()  # ...and the span is not


def test_pixels_that_agree_somewhere_new_every_time_are_not_a_super() -> None:
    """The false positive this whole reading turns on: on a dark stage two frames of the
    same shot disagree pixel by pixel from noise alone, so something bright and static
    carries across every reading — but never the *same* something twice."""
    spots = ((40, 60), (110, 300), (180, 90), (250, 420), (320, 150), (60, 480), (200, 200))
    # Each frame carries the coincidence it shares backwards and the one it will share
    # forwards, so every pair agrees on a patch, and no two pairs agree on the same patch.
    frames = np.stack(
        [
            caption(
                caption(picture(i), top=spots[i][0], left=spots[i][1], strokes=10),
                top=spots[i + 2][0],
                left=spots[i + 2][1],
                strokes=10,
            )
            for i in range(5)
        ]
    )

    assert supers.read_run(frames, lags=(2,), bridge=0) == ()


def test_a_caption_that_does_not_outlast_the_lag_is_not_seen() -> None:
    """The price of needing no cut list, said out loud: the reading is a pair of frames a
    lag apart, so a super shorter than the lag is never held at both ends of one."""
    frames = np.stack([picture(i) for i in range(4)] + [caption(picture(4))] + [picture(5)])

    assert supers.read_run(frames, lags=(3,)) == ()


def test_a_lag_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="reads a frame against itself"):
        supers.read_run(np.stack([picture(0), picture(1)]), lags=(0,))


def test_a_short_super_needs_the_short_lag_to_be_seen_twice() -> None:
    """Why the scan reads two distances. A card is the shortest super there is, and at a
    lag near its own length it fits into a single reading — which the same-pixels-twice
    rule will not take. Read from closer in, the same card is seen three times and stands.

    The other half of that trade only shows on real footage: a second apart, a locked-off
    shot has not moved enough to disagree with itself and the overlay reading refuses the
    pair as too still. These fixtures reframe on every frame, so they cannot pose it.
    """
    card = caption(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), top=180, left=200, strokes=14)
    frames = np.concatenate(
        [
            held_still(card, 4),
            np.stack([picture(i) for i in range(4, 8)]),
            np.stack([caption(picture(i)) for i in range(8, 16)]),
        ]
    )

    near = supers.read_run(frames, lags=(1,))
    far = supers.read_run(frames, lags=(4,))
    both = supers.read_run(frames, lags=(1, 4))

    assert supers.CARD in [one.kind for one in near]
    assert supers.CARD not in [one.kind for one in far]
    assert sorted({one.kind for one in both}) == [supers.CARD, supers.OVERLAY]


def test_a_gap_of_doubt_inside_one_super_does_not_split_it() -> None:
    """One long locked-off shot in the middle of a lower third is an unread pair, not the
    end of the graphic."""
    frames = np.stack(
        [caption(picture(0)), caption(picture(1))]
        + [caption(picture(1))] * 2
        + [caption(picture(6)), caption(picture(7))]
    )

    spans = supers.read_run(frames, lags=(1,), bridge=2)

    assert len(spans) == 1
    assert (spans[0].first, spans[0].last) == (0, 5)


def test_the_edges_walk_out_to_the_frames_the_graphic_holds_between() -> None:
    window = np.stack(
        [picture(i) for i in range(3)]
        + [caption(picture(i)) for i in range(3, 9)]
        + [picture(i) for i in range(9, 12)]
    )
    mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    mask[344:360, 120:354] = caption(np.zeros((HEIGHT, WIDTH), dtype=np.uint8))[
        344:360, 120:354
    ] > 200

    found = supers.edges(window, mask, anchor=5)

    assert (found.first, found.last) == (3, 8)


def test_a_hold_that_ends_at_a_cut_is_not_walked_past_it() -> None:
    """The card that clears for its entrance must not be pushed over it: the frames after
    a hard cut are footage, and footage in a caption's own pixels is not a fade."""
    card = caption(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), top=180, left=200, strokes=14)
    window = np.concatenate([held_still(card, 6), np.stack([picture(i) for i in range(6, 12)])])
    mask = card > 200

    found = supers.edges(window, mask, anchor=2)

    assert (found.first, found.last) == (0, 5)
    assert found.ramp_out == 0


def test_a_fade_out_is_counted_as_the_ramp_it_is() -> None:
    card = caption(np.zeros((HEIGHT, WIDTH), dtype=np.uint8), top=180, left=200, strokes=14)
    fading = [np.clip(card.astype(np.float32) * f, 0, 255).astype(np.uint8) for f in (0.7, 0.4)]
    window = np.concatenate(
        [held_still(card, 5), np.stack(fading), np.stack([picture(i) for i in range(7, 11)])]
    )
    mask = card > 200

    found = supers.edges(window, mask, anchor=2)

    assert found.last == 4
    assert found.ramp_out == 2
    assert supers.Span(
        supers.CARD, found.first, found.last, 0, 0, 0, 0, 0, 0, found.ramp_in, found.ramp_out
    ).visible_last == 6


def span(first: int, last: int, kind: str = supers.OVERLAY) -> supers.Span:
    return supers.Span(
        kind=kind, first=first, last=last, top=0.8, left=0.1, bottom=0.9, right=0.9,
        pixels=500, pairs=4,
    )


def test_the_step_is_only_read_between_frames_the_graphic_was_seen_on() -> None:
    """How the piano keyboard passed on the corpus anchor. Its readings all sat inside one
    unchanging shot, but the span reached a frame past the cut at the end of it, and a step
    sampled at the span's outer edge found that cut and vouched for a graphic that never
    survived it. Only frames the graphic was actually seen on may speak for it."""
    still = [caption(jittered(picture(0), seed)) for seed in range(6)]
    elsewhere = [picture(3), picture(4)]

    assert supers.read_run(np.stack(still + elsewhere), lags=(2,)) == ()


def test_a_cut_inside_a_super_is_a_straddle() -> None:
    caught = supers.straddles([span(100, 200)], cuts=[50, 150, 400])

    assert len(caught) == 1
    assert caught[0]["cut"] == 150
    assert caught[0]["into_super"] == 50
    assert caught[0]["left_of_super"] == 51


def test_a_super_that_arrives_with_a_shot_or_clears_for_one_is_not_a_straddle() -> None:
    """The two legitimate edits, which a naive interval test would call violations: the
    super starting on the cut, and the super ending the frame before it."""
    assert supers.straddles([span(100, 200)], cuts=[100, 201]) == ()


def test_a_fading_super_is_straddled_through_its_fade() -> None:
    fading = span(100, 200)._replace(ramp_out=6)

    caught = supers.straddles([fading], cuts=[204])

    assert len(caught) == 1
    assert caught[0]["out"] == 206


def test_read_marked_hands_back_the_pixels_it_decided_on() -> None:
    """So a caller walking the edges at full rate asks the reading that found the super,
    rather than reading the whole span again and arriving at a second answer."""
    frames = np.stack(
        [picture(i) for i in range(3)]
        + [caption(picture(i)) for i in range(3, 11)]
        + [picture(i) for i in range(11, 14)]
    )

    marked = supers.read_marked(frames, lags=(3,))

    assert len(marked) == 1
    assert marked[0].span == supers.read_run(frames, lags=(3,))[0]
    rows = np.nonzero(marked[0].mask)[0]
    assert 344 <= rows.min() and rows.max() < 360


def test_clearance_measures_the_room_a_card_leaves_its_entrance() -> None:
    assert supers.clearance(span(0, 55, supers.CARD), cuts=[56, 172]) == 1
    assert supers.clearance(span(0, 55, supers.CARD), cuts=[60]) == 5


def test_a_super_with_no_cut_after_it_has_no_clearance_to_report() -> None:
    assert supers.clearance(span(100, 200), cuts=[50, 100]) is None


def test_the_two_kinds_of_straddle_are_counted_apart() -> None:
    """Measured on the corpus and not assumed: the human deliverables hold a personnel
    lower third across cut after cut, so a straddle is a fact whose ``kind`` decides
    whether it is a finding. Pooling them would fail every deliverable there is."""
    found = supers.review(
        [span(0, 200, supers.CARD), span(400, 900)],
        cuts=[100, 500, 700],
    )

    assert found["straddled"] == 3
    assert found["straddled_cards"] == 1
    assert found["straddled_overlays"] == 2


def test_the_review_counts_the_supers_the_straddles_came_out_of() -> None:
    found = supers.review([span(0, 55, supers.CARD), span(300, 480)], cuts=[56, 400, 600])

    assert found["cards"] == 1
    assert found["overlays"] == 1
    assert found["cuts"] == 3
    assert found["straddled"] == 1
    assert found["straddles"][0]["cut"] == 400
    assert found["supers"][0]["clears_before"] == 1
    assert found["held_frames"] == {"min": 56, "median": 118, "max": 181}


def test_a_review_of_nothing_says_so_rather_than_reading_clean() -> None:
    found = supers.review([], cuts=[10, 20])

    assert found["straddled"] == 0
    assert found["supers"] == []
    assert found["held_frames"] is None


def test_a_span_serialises_to_plain_json_types() -> None:
    record = span(10, 20).as_record()

    assert record["visible_first"] == 10
    assert record["frames"] == 11
    assert all(isinstance(one, int | float | str) for one in record.values())
