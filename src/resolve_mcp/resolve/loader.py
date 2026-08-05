"""Importing the Resolve scripting module from disk and attaching to the running app.

Direct-attach: this process imports ``DaVinciResolveScript`` and asks it for the
``Resolve`` handle. There is no daemon and no in-app bridge.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

from ..config import Config, get_config
from ..errors import RESOLVE_FIX, ResolveUnavailableError
from ..interpreter import ensure_supported
from ..logging_config import get_logger

MODULE_NAME = "DaVinciResolveScript"

log = get_logger("loader")


_module: Any | None = None


def load_resolve(config: Config | None = None) -> Any | None:
    """Return the ``Resolve`` handle, or ``None`` when Resolve is not running.

    Raises ``ResolveUnavailableError`` when the scripting API itself cannot be found —
    a configuration problem with a different fix than "start Resolve".
    """
    config = config or get_config()
    module = scripting_module(config)
    handle: Any | None = module.scriptapp("Resolve")
    log.debug("scriptapp('Resolve') returned %s", "a handle" if handle else "None")
    return handle


def scripting_module(config: Config | None = None) -> Any:
    """Import ``DaVinciResolveScript`` once and keep it — the native library loads once."""
    global _module
    if _module is not None:
        return _module

    config = config or get_config()
    # Checked first: on an unsupported interpreter the import below does not raise, it
    # crashes the process, so there is nothing left to recover from.
    ensure_supported(config)
    module_path = config.script_modules / f"{MODULE_NAME}.py"
    if not module_path.is_file():
        raise ResolveUnavailableError(
            cause=f"The Resolve scripting module is not at {module_path}.",
            fix=(
                "Install DaVinci Resolve Studio, or point RESOLVE_SCRIPT_API at the "
                "Scripting directory that contains Modules/DaVinciResolveScript.py."
            ),
        )

    # DaVinciResolveScript reads these at import time to locate fusionscript.
    os.environ.setdefault("RESOLVE_SCRIPT_API", str(config.script_api))
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", str(config.script_lib))

    spec = importlib.util.spec_from_file_location(MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise ResolveUnavailableError(
            cause=f"Python could not load the scripting module at {module_path}.",
            fix=RESOLVE_FIX,
        )
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(MODULE_NAME)
    sys.modules[MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
        # DaVinciResolveScript swaps itself in sys.modules for the native fusionscript
        # module, which is where scriptapp actually lives. Read it back, or we hold a
        # shell with no API on it.
        loaded = sys.modules.get(MODULE_NAME, module)
    except Exception:
        if previous is None:
            sys.modules.pop(MODULE_NAME, None)
        else:
            sys.modules[MODULE_NAME] = previous
        raise

    if not hasattr(loaded, "scriptapp"):
        raise ResolveUnavailableError(
            cause=f"{MODULE_NAME} loaded but exposes no scriptapp(); "
            f"the native library at {config.script_lib} did not come up.",
            fix=(
                "Check that RESOLVE_SCRIPT_LIB points at fusionscript.dll inside the "
                "DaVinci Resolve install directory, and that Resolve is Studio, not free."
            ),
        )
    _module = loaded
    return loaded


def reset_module_cache() -> None:
    """Forget the imported scripting module (tests; not used at runtime)."""
    global _module
    _module = None
