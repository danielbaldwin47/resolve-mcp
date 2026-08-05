"""Structured errors.

Every failure that reaches the agent arrives as ``cause`` (what went wrong, in one
sentence) plus ``fix`` (what to do about it). Raw tracebacks go to the stderr log, never
into a tool result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESOLVE_FIX = (
    "Launch DaVinci Resolve Studio, open a project, and make sure "
    "Preferences > System > General > External scripting using is set to Local. "
    "Then retry."
)


class ResolveMcpError(Exception):
    """A failure the agent can act on."""

    code = "error"
    default_fix = "Retry, or use run_python to inspect the scripting API directly."

    def __init__(
        self,
        cause: str,
        fix: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(cause)
        self.cause = cause
        self.fix = fix or self.default_fix
        self.detail = detail or {}

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "cause": self.cause,
            "fix": self.fix,
            "detail": self.detail,
        }


class ResolveUnavailableError(ResolveMcpError):
    """Resolve is not running, not reachable, or dropped its handle mid-session."""

    code = "resolve_unavailable"
    default_fix = RESOLVE_FIX


class UnsupportedInterpreterError(ResolveMcpError):
    """This Python cannot load the Resolve scripting library without crashing."""

    code = "unsupported_interpreter"


class NoProjectOpenError(ResolveMcpError):
    code = "no_project_open"
    default_fix = (
        "Open a project first — list_projects shows what is available, open_project loads one."
    )


class ProjectNotFoundError(ResolveMcpError):
    code = "project_not_found"

    def __init__(self, name: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "none in the current database folder"
        super().__init__(
            cause=f"No project named {name!r} in the current database folder.",
            fix=f"Use one of these names exactly, or switch database folder: {listed}.",
            detail={"requested": name, "available": available},
        )


class NoTimelineOpenError(ResolveMcpError):
    """A project is open but nothing is on the timeline — or it holds no timelines at all."""

    code = "no_timeline_open"
    default_fix = (
        "Open a timeline in Resolve, or name one explicitly — list_timelines shows what "
        "the project has."
    )


class TimelineNotFoundError(ResolveMcpError):
    code = "timeline_not_found"

    def __init__(self, name: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "the project has no timelines"
        super().__init__(
            cause=f"No timeline named {name!r} in the open project.",
            fix=f"Timeline names must match exactly, version suffix included: {listed}.",
            detail={"requested": name, "available": available},
        )


class TimelineExportFailedError(ResolveMcpError):
    """Resolve would not write the interchange file, or wrote nothing."""

    code = "timeline_export_failed"
    default_fix = (
        "Check the target directory is writable from the machine Resolve runs on, then "
        "retry. Another format may be supported where this one is not: otio, fcpxml, drt."
    )


class TimelineImportFailedError(ResolveMcpError):
    """Resolve would not materialise a timeline from the file."""

    code = "timeline_import_failed"
    default_fix = (
        "Check the file is a timeline Resolve can read (.otio, .fcpxml, .drt) and is "
        "readable from the machine Resolve runs on. If it was hand-edited, an invalid "
        "document imports as nothing — validate the edit and retry."
    )


class SnapshotFailedError(ResolveMcpError):
    code = "snapshot_failed"
    default_fix = (
        "Check the target directory is writable and that the project is not mid-render, "
        "then retry. A different path can be passed explicitly."
    )


class MediaPoolUnavailableError(ResolveMcpError):
    """A project is open but Resolve would not hand over its media pool."""

    code = "media_pool_unavailable"
    default_fix = (
        "Reopen the project with open_project and retry; if it persists, check the project "
        "is not mid-import in the GUI."
    )


class BinNotFoundError(ResolveMcpError):
    code = "bin_not_found"

    def __init__(self, path: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "the media pool root only"
        super().__init__(
            cause=f"No bin at {path!r} in the media pool.",
            fix=(
                f"Bin paths are slash-separated from the root and case-sensitive. "
                f"These exist: {listed}. organize_media creates missing bins."
            ),
            detail={"requested": path, "available": available},
        )


class ClipNotFoundError(ResolveMcpError):
    code = "clip_not_found"

    def __init__(self, name: str, where: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "no clips at all"
        super().__init__(
            cause=f"No clip named {name!r} in {where}.",
            fix=f"Clip names must match exactly. list_media shows what is there: {listed}.",
            detail={"requested": name, "searched": where, "available": available},
        )


class AmbiguousClipError(ResolveMcpError):
    code = "ambiguous_clip"

    def __init__(self, name: str, bins: list[str]) -> None:
        listed = ", ".join(bin_path or "the root" for bin_path in bins)
        super().__init__(
            cause=f"{len(bins)} clips are named {name!r}, so the reference is ambiguous.",
            fix=f"Pass bin= to say which one: {listed}.",
            detail={"requested": name, "bins": bins},
        )


class ImportFailedError(ResolveMcpError):
    code = "import_failed"
    default_fix = (
        "Resolve imported none of these paths. Check they exist and are readable from the "
        "machine Resolve runs on, and that an image sequence pattern (file_%04d.png) matches "
        "the frames on disk."
    )


class RelinkFailedError(ResolveMcpError):
    code = "relink_failed"
    default_fix = (
        "Point relink_media at a folder that holds the moved media, or at the replacement "
        "file itself. list_media with offline_only shows what is still unlinked."
    )


class MediaOperationError(ResolveMcpError):
    """Resolve refused a media pool write without saying why — its usual failure mode."""

    code = "media_operation_failed"
    default_fix = (
        "Check the media pool is not locked by an open dialog in the Resolve GUI, then retry. "
        "run_python can reproduce the call directly for diagnosis."
    )


class TimelineOperationError(ResolveMcpError):
    """Resolve refused a timeline write — a bare ``False``, with no reason given."""

    code = "timeline_operation_failed"
    default_fix = (
        "Check the timeline is not locked or mid-render in the Resolve GUI, and that the "
        "frame is inside it, then retry. run_python can reproduce the call for diagnosis."
    )


class InvalidRequestError(ResolveMcpError):
    """The request could not be acted on as written — a shape problem, not a Resolve one."""

    code = "invalid_request"


class CutInvalidError(ResolveMcpError):
    """The cut file did not pass the rules, so nothing was built. ``detail`` holds them all."""

    code = "cut_invalid"
    default_fix = (
        "Fix every error in detail.errors and build again — validate_cut runs the identical "
        "checks without touching Resolve."
    )


class UnsupportedCutFeatureError(ResolveMcpError):
    """The cut is valid but describes something this build cannot place yet.

    Refused rather than partially built: a timeline missing a part of the cut that made it
    is the half-built outcome the pre-flight exists to prevent.
    """

    code = "unsupported_cut_feature"


class BuildFailedError(ResolveMcpError):
    """Resolve would not build the cut — creation refused, a locked track, a clip astray.

    ``detail`` names the timeline it was building when it stopped, because that timeline
    may exist and be incomplete: the earlier versions are untouched, this one is scrap.
    """

    code = "build_failed"
    default_fix = (
        "Fix what detail names in the Resolve GUI (unlock the track, clear the timeline), "
        "delete the incomplete version if one was made, and build again."
    )


class PythonExecutionError(ResolveMcpError):
    """The escape-hatch code raised. The traceback is logged, not returned."""

    code = "python_error"
    default_fix = (
        "Fix the code and retry. get_status confirms what is currently open; "
        "the namespace holds resolve, project_manager, project and timeline."
    )


class JobNotFoundError(ResolveMcpError):
    code = "job_not_found"

    def __init__(self, job_id: str) -> None:
        super().__init__(
            cause=f"No job with id {job_id!r}.",
            fix="list_jobs shows every job this cache directory knows about, newest first.",
            detail={"requested": job_id},
        )


class JobInterruptedError(ResolveMcpError):
    """The server restarted while this job was running, so its thread died with it."""

    code = "job_interrupted"
    default_fix = (
        "Start the job again. Anything it had already finished is in the result cache, "
        "so completed work is not paid for twice."
    )


class RenderQueueError(ResolveMcpError):
    """The render queue refused a job, failed one, or never produced the file."""

    code = "render_queue_failed"
    default_fix = (
        "Open the Deliver page and check the render queue for a failed or cancelled job, "
        "clear it, and retry. A render that reports success but writes nothing usually means "
        "the target directory is not writable from the machine Resolve runs on."
    )


class AudioExportError(ResolveMcpError):
    """Resolve's render queue would not produce the timeline mix."""

    code = "audio_export_failed"
    default_fix = (
        "Check the render queue in the Deliver page for a stuck or failed job, make sure the "
        "timeline has audio on it, then retry."
    )


