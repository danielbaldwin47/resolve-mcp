"""The session/project tools, called in-process against the fake Resolve seam."""

from __future__ import annotations

from pathlib import Path

from resolve_mcp.config import Config, set_config
from resolve_mcp.tools.project import (
    get_status,
    list_projects,
    open_project,
    snapshot_project,
)

from .conftest import Attach
from .fakes import studio


def test_status_orients_the_agent(attach: Attach) -> None:
    attach(studio(project="sunset-set", timeline="sunset-set v3", fps="59.94"))

    result = get_status()

    assert result["ok"] is True
    assert result["context"] == {
        "connected": True,
        "resolve_version": "21.0.3",
        "project": "sunset-set",
        "timeline": "sunset-set v3",
        "fps": 59.94,
    }
    assert result["product"] == "DaVinci Resolve Studio"


def test_status_reports_an_open_project_with_no_current_timeline(attach: Attach) -> None:
    attach(studio(timeline=None))

    context = get_status()["context"]

    assert context["timeline"] is None
    assert context["project"] == "sunset-set"
    assert context["fps"] == 59.94


def test_status_says_what_to_do_when_resolve_is_closed(attach: Attach) -> None:
    attach(None)

    result = get_status()

    assert result["ok"] is False
    assert result["error"]["code"] == "resolve_unavailable"
    assert result["error"]["cause"]
    assert result["error"]["fix"]
    assert result["context"]["connected"] is False


def test_status_says_what_to_do_when_no_project_is_open(attach: Attach) -> None:
    attach(studio(project=None))

    result = get_status()

    assert result["ok"] is True
    assert result["context"]["project"] is None
    assert result["context"]["connected"] is True


def test_lists_projects_and_marks_the_open_one(attach: Attach) -> None:
    attach(studio(project="sunset-set", extra_projects=("holiday-gig", "trio-session")))

    result = list_projects()

    assert result["ok"] is True
    assert sorted(result["projects"]) == ["holiday-gig", "sunset-set", "trio-session"]
    assert result["context"]["project"] == "sunset-set"


def test_opening_a_project_echoes_the_new_context(attach: Attach) -> None:
    attach(studio(project="sunset-set", extra_projects=("holiday-gig",)))

    result = open_project("holiday-gig")

    assert result["ok"] is True
    assert result["opened"] == "holiday-gig"
    assert result["context"]["project"] == "holiday-gig"
    assert result["context"]["timeline"] is None
    assert get_status()["context"]["project"] == "holiday-gig"


def test_opening_an_unknown_project_lists_the_real_ones(attach: Attach) -> None:
    attach(studio(project="sunset-set", extra_projects=("holiday-gig",)))

    result = open_project("sunset set")

    assert result["ok"] is False
    assert result["error"]["code"] == "project_not_found"
    assert "holiday-gig" in result["error"]["fix"]
    assert result["context"]["project"] == "sunset-set"


def test_snapshot_writes_an_opaque_drp_backup(attach: Attach, tmp_path: Path) -> None:
    fake = studio(project="sunset-set")
    attach(fake)
    set_config(Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path / "cache")}))

    result = snapshot_project()

    assert result["ok"] is True
    snapshot = Path(result["snapshot"])
    assert snapshot.exists()
    assert snapshot.suffix == ".drp"
    assert snapshot.parent == tmp_path / "cache" / "snapshots"
    assert "sunset-set" in snapshot.name
    assert result["context"]["project"] == "sunset-set"
    assert fake.GetProjectManager().exports[0][0] == "sunset-set"


def test_snapshot_honours_an_explicit_path(attach: Attach, tmp_path: Path) -> None:
    attach(studio(project="sunset-set"))
    target = tmp_path / "backups" / "before-rebuild.drp"

    result = snapshot_project(str(target))

    assert result["ok"] is True
    assert Path(result["snapshot"]) == target
    assert target.exists()


def test_snapshot_needs_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = snapshot_project()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"
    assert result["error"]["fix"]


def test_snapshot_reports_a_refusal_from_resolve(attach: Attach) -> None:
    fake = studio(project="sunset-set")
    attach(fake)
    fake.GetProjectManager().export_result = False

    result = snapshot_project()

    assert result["ok"] is False
    assert result["error"]["code"] == "snapshot_failed"
    assert result["error"]["fix"]


def test_tools_survive_a_dropped_handle_with_one_reconnect(attach: Attach) -> None:
    dead = studio(project="sunset-set")
    attach(dead, studio(project="sunset-set"))
    assert get_status()["ok"] is True

    dead.drop()

    result = get_status()

    assert result["ok"] is True
    assert result["context"]["project"] == "sunset-set"


def test_tools_report_cause_and_fix_when_resolve_stays_down(attach: Attach) -> None:
    dead = studio(project="sunset-set")
    attach(dead, None)
    get_status()
    dead.drop()

    result = list_projects()

    assert result["ok"] is False
    assert result["error"]["code"] == "resolve_unavailable"
    assert "Traceback" not in result["error"]["cause"]
