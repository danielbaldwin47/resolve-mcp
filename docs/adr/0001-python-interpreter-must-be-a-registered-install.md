# ADR 0001 — The server must run on a registered CPython install, not a uv-managed one

- **Status**: accepted (amends the runtime decision in [#11](https://github.com/danielbaldwin47/resolve-mcp/issues/11), restated in the build spec [#22](https://github.com/danielbaldwin47/resolve-mcp/issues/22))
- **Date**: 2026-08-05
- **Context**: P1 ([#23](https://github.com/danielbaldwin47/resolve-mcp/issues/23)) — first time anything attached to real Resolve

## Context

#11 settled the runtime as "Python 3.11 x64 pinned, uv-managed venv + lockfile". The
implicit reading — let uv install and manage the interpreter too — does not work.

`fusionscript.dll` does not import a `pythonXX.dll`; its PE import table names no Python
library at all, so it resolves the Python C API at runtime by finding an interpreter
itself. Under a redistributable standalone interpreter (python-build-standalone, which is
what `uv python install` provides) that lookup ends in a **Windows access violation** —
the process dies. There is no exception to catch and nothing for the connection manager
to recover from: an MCP server that touches Resolve on such an interpreter simply
disappears mid-session.

Measured on this machine against Resolve Studio 21.0.3, loading the extension module:

| Interpreter | Result |
| --- | --- |
| python.org 3.12.10 (PEP 514-registered, `%LOCALAPPDATA%\Programs\Python\Python312`) | loads; `scriptapp("Resolve")` returns a live handle |
| uv-managed 3.11.15 (python-build-standalone) | access violation, process killed |
| uv-managed 3.13.5 (python-build-standalone) | access violation, process killed |

The version is not the variable — the build is. `ctypes.CDLL` on the same DLL succeeds
under all three; only the extension-module initialisation crashes.

## Decision

1. **uv stays** for dependency resolution, locking and the venv. Only the *interpreter*
   changes: it must be a PEP 514-registered CPython (a python.org installer build), and
   the venv is created against it (`uv venv --python <path-to-python.exe>`).
2. **The interpreter is checked before the scripting library is loaded.**
   `resolve_mcp.interpreter.ensure_supported` compares `sys.base_prefix` against the
   `SOFTWARE\Python\PythonCore\*\InstallPath` entries in HKCU and HKLM, and raises a
   structured `unsupported_interpreter` error if there is no match. The check is cheap,
   runs once per attach, and is the only way to turn a fatal crash into an error message.
   `RESOLVE_MCP_ALLOW_ANY_PYTHON=1` bypasses it for anyone retesting the finding.
3. **`requires-python` is widened to `>=3.11,<3.13`.** 3.11 remains the target — #11
   chose it as the safe overlap for the P3 analysis stack (torch-CUDA, beat_this,
   audio-separator, PANNs) and nothing here disturbs that. The widening exists so the live
   path is usable on a machine whose only registered interpreter is 3.12, which is how P1
   was verified against real Resolve.

The registry match is a proxy, not a proven cause: what was measured is that registered
python.org builds work and standalone builds crash. It is the discriminator that matched
every data point, and it fails safe — an unrecognised interpreter is refused with an
explanation rather than trusted into a crash.

## Consequences

- Setup gains one manual step: install CPython from python.org before creating the venv.
  `uv python install` is *not* sufficient, and this is easy to do by accident.
- The unit suite is unaffected — it runs against the fake seam on any interpreter,
  including uv-managed ones, because it never loads the scripting library.
- The live smoke tier only runs on a registered interpreter; on any other it skips with
  the structured cause rather than taking pytest down with it.
- Open: whether to install python.org **3.11** (restores the exact #11 pin, keeps the P3
  stack on its verified version) or to standardise on the **3.12** already installed
  (verified end-to-end against Resolve today, but moves the analysis stack off its pinned
  version). P1 needs neither to be settled; P3 does.
