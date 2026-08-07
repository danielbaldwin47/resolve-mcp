"""A fake DaVinci Resolve scripting API.

This is the project's single test seam: it substitutes at the point the connection
manager hands out the Resolve singleton, so every layer above it — wrappers, tools —
is exercised with Resolve closed.

The fakes mimic the real API's shape, including its quirks: getters return ``None``
rather than raising, ``LoadProject`` returns ``None`` for an unknown name, and settings
come back as strings.

It also holds the fixtures and stand-ins that go with them: the media files the worker tier
reads back (``write_wav``, ``write_jpeg``) and the ffmpeg runners every route that shells
out is tested against (``ffmpeg_absent``, ``ffmpeg_refusing``).

Every name below is re-exported here, so ``from .fakes import X`` reaches all of them and no
test needs to know which module a fake lives in. Open one module, not the package:

- ``core`` — ``DroppedHandleError``, ``AnswersNone``; the primitives everything else builds on
- ``fusion`` — comps, tools, splines
- ``timeline_item`` — one clip on a track
- ``timeline`` — timelines, tracks, the ``TrackSpec`` shorthand
- ``media`` — clips, bins, and the helpers that build clips from paths
- ``pool`` — ``FakeMediaPool`` and the ``media_pool()`` builder
- ``project`` — projects, render jobs, presets, settings
- ``connection`` — ``FakeResolve`` and ``FakeConnector``, the root of the object graph
- ``separator`` — the stem-separation backend stand-in
- ``fixtures`` — real media files on disk, and the ffmpeg runners
- ``builders`` — composed scenarios (``studio()``, ``sync_reference()``, ``with_a_mix()``)

``core`` and ``fixtures`` import nothing from their siblings, and the runtime import graph over
the rest is acyclic — which is what keeps a package importable where a single file could not
care. The graph only stays that way because references that exist solely in annotations go
under ``if TYPE_CHECKING``; move one out of that block and you can reintroduce a cycle.
"""

from __future__ import annotations

from .builders import studio as studio
from .builders import sync_reference as sync_reference
from .builders import with_a_mix as with_a_mix
from .connection import EXPORT_TYPES as EXPORT_TYPES
from .connection import FakeConnector as FakeConnector
from .connection import FakeResolve as FakeResolve
from .core import AnswersNone as AnswersNone
from .core import DroppedHandleError as DroppedHandleError
from .fixtures import ffmpeg_absent as ffmpeg_absent
from .fixtures import ffmpeg_refusing as ffmpeg_refusing
from .fixtures import write_clicks as write_clicks
from .fixtures import write_hits as write_hits
from .fixtures import write_jpeg as write_jpeg
from .fixtures import write_sections as write_sections
from .fixtures import write_wav as write_wav
from .fusion import FakeFusionComp as FakeFusionComp
from .fusion import FakeFusionInput as FakeFusionInput
from .fusion import FakeFusionTool as FakeFusionTool
from .fusion import FakeSpline as FakeSpline
from .media import AUDIO_TYPE as AUDIO_TYPE
from .media import DEFAULT_PROPERTIES as DEFAULT_PROPERTIES
from .media import IMAGE_SUFFIXES as IMAGE_SUFFIXES
from .media import STILL_DEFAULT_FRAMES as STILL_DEFAULT_FRAMES
from .media import FakeFolder as FakeFolder
from .media import FakeMediaPoolItem as FakeMediaPoolItem
from .media import text_plus_template as text_plus_template
from .pool import FakeMediaPool as FakeMediaPool
from .pool import media_pool as media_pool
from .project import FakeProject as FakeProject
from .project import FakeProjectManager as FakeProjectManager
from .separator import FakeSeparator as FakeSeparator
from .timeline import FakeTimeline as FakeTimeline
from .timeline import FakeTrack as FakeTrack
from .timeline import TrackSpec as TrackSpec
from .timeline_item import FakeTimelineItem as FakeTimelineItem
