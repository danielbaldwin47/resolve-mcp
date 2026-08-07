"""The rough-cut pillar, end to end, at the one seam — ticket #47.

Every other test in this suite proves one tool in isolation. This one walks the pillar the
way a session does, in order, on one cut file: read the transcript, keep the good take,
offer the abandoned one as an alternate, cover the jump cut with b-roll, validate, build,
flip the take the way a review round does, read the result back, and mark what the director
should look at. The value is in the joins — a build that places segments correctly and a
swap that flips correctly can still not add up to a workflow, and nothing that tests them
one at a time would notice.

What this cannot prove is the half that is editorial: that the take chosen was the better
one, that the b-roll is topically right, that the assembly follows the brief. Those are
judgements over real footage and they go to the director — see the ticket's
`## Needs from you`. What is provable here is that the mechanism underneath them holds.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from resolve_mcp.tools import cut as cut_tools
from resolve_mcp.tools import timeline as timeline_tools

from .conftest import Attach
from .cutfile import built, placements, selector, shots
from .roughcut import (
    CLOSE,
    FALSE_START,
    GOOD_TAKE,
    a_cut,
    a_project,
    a_transcript,
    both_takes,
    delivered,
    with_alternate,
)

TIMELINE = "interview v1"

SOURCE = Path(__file__).resolve().parent.parent / "src" / "resolve_mcp"

PILLAR_DOCUMENTS = re.compile(
    r"""
      \bprojects [/\\]     # the per-project directory the two documents live in
    | \bbriefs? \.md        # or either of them named as the file it is
    | \bb-?roll \.json
    """,
    re.VERBOSE | re.IGNORECASE,
)
"""What reading the brief or the catalog would have to look like in source.

The same guard the style layer gets, for the same reason: these two documents are the
agent's, and the way that breaks is not a failing assertion — it is a convenience landing
in ``src/`` that opens the catalog "just to check", after which coverage is server
behaviour.

Written against the layout ``docs/agents/rough-cut.md`` documents —
``projects/<project>/brief.md`` and ``projects/<project>/broll.json`` — because a guard
matching a shape nothing uses passes forever while proving nothing. It is deliberately a
path shape: prose about b-roll is what these modules *should* carry, and ``broll_pan`` is
already a source alias in the schema example.
"""

PROBES = (
    ('open("projects/demo/broll.json")', True),
    ('Path("projects") / project / "brief.md"', True),
    ('"source": "broll_pan"', False),
    ("b-roll rides the segment it covers", False),
)
"""Lines the guard must and must not catch — the assertion that the assertion works."""


def test_the_guard_catches_what_it_is_written_for() -> None:
    """A regex guard that matches nothing is a test that passes for the wrong reason."""
    assert [bool(PILLAR_DOCUMENTS.search(line)) for line, _ in PROBES] == [
        caught for _, caught in PROBES
    ]


def test_the_pillars_documents_stay_the_agents() -> None:
    """No module reaches for a brief or a b-roll catalog by path."""
    files = sorted(SOURCE.rglob("*.py"))
    assert len(files) > 20

    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            assert not PILLAR_DOCUMENTS.search(line), f"{path.name}:{number} opens {line!r}"


def test_the_assembled_cut_validates_without_a_master_mix(attach: Attach, tmp_path: Path) -> None:
    """A rough cut has no continuous mix under it — the concert substrate, deliberately absent."""
    attach(a_project())

    result = cut_tools.validate_cut(a_cut(tmp_path, delivered()))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["valid"] is True


def test_a_retake_offered_as_an_alternate_validates(attach: Attach, tmp_path: Path) -> None:
    """E8 is the rule that bites here: an alternate must match its main frame for frame."""
    attach(a_project())

    result = cut_tools.validate_cut(a_cut(tmp_path, with_alternate()))

    assert result["valid"] is True


def test_the_cut_builds_the_a_roll_in_order(attach: Attach, tmp_path: Path) -> None:
    """AC1's mechanical half: the takes the transcript reading chose, in the order it chose."""
    resolve = a_project()
    attach(resolve)

    result = cut_tools.build_timeline(a_cut(tmp_path, delivered()))

    assert result["ok"] is True
    assert result["timeline"]["name"] == TIMELINE
    assert placements(built(resolve, TIMELINE)) == [
        ("A001.mp4", 0, GOOD_TAKE[1] - GOOD_TAKE[0]),
        ("A001.mp4", GOOD_TAKE[1] - GOOD_TAKE[0], CLOSE[1] - CLOSE[0]),
    ]


def test_the_b_roll_lands_on_v2_across_the_seam(attach: Attach, tmp_path: Path) -> None:
    """AC3's mechanical half: coverage rides the segment, so it sits over the join at 44."""
    resolve = a_project()
    attach(resolve)

    cut_tools.build_timeline(a_cut(tmp_path, delivered()))

    assert placements(built(resolve, TIMELINE), index=2) == [("B012.mp4", 36, 16)]


