"""Where the master mix sits under a timeline — the axis a rebuild's marker carry rides.

The reading itself is exercised through its callers (``build_timeline`` carrying markers,
``correlate_timeline`` reading seconds off a cut). What is pinned here is the one decision
the callers delegate and neither can restate: **when do a timeline's audio shots agree on a
single mix, and when do they refuse to answer.**

That question has a live-measured answer behind it, which is why it is a test of its own.
A single append of a multi-channel clip comes back as one shot *per channel* — eight tracks
for an eight-channel MXF, all naming the same clip at the same frame. The obvious rule,
"exactly one audio item", reads that as an ambiguous timeline and refuses the commonest
concert mix there is. Agreement, not arity, is the thing.
"""

from __future__ import annotations

from resolve_mcp.resolve.mix import MixShot, anchor

MIX = "sunset-master.wav"


def _shot(name: str = MIX, record: int = 86400, source: int = 0) -> MixShot:
    return MixShot(name, record, source)


def test_the_mix_zero_is_the_record_frame_its_own_first_frame_lands_on() -> None:
    """A clip starting 24 frames into its media extends backwards to reach frame 0."""
    assert _shot(record=86400, source=24).zero_frame == 86376


def test_one_shot_is_the_anchor() -> None:
    assert anchor([_shot()]) == _shot()


def test_no_audio_at_all_answers_nothing_rather_than_a_default() -> None:
    """A rough cut has no mix. Counting from the timeline start instead would be a guess
    that reads exactly like a measurement."""
    assert anchor([]) is None


def test_the_channels_of_one_multi_channel_mix_are_one_anchor() -> None:
    """Measured live on Studio 21.0.3.7: appending an 8-channel MXF as one clip lands one
    item on each of A1-A8, same clip, same record frame, same source frame. That is one
    placement said eight times, and the whole reason the rule is agreement and not arity."""
    channels = [_shot() for _ in range(8)]

    assert anchor(channels) == _shot()


def test_the_same_clip_laid_at_two_offsets_is_not_an_anchor() -> None:
    """Which one is the mix? Nothing here answers that, so nothing is derived from either."""
    assert anchor([_shot(source=0), _shot(source=24)]) is None


def test_two_clips_at_the_same_offset_are_still_not_an_anchor() -> None:
    """Agreeing by accident is not agreeing: the caller asked which *clip* the mix is."""
    assert anchor([_shot(), _shot(name="scratch.wav")]) is None


def test_naming_the_clip_ignores_the_camera_scratch_beside_it() -> None:
    """A hand-edited concert routinely carries scratch audio the mix has no relation to."""
    scratch = _shot(name="C0012.MP4", record=86400, source=5000)

    assert anchor([scratch, _shot(source=24)], MIX) == _shot(source=24)


def test_naming_a_clip_the_timeline_does_not_carry_answers_nothing() -> None:
    assert anchor([_shot(name="C0012.MP4")], MIX) is None


def test_every_channel_of_the_named_mix_still_has_to_agree() -> None:
    """Narrowing by name is not a licence to take the first: two placements of the named
    clip disagree about where the mix starts however many channels each of them has."""
    shots = [_shot(source=0), _shot(source=0), _shot(source=24), _shot(name="C0012.MP4")]

    assert anchor(shots, MIX) is None
