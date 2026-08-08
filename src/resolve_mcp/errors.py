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

    def __init__(
        self,
        name: str,
        bins: list[str],
        addressable: list[str],
        shallow: list[str] | None = None,
    ) -> None:
        """``bins`` is the bin of every matching clip; the rest are the values that work.

        Only a value that reaches one clip is offered. One that lands back on this same
        refusal is not a fix (#122), and the empty string is offered like any other because
        it addresses the pool root itself. ``shallow`` holds the bins that single a copy out
        only once the search stops descending, which is what ``recursive=False`` asks for
        (#134); the remainder — a bin holding two of the name — no lookup can answer.
        """
        offered = []
        if addressable:
            listed = ", ".join(f'bin="{path}"' for path in addressable)
            root = ' (bin="" is the pool root itself)' if "" in addressable else ""
            offered.append(f"Pass one of these to say which: {listed}{root}.")
        if shallow:
            listed = ", ".join(f'bin="{path}"' for path in shallow)
            lead = "Or" if offered else "Pass"
            offered.append(f"{lead} {listed} with recursive=false for the copy in that bin itself.")
        if offered:
            fix = " ".join(offered)
        else:
            fix = (
                "No lookup singles one out — one bin holds two clips of the name. Rename "
                "one in the Resolve GUI, or work from the file paths list_media reports."
            )
        super().__init__(
            cause=f"{len(bins)} clips are named {name!r}, so the reference is ambiguous.",
            fix=fix,
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


class TitlesInvalidError(ResolveMcpError):
    """The titles file did not pass the rules, so nothing was applied. ``detail`` holds them.

    Nothing was applied means nothing was *cleared* either: the Titles track still holds
    whatever the last good apply put there.
    """

    code = "titles_invalid"
    default_fix = (
        "Fix every error in detail.errors and apply again — validate_titles runs the "
        "identical checks without touching the timeline."
    )


class TitlesApplyFailedError(ResolveMcpError):
    """Resolve would not place the titles — a locked track, a refused clear, a clip astray.

    ``detail`` names the timeline and the track it was working on, because the Titles
    track may have been cleared before the failure: re-applying is always the right next
    move, and it is safe, because the apply is declarative.
    """

    code = "titles_apply_failed"
    default_fix = (
        "Fix what detail names in the Resolve GUI (unlock the Titles track, close any modal "
        "dialog) and apply again — the apply is idempotent, so a retry is always safe."
    )


class TitleNotFoundError(ResolveMcpError):
    """No single placed title answers to what the edit asked for — none, or more than one.

    ``detail`` lists what is actually on the Titles track, because the caller's next move
    is always to pick from that list rather than to guess again.
    """

    code = "title_not_found"
    default_fix = (
        "Run list_titles and copy the text of the one you mean exactly; pass at= its record "
        "frame as well when two titles read the same."
    )


class TitleEditFailedError(ResolveMcpError):
    """A placed title was found but would not take the edit, or took it for its neighbours.

    Distinct from ``titles_apply_failed`` because the recovery is different: nothing was
    cleared and nothing was placed, so the track is exactly as it was apart from whatever
    this write did land — which ``detail`` says per input.
    """

    code = "title_edit_failed"
    default_fix = (
        "Check the input id against list_titles and that the Titles track is unlocked, then "
        "edit again — or re-run apply_titles, which rebuilds the track from the file."
    )


class TitleTemplateError(ResolveMcpError):
    """A placed template instance is not one this route can title.

    The clip landed on the timeline but carries no Fusion comp, or a comp with no Text+
    node in it — which means the media-pool clip is not a Text+ title template at all.
    """

    code = "title_template_unusable"
    default_fix = (
        "Point the template at a Text+ title clip: author it in the Resolve GUI, export "
        "its bin as a .drb, and import that bin into the project's media pool."
    )


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


class RenderPresetNotFoundError(ResolveMcpError):
    """The render preset asked for is not one this project offers."""

    code = "render_preset_not_found"

    def __init__(self, name: str, available: list[str]) -> None:
        super().__init__(
            cause=f"No render preset named {name!r} in this project.",
            fix=(
                "list_render_presets names every preset, exactly as it must be spelled. "
                "Presets are per project and per user — one made on another machine is not here."
            ),
            detail={"requested": name, "available": available},
        )


class RenderTargetExistsError(ResolveMcpError):
    """A file already sits where the render would land, and the caller named that place.

    Resolve does not reliably overwrite: it may write ``name_0.mp4`` beside the old file
    instead, and the job would then report a path holding yesterday's export.
    """

    code = "render_target_exists"
    default_fix = (
        "Render under a different name, or pass refresh=true to replace what is there. "
        "Leaving target_dir out puts the file in the server's own render directory, which "
        "it replaces without asking."
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


class AnalysisDependencyError(ResolveMcpError):
    """An analysis model is not installed on this machine.

    Kept apart from a plain failure because the fix is an install the human has to run, not
    something the agent can retry its way out of.
    """

    code = "analysis_dependency_missing"
    default_fix = (
        "Install the missing package on the machine running the server, then start the job "
        "again. Analysis that does not need it can be asked for on its own."
    )


class AnalysisFailedError(ResolveMcpError):
    """A model or a curve refused the audio — a bad mix, a truncated file, a model falling over."""

    code = "analysis_failed"
    default_fix = (
        "Check the audio plays and holds what you expect, then start the job again. "
        "Analysis can be narrowed to one half (beats or energy) to isolate which fails."
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


class TranscriberUnavailableError(ResolveMcpError):
    """faster-whisper is not installed in this venv, so nothing can be transcribed."""

    code = "transcriber_unavailable"
    default_fix = (
        "Install the transcription extra with `uv sync --extra analysis`, which pulls "
        "faster-whisper and its CUDA runtime. The first run also downloads the model, "
        "which takes a while and needs the disk space."
    )


class TranscriptionError(ResolveMcpError):
    """The transcription job could not produce a transcript."""

    code = "transcription_failed"
    default_fix = (
        "Check the audio the transcript was to be made from — get_job on the acquisition "
        "job named in detail reports what happened to it — then start the job again."
    )


class FrameGrabError(ResolveMcpError):
    """ffmpeg would not give up a frame of this clip."""

    code = "frame_grab_failed"
    default_fix = (
        "Check the clip is online and holds video — inspect_clip reports both — and that the "
        "time asked for is inside its bounds. ffmpeg's own message is in detail.stderr."
    )


class SceneDetectionError(ResolveMcpError):
    """ffmpeg would not decode this clip looking for scene cuts."""

    code = "scene_detection_failed"
    default_fix = (
        "Check the clip is online and holds video — inspect_clip reports both. "
        "ffmpeg's own message is in detail.stderr."
    )


class InternalError(ResolveMcpError):
    code = "internal_error"
    default_fix = (
        "This is a bug in resolve-mcp rather than a Resolve state problem. "
        "Retry once; if it persists, work around it with run_python and report it."
    )
