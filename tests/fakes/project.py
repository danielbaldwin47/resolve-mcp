"""Projects and the project manager: render jobs, presets, settings and timeline ownership."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fixtures import write_wav

if TYPE_CHECKING:
    from .connection import FakeResolve
    from .pool import FakeMediaPool
    from .timeline import FakeTimeline


class FakeProject:
    """A project, including its render queue.

    The queue models what makes the real one hard to drive: settings live on the project
    rather than on the job, ``StartRendering`` returns before the render is done, and a job
    reports a status per poll — so ``render_statuses`` is a sequence handed out one per
    ``GetRenderJobStatus`` call, the last one repeating. ``render_writes_the_file=False``
    models the failure the return value hides: every call answers True and nothing lands.
    """

    def __init__(
        self,
        name: str,
        timeline: FakeTimeline | None = None,
        fps: str = "24",
        media_pool: FakeMediaPool | None = None,
        timelines: list[FakeTimeline | None] | None = None,
    ) -> None:
        """``timelines`` may hold a ``None``: Resolve sometimes answers an index with one."""
        self._name = name
        self._timeline = timeline
        self._fps = fps
        self._media_pool = media_pool
        if timelines is not None:
            self._timelines = list(timelines)
        else:
            self._timelines = [timeline] if timeline is not None else []
        self.render_settings: dict[str, Any] = {}
        self.render_format: tuple[str, str] | None = None
        self.render_mode: int | None = None
        self.render_queue: list[str] = []
        self.render_jobs: list[dict[str, Any]] = []
        self.render_statuses: list[str] = ["Complete"]
        self.render_seconds = 2.0
        self.render_writes_the_file = True
        # A preset carries the format and codec with it — loading one is what sets them,
        # which is why the deliver route never sets a format of its own.
        self.render_presets: dict[str, tuple[str, str]] = {
            "H.264 Master": ("mp4", "H.264"),
            "ProRes 422 HQ": ("mov", "ProRes422HQ"),
        }
        self.loaded_presets: list[str] = []
        self.accepts_preset = True
        self.accepts_format = True
        self.accepts_settings = True
        self.accepts_job = True
        self.starts_rendering = True
        self.refuse_set_current = False
        self.timeline_switches: list[str] = []
        self._status_calls = 0
        if media_pool is not None:
            media_pool.attach_project(self)

    def SetCurrentRenderMode(self, mode: int) -> bool:  # noqa: N802
        self.render_mode = mode
        return True

    def SetCurrentRenderFormatAndCodec(self, format_: str, codec: str) -> bool:  # noqa: N802
        if not self.accepts_format:
            return False
        self.render_format = (format_, codec)
        return True

    def GetRenderPresetList(self) -> list[str]:  # noqa: N802
        return list(self.render_presets)

    def LoadRenderPreset(self, name: str) -> bool:  # noqa: N802
        """Resolve answers a bare ``False`` for a preset it does not have, and for a refusal.

        Loading a preset also **replaces the render settings**, which is why the caller
        applies its own after this and not before. Modelling the clobber is what makes
        that ordering testable: get it backwards and the caller's target directory is the
        preset's, not the one that was asked for.
        """
        if not self.accepts_preset or name not in self.render_presets:
            return False
        self.loaded_presets.append(name)
        self.render_format = self.render_presets[name]
        self.render_settings = {"TargetDir": "C:/preset-default", "CustomName": "preset-default"}
        return True

    def GetCurrentRenderFormatAndCodec(self) -> dict[str, str]:  # noqa: N802
        """``format`` is the file extension, not the display name — as in the real API."""
        if self.render_format is None:
            return {}
        format_, codec = self.render_format
        return {"format": format_, "codec": codec}

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:  # noqa: N802
        if not self.accepts_settings:
            return False
        self.render_settings = dict(settings)
        return True

    def AddRenderJob(self) -> str | None:  # noqa: N802
        """Returns the new job's id, or ``None`` when Resolve refuses it."""
        if not self.accepts_job:
            return None
        job_id = f"render-{len(self.render_jobs) + 1}"
        self.render_jobs.append({"id": job_id, "settings": dict(self.render_settings)})
        self.render_queue.append(job_id)
        return job_id

    def StartRendering(self, *job_ids: str) -> bool:  # noqa: N802
        if not self.starts_rendering:
            return False
        if self.render_writes_the_file:
            self._write_the_render()
        return True

    def GetRenderJobStatus(self, job_id: str) -> dict[str, Any]:  # noqa: N802
        """One status per poll, the last one repeating — a render is watched, not awaited."""
        index = min(self._status_calls, len(self.render_statuses) - 1)
        self._status_calls += 1
        status = self.render_statuses[index]
        return {
            "JobStatus": status,
            "CompletionPercentage": 100 if status == "Complete" else index * 10,
        }

    def DeleteRenderJob(self, job_id: str) -> bool:  # noqa: N802
        if job_id in self.render_queue:
            self.render_queue.remove(job_id)
            return True
        return False

    def _write_the_render(self) -> None:
        """Whatever the current format says lands: a real WAV, or bytes for a video file."""
        target = Path(str(self.render_settings.get("TargetDir", "")))
        name = str(self.render_settings.get("CustomName", "render"))
        extension = self.render_format[0] if self.render_format else "wav"
        if extension == "wav":
            write_wav(
                target / f"{name}.wav",
                seconds=self.render_seconds,
                sample_rate=int(self.render_settings.get("AudioSampleRate", 48000)),
                bit_depth=int(self.render_settings.get("AudioBitDepth", 24)),
            )
            return
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{name}.{extension}").write_bytes(b"\0" * 2048)

    def GetName(self) -> str:  # noqa: N802
        return self._name

    def GetCurrentTimeline(self) -> FakeTimeline | None:  # noqa: N802
        return self._timeline

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:  # noqa: N802
        """Resolve only appends to the current timeline, so the build has to set it.

        ``refuse_set_current`` models a Resolve that will not switch.
        """
        if self.refuse_set_current:
            return False
        self.timeline_switches.append(str(timeline.GetName()))
        self._timeline = timeline
        return True

    def add_timeline(self, timeline: FakeTimeline) -> None:
        """What creating a timeline does to a project — the pool reaches through here."""
        self._timelines.append(timeline)

    def remove_timeline(self, timeline: FakeTimeline) -> None:
        """Deleting a cut the project never held is a no-op, as in Resolve."""
        self._timelines = [held for held in self._timelines if held is not timeline]
        if self._timeline is timeline:
            self._timeline = None

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline | None:  # noqa: N802
        """One-based, as in the real API; out of range returns ``None`` rather than raising."""
        if 1 <= index <= len(self._timelines):
            return self._timelines[index - 1]
        return None

    def GetSetting(self, key: str) -> str | None:  # noqa: N802
        return self._fps if key == "timelineFrameRate" else None

    def GetMediaPool(self) -> FakeMediaPool | None:  # noqa: N802
        return self._media_pool


