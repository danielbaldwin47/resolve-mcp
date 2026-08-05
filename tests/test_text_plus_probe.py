"""The Text+ template-append probe, run against fakes.

What this tier can prove: that the probe walks the route in the right order, that it
reads every title back *after* all the writes rather than one at a time, and that each
way the route can fail comes back named. What it cannot prove is the thing #41 exists to
answer — whether Resolve really keeps a per-instance comp — because the fake behaves
however it was told to. That answer only comes from ``uv run pytest -m live``; this suite
is what stops the live run failing on a typo instead of on a finding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.errors import NoProjectOpenError
from resolve_mcp.resolve.connection import get_connection

from .conftest import Attach
from .fakes import (
    DroppedHandleError,
    FakeFolder,
    FakeFusionComp,
    FakeFusionTool,
    FakeMediaPoolItem,
    FakeProject,
    FakeResolve,
    FakeTimelineItem,
    media_pool,
    studio,
    text_plus_template,
)
from .text_plus_probe import ProbeFailed, probe_template_append


def a_template_file(tmp_path: Path, name: str = "Titles.drb") -> Path:
    """The ``.drb`` the human exported from the GUI.

    Its bytes never matter — only Resolve reads them — but its existence does: an
    unreadable path is refused, not imported.
    """
    target = tmp_path / name
    target.write_bytes(b"fake-drb")
    return target


def the_open_project(handle: FakeResolve) -> FakeProject:
    """``studio()`` always opens one; this is only here to say so in the type."""
    project = handle.current_project
    assert project is not None
    return project


def test_the_route_runs_end_to_end_and_each_instance_keeps_its_own_text(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool()
    attach(studio(pool=pool))

    report = probe_template_append(
        get_connection(),
        a_template_file(tmp_path),
        texts=("Sunset Boulevard", "Bass — Ana Ruiz"),
    )

    assert [placed.asked for placed in report.placed] == ["Sunset Boulevard", "Bass — Ana Ruiz"]
    assert [placed.read_back for placed in report.placed] == [
        "Sunset Boulevard",
        "Bass — Ana Ruiz",
    ]
    assert report.clip_name == "Song Title"
    assert report.clip_type == "Generator"
    assert report.cleaned_up is True
    assert pool.folder_imports == [(str(a_template_file(tmp_path)), "")]
    # Each instance is asked for with an inclusive end frame, and lands after the one
    # before it — two titles stacked on one frame would read back as one title placed twice.
    sent = [dict(one) for one in pool.appends[0]]
    assert [{key: one[key] for key in ("startFrame", "endFrame")} for one in sent] == [
        {"startFrame": 0, "endFrame": 119},
        {"startFrame": 0, "endFrame": 119},
    ]
    assert {one["mediaPoolItem"].GetName() for one in sent} == {"Song Title"}
    assert [placed.record_in for placed in report.placed] == [0, 120]
    assert [placed.duration for placed in report.placed] == [120, 120]


def test_instances_that_share_one_comp_are_caught_by_reading_every_title_back(
    attach: Attach, tmp_path: Path
) -> None:
    """The finding the whole ticket turns on.

    A probe that set a title and read it straight back would pass here — the second write
    has not happened yet. Only reading every instance after every write shows the bleed.
    """
    pool = media_pool()
    pool.appends_share_one_comp = True
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path), texts=("One", "Two"))

    assert raised.value.step == "read the text back"
    assert "share one comp" in str(raised.value)
    assert "'Two'" in str(raised.value)


def test_nothing_is_appended_until_the_scratch_timeline_is_confirmed_current(
    attach: Attach, tmp_path: Path
) -> None:
    """The footgun that would have cost the operator their cut.

    ``AppendToTimeline`` appends to the project's *current* timeline, not to the one just
    created. A probe that assumed ``CreateEmptyTimeline`` switched would, on a Resolve
    that did not, place two Text+ instances on the open cut — and still pass every
    assertion after it, because the instances would be perfectly real.
    """
    pool = media_pool()
    pool.switches_current_timeline = False
    handle = studio(pool=pool)
    open_cut = the_open_project(handle).GetCurrentTimeline()
    the_open_project(handle).refuse_set_current = True
    attach(handle)

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "target the scratch timeline"
    assert "nothing was appended" in str(raised.value)
    assert pool.appends == []
    assert open_cut is not None
    assert open_cut.first_video_track().items == []


def test_a_resolve_that_does_not_switch_on_create_is_switched_by_hand(
    attach: Attach, tmp_path: Path
) -> None:
    """The recovery the check above exists for: creating a timeline need not select it, so
    the probe selects it, and the run proceeds on the scratch cut rather than the open one."""
    pool = media_pool()
    pool.switches_current_timeline = False
    handle = studio(pool=pool)
    open_cut = the_open_project(handle).GetCurrentTimeline()
    attach(handle)

    report = probe_template_append(get_connection(), a_template_file(tmp_path))

    assert report.per_instance_text is True
    assert open_cut is not None
    assert open_cut.first_video_track().items == []
    assert [placed.record_in for placed in report.placed] == [0, 120]


def test_two_instances_stacked_on_one_frame_are_not_read_as_two_titles(
    attach: Attach, tmp_path: Path
) -> None:
    """One title placed twice on the same frame would satisfy every later check while
    proving nothing about per-instance text."""
    pool = media_pool()
    pool.append_result = [FakeTimelineItem("Song Title", 0, 120) for _ in range(2)]
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the placed instances"
    assert "[0, 0]" in str(raised.value)


def test_an_append_the_timeline_does_not_hold_is_not_taken_on_trust(
    attach: Attach, tmp_path: Path
) -> None:
    """What the append returned proves a call succeeded; what the timeline holds proves a
    title was placed. Only the second is what titling needs."""
    pool = media_pool()
    pool.appends_land_nowhere = True
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the placed instances"
    assert "holds 0" in str(raised.value)


def test_the_current_folder_and_timeline_are_put_back(attach: Attach, tmp_path: Path) -> None:
    """A probe that leaves the GUI somewhere else is a probe that gets blamed for the mess."""
    pool = media_pool({"Concert/Angles": [FakeMediaPoolItem("C0012.mp4")]})
    was_in = pool.GetRootFolder().GetSubFolderList()[0]
    pool.SetCurrentFolder(was_in)
    handle = studio(pool=pool)
    was_on = the_open_project(handle).GetCurrentTimeline()
    attach(handle)

    report = probe_template_append(get_connection(), a_template_file(tmp_path))

    assert report.per_instance_text is True
    assert pool.GetCurrentFolder() is was_in
    assert the_open_project(handle).GetCurrentTimeline() is was_on


def test_an_import_that_answers_true_and_lands_nothing_is_not_mistaken_for_success(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool()
    pool.import_lands_nothing = True
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the imported bin"
    assert "answered True" in str(raised.value)


def test_a_bin_whose_name_was_already_taken_is_reported_rather_than_reused(
    attach: Attach, tmp_path: Path
) -> None:
    """New bins are recognised by name, because Resolve hands out a fresh proxy per call
    and identity does not survive a second walk. A name collision therefore looks exactly
    like an import that landed nothing, and must not be answered with the older bin."""
    pool = media_pool({"Titles": [FakeMediaPoolItem("someone-elses-title")]})
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the imported bin"
    assert "Titles" in str(raised.value)


def test_a_refused_import_names_the_template_it_could_not_read(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool()
    pool.import_folder_result = False
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "import the template bin"
    assert "Titles.drb" in str(raised.value)


def test_a_template_bin_holding_more_than_one_clip_refuses_to_guess(
    attach: Attach, tmp_path: Path
) -> None:
    crowded = FakeFolder("Titles")
    crowded.clips.extend([text_plus_template("Song Title"), text_plus_template("Personnel")])
    pool = media_pool()
    pool.imported_folder = crowded
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the template clip"
    assert "Song Title" in str(raised.value)
    assert "Personnel" in str(raised.value)


def test_a_placed_instance_with_no_fusion_comp_names_the_drb_round_trip(
    attach: Attach, tmp_path: Path
) -> None:
    """A template that survives the export but arrives as a plain clip is the failure
    mode that would sink the whole Text+ route, so it gets its own sentence."""
    stripped = FakeFolder("Titles")
    stripped.clips.append(FakeMediaPoolItem("Song Title", properties={"Type": "Generator"}))
    pool = media_pool()
    pool.imported_folder = stripped
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "open the Fusion comp"
    assert "no Fusion comp" in str(raised.value)


def test_a_comp_without_a_text_plus_node_lists_what_the_comp_does_hold(
    attach: Attach, tmp_path: Path
) -> None:
    wrong_node = FakeFolder("Titles")
    wrong_node.clips.append(
        FakeMediaPoolItem(
            "Song Title",
            properties={"Type": "Generator"},
            template_comp=FakeFusionComp([FakeFusionTool("Background", name="Backdrop")]),
        )
    )
    pool = media_pool()
    pool.imported_folder = wrong_node
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "find the Text+ node"
    assert "Backdrop" in str(raised.value)


def test_a_build_without_the_fusion_getters_is_named_rather_than_left_to_blow_up(
    attach: Attach, tmp_path: Path
) -> None:
    """Live on 21.0.3.7 a getter Resolve does not have reads back as ``None``, not as a
    missing attribute — so the naive call dies with ``NoneType is not callable``, which
    names neither the method nor the build."""
    pool = media_pool()
    pool.append_result = [
        FakeTimelineItem("Song Title", record, 120, missing={"GetFusionCompCount"})
        for record in (0, 120)
    ]
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "open the Fusion comp"
    assert "no GetFusionCompCount" in str(raised.value)


def test_the_scratch_bin_is_removed_when_the_timeline_was_never_created(
    attach: Attach, tmp_path: Path
) -> None:
    """A probe that leaves its scratch bin behind is a probe nobody runs twice — and the
    bin is already in the pool by the time a later step fails."""
    pool = media_pool()
    pool.refuses_create_timeline = True
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), a_template_file(tmp_path))

    assert raised.value.step == "create the scratch timeline"
    assert [folder.GetName() for folder in pool.deleted_folders] == ["Titles"]
    assert pool.deleted_timelines == []
    assert pool.GetRootFolder().GetSubFolderList() == []


def test_a_failed_cleanup_is_recorded_rather_than_raised(attach: Attach, tmp_path: Path) -> None:
    """The route worked; only the tidying did not. Losing the result over that would throw
    away the answer the live run was made for."""
    pool = media_pool()
    pool.delete_folders_result = False
    attach(studio(pool=pool))

    report = probe_template_append(get_connection(), a_template_file(tmp_path))

    assert report.cleaned_up is False
    assert "left behind" in report.render()


def test_resolve_quitting_mid_probe_surfaces_as_a_dropped_handle(
    attach: Attach, tmp_path: Path
) -> None:
    """Not a finding about Text+ — the probe must not dress a departed Resolve up as one."""
    pool = media_pool()
    handle = studio(pool=pool)
    attach(handle)
    handle.die_after(4)

    with pytest.raises(DroppedHandleError):
        probe_template_append(get_connection(), a_template_file(tmp_path))


def test_no_project_open_fails_before_anything_is_touched(attach: Attach, tmp_path: Path) -> None:
    attach(studio(project=None))

    with pytest.raises(NoProjectOpenError):
        probe_template_append(get_connection(), a_template_file(tmp_path))


def test_two_titles_asked_for_the_same_text_would_prove_nothing(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=media_pool()))

    with pytest.raises(ValueError, match="distinct"):
        probe_template_append(get_connection(), a_template_file(tmp_path), texts=("Same", "Same"))


def test_a_missing_template_is_refused_before_resolve_is_touched(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool()
    attach(studio(pool=pool))

    with pytest.raises(ProbeFailed) as raised:
        probe_template_append(get_connection(), tmp_path / "never-exported.drb")

    assert raised.value.step == "read the template"
    assert pool.calls == []


def test_the_report_carries_what_the_ticket_has_to_record(attach: Attach, tmp_path: Path) -> None:
    """The live run happens on a machine nobody is watching; the report is the evidence."""
    pool = media_pool()
    attach(studio(pool=pool))

    rendered = probe_template_append(
        get_connection(), a_template_file(tmp_path), texts=("First", "Second")
    ).render()

    assert "Titles.drb" in rendered
    assert "Generator" in rendered
    # The node's own name, not its RegID: a template with several Text+ nodes is a real
    # possibility, and which one answered is the part apply_titles cannot guess.
    assert "Template Text" in rendered
    assert "Text+ nodes in comp: 1" in rendered
    assert "First" in rendered and "Second" in rendered
    assert "per-instance text: yes" in rendered