def test_the_retake_is_a_take_the_director_can_flip(attach: Attach, tmp_path: Path) -> None:
    """AC2: the abandoned pass is not deleted, it is parked in the shot as a second take."""
    resolve = a_project()
    attach(resolve)
    path = a_cut(tmp_path, with_alternate())
    cut_tools.build_timeline(path)

    first = shots(built(resolve, TIMELINE))[0]

    assert selector(first) == [
        ("A001.mp4", GOOD_TAKE[0], GOOD_TAKE[1]),
        ("A001.mp4", FALSE_START[0], GOOD_TAKE[1] - GOOD_TAKE[0]),
    ]


def test_swap_take_flips_the_shot_in_place(attach: Attach, tmp_path: Path) -> None:
    """AC2 again, from the review round: a note about a take costs one call, not a rebuild."""
    resolve = a_project()
    attach(resolve)
    path = a_cut(tmp_path, with_alternate())
    cut_tools.build_timeline(path)

    result = cut_tools.swap_take(path, "s001", 2, timeline=TIMELINE)

    assert result["ok"] is True
    assert shots(built(resolve, TIMELINE))[0].GetSelectedTakeIndex() == 2


def test_the_delivered_cut_reads_back_as_one_clean_line(attach: Attach, tmp_path: Path) -> None:
    """AC4's self-review: the flub is gone, the good take is what the timeline says."""
    attach(a_project())

    reading = cut_tools.virtual_transcript(
        a_cut(tmp_path, delivered()),
        {"cam_a": a_transcript(tmp_path)},
    )

    assert reading["text"] == "we start here and finish"
    assert reading["counts"]["uncovered"] == 0
    assert [one["rule"] for one in reading["warnings"]] == ["W6"]


def test_the_self_review_catches_the_assembly_that_kept_both_takes(
    attach: Attach, tmp_path: Path
) -> None:
    """The mistake the pillar is most likely to ship: a false start left in front of the take.

    This is the join that matters. Validation passes it — it is a structurally perfect cut
    file — and the build makes a perfectly good timeline out of it. Only reading the words
    back says the piece stammers.
    """
    attach(a_project())
    path = a_cut(tmp_path, both_takes())

    assert cut_tools.validate_cut(path)["valid"] is True

    reading = cut_tools.virtual_transcript(path, {"cam_a": a_transcript(tmp_path)})

    assert reading["text"] == "we start we start here and finish"
    assert [one["rule"] for one in reading["warnings"]] == ["W4", "W6", "W7", "W7"]


def test_the_uncertainties_go_onto_the_timeline_as_the_cut_report(
    attach: Attach, tmp_path: Path
) -> None:
    """AC4's other half: the director reads flags on the timeline, not a wall of prose."""
    resolve = a_project()
    attach(resolve)
    path = a_cut(tmp_path, delivered())
    cut_tools.build_timeline(path)
    reading = cut_tools.virtual_transcript(path, {"cam_a": a_transcript(tmp_path)})

    result = timeline_tools.set_markers(
        [_flag(one, reading) for one in reading["warnings"]],
        timeline=TIMELINE,
    )

    assert result["ok"] is True
    marked = timeline_tools.list_markers(timeline=TIMELINE)
    assert [one["note"] for one in marked["markers"]] == [
        "'finish' is delivered at confidence 0.3"
    ]


def test_the_whole_pillar_runs_in_one_pass(attach: Attach, tmp_path: Path) -> None:
    """Assemble, review, fix, rebuild — the loop, with the second version proving the fix.

    The first assembly keeps both takes and reads back stammering; the fix drops the false
    start and covers the seam; the rebuild is a new version rather than a mutation, and the
    read-back of that version is the quality bar being met rather than asserted.
    """
    resolve = a_project()
    attach(resolve)
    path = a_cut(tmp_path, both_takes())
    cut_tools.build_timeline(path)
    before = cut_tools.virtual_transcript(path, {"cam_a": a_transcript(tmp_path)})

    assert "W4" in [one["rule"] for one in before["warnings"]]

    fixed = a_cut(tmp_path, delivered(), name="interview.cut.json")
    result = cut_tools.build_timeline(fixed)
    after = cut_tools.virtual_transcript(fixed, {"cam_a": a_transcript(tmp_path)})

    assert result["timeline"]["name"] == "interview v2"
    assert built(resolve, "interview v1") is not None
    assert "W4" not in [one["rule"] for one in after["warnings"]]
    assert after["counts"]["uncovered"] == 0


def _flag(warning: dict[str, Any], reading: dict[str, Any]) -> dict[str, Any]:
    """One uncertainty as one marker, at the segment it was found in."""
    at = next(
        one["at"]["frames"] for one in reading["segments"] if one["id"] == warning["id"]
    )
    return {
        "frame": at,
        "color": "Yellow",
        "name": warning["rule"],
        "note": warning["message"],
    }
