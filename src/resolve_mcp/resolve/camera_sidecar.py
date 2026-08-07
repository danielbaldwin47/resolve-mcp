"""The camera model for media Resolve reads no camera metadata from.

Resolve fills its ``Camera *`` clip properties from the MXF wrapper on an XDROOT card, so
FX6 footage names its own bin and mirrorless footage does not. Live-probed on 2026-08-07
in one project (#94): an FX6 ``.MXF`` reports seven camera keys including ``Camera TC
Type = ILME-FX6V``, while an A7 IV ``.MP4`` on an M4ROOT card reports *no* camera key at
all — not an empty one, absent. The camera wrote the same facts to an XML sidecar beside
the clip, which Resolve never reads:

    20260617_D_A7IV_0001.MP4
    20260617_D_A7IV_0001M01.XML   <Device manufacturer="Sony" modelName="ILCE-7M4"/>

So the model is one file read away from a bin suggestion that would otherwise fall back.
This module is that read and nothing more: it is consulted only after the clip properties
come back empty, and every failure — no sidecar, unreadable file, malformed XML, no
``Device`` element — returns ``None`` so the caller falls back exactly as it did before.
A sidecar is untrusted input from a card, so nothing here raises.

Named ``camera_sidecar`` rather than ``sidecar`` because this repo already spends that
word on the angle sidecars of #13 — one JSON per project, and defined by contrast with
exactly this kind of file (see ``docs/agents/style-layer.md``). Two different things
called "the sidecar" in one codebase is one too many.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from ..logging_config import get_logger

log = get_logger("media")

# Sony numbers the sidecar per clip: C0012M01.XML. Nearly always index 01, so that name is
# tried directly and the directory is only listed when it misses — a card's CLIP folder
# holds thousands of entries on a drive that is often a network share, and an import walks
# every clip in it. The index is not always 01 across vendors and firmware, and cards mix
# the case of the extension, so the fallback matches the tail as a pattern.
USUAL_TAIL = "M01.XML"
SIDECAR_SUFFIX = re.compile(r"M\d{2}\.XML", re.IGNORECASE)

# The sidecar is namespaced (urn:schemas-professionalDisc:nonRealTimeMeta:...) and the
# namespace has changed between camera generations, so elements are matched on local name.
DEVICE_TAG = "Device"
MODEL_ATTRIBUTE = "modelName"


def camera_model(file_path: str) -> str | None:
    """The camera model recorded beside ``file_path``, or ``None`` if there is not one.

    ``None`` covers every way this can come up empty, because the caller's answer is the
    same for all of them: suggest the bare footage bin.
    """
    if not file_path:
        return None
    sidecar = _beside(Path(file_path))
    if sidecar is None:
        return None
    return _model_in(sidecar)


def _beside(clip: Path) -> Path | None:
    """The sidecar for ``clip``: the clip's own name plus the vendor's ``M01.XML`` tail."""
    usual = clip.with_name(f"{clip.stem}{USUAL_TAIL}")
    try:
        if usual.is_file():
            return usual
    except OSError:
        log.debug("Could not reach %r looking for a camera sidecar", str(usual))
        return None
    return _hunted(clip)


def _hunted(clip: Path) -> Path | None:
    """The sidecar under a name this vendor spells differently, or ``None``.

    Only reached when the usual name misses, because this is the expensive branch: it
    lists the whole card directory. Sorted so that a card carrying more than one index
    for a clip answers the same way every time rather than in directory order.
    """
    stem = clip.stem
    try:
        entries = sorted(entry.name for entry in clip.parent.iterdir())
    except OSError:
        log.debug("Could not list %r looking for a camera sidecar", str(clip.parent))
        return None
    for name in entries:
        if len(name) <= len(stem) or name[: len(stem)].lower() != stem.lower():
            continue
        if SIDECAR_SUFFIX.fullmatch(name[len(stem) :]):
            return clip.parent / name
    return None


def _model_in(sidecar: Path) -> str | None:
    """``Device/@modelName`` from a camera sidecar, without trusting the file."""
    try:
        root = ElementTree.parse(sidecar).getroot()
    except (OSError, ElementTree.ParseError):
        log.warning("Camera sidecar %r is not readable XML; ignoring it", str(sidecar))
        return None
    for element in root.iter():
        tag = element.tag
        if not isinstance(tag, str) or tag.rpartition("}")[2] != DEVICE_TAG:
            continue
        model = str(element.get(MODEL_ATTRIBUTE) or "").strip()
        if model:
            log.info("Camera model %r read from sidecar %r", model, sidecar.name)
            return model
    log.debug("Camera sidecar %r carries no %s model", str(sidecar), DEVICE_TAG)
    return None
