"""The two primitives every other fake is built on.

``AnswersNone`` reproduces the quirk that shapes every wrapper in this project, so it sits
below the whole package: no other module here may import from one that imports this one.
"""

from __future__ import annotations

from typing import Any


class DroppedHandleError(RuntimeError):
    """What a stale Resolve handle raises when the app has gone away."""


class AnswersNone:
    """A Resolve object whose ``missing`` methods answer ``None`` instead of raising.

    That is how the real API expresses "this build does not have that method": fusionscript
    answers *every* attribute name, so ``hasattr`` passes and the call then dies with
    ``NoneType is not callable``. Verified live on Studio 21.0.3.7 (#41). A wrapper that
    guarded with ``hasattr`` would pass against a fake that raised ``AttributeError``, so
    no fake here does.
    """

    _missing: set[str]

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_") and name in object.__getattribute__(self, "_missing"):
            return None
        return object.__getattribute__(self, name)