class FfmpegUnavailableError(ResolveMcpError):
    """ffmpeg is not on PATH — the per-clip extraction route cannot run without it."""

    code = "ffmpeg_unavailable"
    default_fix = (
        "Install ffmpeg and put it on PATH, or point RESOLVE_MCP_FFMPEG at the executable. "
        "The timeline route needs no ffmpeg if you only want the timeline mix."
    )


class AudioExtractionError(ResolveMcpError):
    """ffmpeg ran and refused the file."""

    code = "audio_extraction_failed"
    default_fix = (
        "Check the clip is online and holds an audio stream — inspect_clip reports both. "
        "ffmpeg's own message is in detail.stderr."
    )


class AudioMappingError(ResolveMcpError):
    """The clip's audio does not live in the clip's own file, so extracting it would lie."""

    code = "audio_mapping_unsupported"
    default_fix = (
        "Acquire this audio from the timeline instead (scope=timeline): only the render "
        "route captures audio Resolve has linked or offset away from the source file."
    )


class SeparatorUnavailableError(ResolveMcpError):
    """python-audio-separator is not installed — stem separation cannot run without it."""

    code = "separator_unavailable"
    default_fix = (
        "Install it with pip install audio-separator[gpu] and make sure the audio-separator "
        "command is on PATH, or point RESOLVE_MCP_AUDIO_SEPARATOR at the executable."
    )


class StemSeparationError(ResolveMcpError):
    """The separator ran and did not produce the stems that were asked for."""

    code = "stem_separation_failed"
    default_fix = (
        "Check the model name (RESOLVE_MCP_STEM_MODEL, RESOLVE_MCP_DRUM_MODEL) is one "
        "audio-separator knows, and that the GPU has memory free. The separator's own "
        "message is in detail.output."
    )


class ChainedJobError(ResolveMcpError):
    """A job this one had to run first failed. Its cause, fix and code travel back unchanged.

    Relabelling it as a stems failure would hide what actually broke: a render queue that
    refused the export is a render queue problem whether the agent asked for audio or for
    stems, and the advice that fixes it is the advice the acquisition already wrote.
    """

    code = "chained_job_failed"

    def __init__(self, error: Mapping[str, Any], job_id: str) -> None:
        detail = dict(error.get("detail") or {})
        detail["job_id"] = job_id
        super().__init__(
            cause=str(error.get("cause") or f"The job {job_id} this one depends on failed."),
            fix=str(error.get("fix")) if error.get("fix") else None,
            detail=detail,
        )
        code = error.get("code")
        if code:
            self.code = str(code)


class InternalError(ResolveMcpError):
    code = "internal_error"
    default_fix = (
        "This is a bug in resolve-mcp rather than a Resolve state problem. "
        "Retry once; if it persists, work around it with run_python and report it."
    )