class FakeProjectManager:
    def __init__(self, owner: FakeResolve) -> None:
        self._owner = owner
        self.exports: list[tuple[str, str, bool]] = []
        self.export_result = True
        self.save_result = True
        self.calls: list[str] = []

    def _check(self, method: str) -> None:
        self.calls.append(method)
        self._owner._check()

    def GetProjectListInCurrentFolder(self) -> list[str]:  # noqa: N802
        self._check("GetProjectListInCurrentFolder")
        return list(self._owner.projects)

    def GetCurrentProject(self) -> FakeProject | None:  # noqa: N802
        self._check("GetCurrentProject")
        return self._owner.current_project

    def SaveProject(self) -> bool:  # noqa: N802
        self._check("SaveProject")
        return self.save_result

    def LoadProject(self, name: str) -> FakeProject | None:  # noqa: N802
        self._check("LoadProject")
        if name not in self._owner.projects:
            return None
        self._owner.current_project = self._owner.projects[name]
        return self._owner.current_project

    def ExportProject(  # noqa: N802
        self,
        project_name: str,
        file_path: str,
        with_stills_and_luts: bool = False,
    ) -> bool:
        self._check("ExportProject")
        self.exports.append((project_name, file_path, with_stills_and_luts))
        if self.export_result:
            Path(file_path).write_bytes(b"fake-drp")
        return self.export_result
