# Native server anatomy — `ResolveMCP.exe`

Research for #265 (map #263). Facts only, each with how it was established.

**Confidence flags**

- `measured` — observed on this box this session (Resolve Studio 21.1.0.17,
  Windows 11, 2026-09-15).
- `source` — read from the server's own shipped Python source, which is a
  plain zipapp inside `ResolveMCP.exe` (see "Reading the source" below).
- `inferred` — follows from the two above, not directly observed.

Paths are the live box's: `C:\Program Files\Blackmagic Design\DaVinci Resolve\`
(`INSTALL` below).

## 1. What the binary is

| Fact | Flag |
| --- | --- |
| `INSTALL\ResolveMCP.exe` is a stdio JSON-RPC MCP server. `--help` lists exactly three flags: `--test`, `--dump-tools`, `--pretty`. | measured |
| It is a launcher plus a zipapp: `zipfile.ZipFile(r"…\ResolveMCP.exe")` opens it (19 entries) and yields `resolve_mcp_server/{__init__,__main__,server,tools_script,tools_docs,tools_dctl_lut}.py` plus a `resources` package. | measured |
| The code runs in a **child process**: `sys.executable` is `INSTALL\ResolvePython\ResolvePython.exe` (CPython **3.14.4**, MSC v.1944 x64), `sys.argv[0]` is `…\ResolveMCP.exe`, and `os.getppid()` is the `ResolveMCP.exe` pid. `sys.path[0]` is the exe itself. | measured |
| So each connected client shows **one `ResolveMCP.exe` + one `ResolvePython.exe`** in `tasklist`. Five pairs were live during this session (five MCP clients); every `ResolvePython.exe` has `fusionscript.dll` loaded. | measured |
| `--test` performs a full `initialize` + `tools/list` handshake against itself and exits 0; it needs no stdin. Protocol version `2024-11-05`, `serverInfo` `{"name":"davinci_resolve","version":"21.1"}` — the version string is Resolve's, not the server's. | measured |
| 14 tools: `launch_resolve`, `get_resolve_status`, `get_whats_new`, `get_scripting_api`, `search_scripting_api`, `run_script`, `run_script_unsafe`, `get_scripting_docs`, `list_dctls`, `list_luts`, `update_dctl`, `delete_dctl`, `delete_lut`, `generate_lut`. | measured |
| The server writes a log to `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\logs\mcp.log` at DEBUG — unless `BMD_IS_MCPB=1` is set, which suppresses the file handler. | source |
| `initialize` returns different `instructions` depending on whether Resolve is reachable at that moment ("… is running" vs "… is not running, call the `launch_resolve` tool to start it"). | source (both branches), measured (the "is running" branch) |

### The `.mcpb` bundle is a different front end

`INSTALL\DaVinciResolve.mcpb` is a zip: `manifest.json`, `package.json`,
`icon.png`, `LICENSE`, `server/index.js` (375 lines).

| Fact | Flag |
| --- | --- |
| `manifest.json` declares `"server": {"type": "node", "entry_point": "server/index.js"}`, `manifest_version` 0.3, `version` "1.0", runtime `node >=18`, platforms darwin/win32/linux. The tool list matches the binary's. | measured |
| `index.js` is a "thin MCP wrapper that proxies to the ResolveMCP binary" (its own comment). It finds the binary at `%ProgramFiles%\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`, falling back to a `reg.exe query` of the registered install dir; `RESOLVE_MCP_PATH` overrides. It spawns the binary with `BMD_IS_MCPB=1`. | measured |
| Proxy-only behaviour: the binary is **spawned lazily on the first `tools/call`** and **killed after 5 min idle** (`IDLE_TIMEOUT_MS`), with a 30 s init timeout (`INIT_TIMEOUT_MS`). `tools/list` is served from a cached `--dump-tools` run. | measured (constants read from the file) |
| **Claude Code on this box does not use the bundle.** `~/.claude.json` registers `"DaVinci Resolve"` as `{"command": "C:\\Program Files\\…\\ResolveMCP.exe", "args": []}`. So no idle timeout, no lazy spawn, no `BMD_IS_MCPB` — the binary lives for the session and does log to `mcp.log`. | measured |

### Reading the source

The whole server is readable, which is why many rows below are `source`
rather than `inferred`:

```python
import zipfile, os
z = zipfile.ZipFile(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe")
z.extractall(os.environ["TEMP"] + r"\resolvemcp_src")
```

`inspect.getsource(sys.modules["resolve_mcp_server.tools_script"])` works too
(29,858 chars).

## 2. Process model and persistence

The two script tools have **different** execution models. This is the single
most consequential fact in this document.

| Fact | Flag |
| --- | --- |
| `run_script_unsafe` runs **in the server's own worker process**, on a daemon `threading.Thread`, via `exec(script, namespace)`. `os.getpid()` returned the same pid (25732) across every unsafe call in this session. | source + measured |
| `run_script` runs in a **fresh child process per call**: `subprocess.Popen([sys.executable, '-c', _SANDBOX_SCRIPT], …)`, script fed on stdin, JSON response on stdout. | source |
| **No state survives between calls, for either tool.** A module-level name set in call 1 (`PROBE_GLOBAL`) was absent in call 2; an attribute stuck on `builtins` was likewise gone. Each call gets a fresh `namespace` dict; for `run_script` the whole interpreter is new as well. | measured |
| Consequence: imports, caches and warm-up cost are paid per call. There is no session, no cross-call handle, no way to hold state server-side other than the filesystem (unsafe only). | inferred |
| A `run_script_unsafe` script that exceeds its timeout is **not killed** — the thread is a daemon and keeps running, holding the Resolve connection. The server logs `run_script_unsafe timed out after Ns; leaked thread keeps running in the background` and returns the timeout error. | source |
| A `run_script` timeout **is** enforced: `proc.kill()` then `proc.wait()`. | source |

## 3. Attach: how the Resolve handle is obtained

| Fact | Flag |
| --- | --- |
| Same mechanism this repo uses: `import DaVinciResolveScript as dvr; dvr.scriptapp('Resolve')`. `DaVinciResolveScript.__file__` and `fusionscript.__file__` both resolve to `INSTALL\fusionscript.dll`. | source + measured |
| The injected `resolve` and `project` are `BlackmagicFusion.PyRemoteObject` (`type(resolve).__module__ == 'BlackmagicFusion'`) — the ordinary scripting proxy type, not anything MCP-specific. | measured |
| No port is opened *by the MCP server*. The transport is Resolve's own script server: `fuscript.exe` (pid 19384 here) **listens on `0.0.0.0:1144`** and Resolve.exe holds an established connection to it. `ResolveDebug.txt` logs `Fusion | INFO | Started script server: 19384`. `fusionscript.dll` in each `ResolvePython.exe` is the client of that port. | measured |
| So "port 1144 or fusionscript.dll" is a false dichotomy: `fusionscript.dll` **is** the 1144 client. | inferred |
| The server keeps one long-lived handle (`self._resolve`). `id(resolve)` was identical (2744955916112) across separate `run_script_unsafe` calls. | source + measured |
| `run_script`'s child, by contrast, **re-attaches from scratch on every call** (`dvr.scriptapp('Resolve')` in the sandbox preamble, before the sandbox is installed), and asserts `resolve` truthy with `assert resolve, 'DaVinci Resolve is not running'`. | source |
| Before every Resolve-touching tool call the server runs `_reconnect_if_needed()`: if `getattr(self._resolve, 'GetCurrentPage', None) is None` it logs `Lost connection to DaVinci Resolve, reconnecting.` and re-attaches. This is the same dead-handle probe pattern this repo uses. | source |
| With Resolve not running, a Resolve-touching tool raises `DaVinci Resolve is not running, start it with the \`launch_resolve\` tool.` (logged at error). `launch_resolve` spawns `BMD_RESOLVE_APP_PATH` (env var, set to `INSTALL\Resolve.exe` in the server's environment) and polls `scriptapp` once a second up to the launch timeout, then raises `Resolve did not respond within Ns`. | source |
| **External scripting preference.** The README's Configuration section (fetched via `get_scripting_docs`) documents Preferences > System > General > External scripting (None / Local / Network) as the gate on "whether external scripts can connect to Resolve". The native server attaches from a separate process through the same `fusionscript.dll` + 1144 script server as any external script, so the same gate applies. Not proven by toggling: the setting's on-disk value was not found under `%APPDATA%\…\DaVinci Resolve\Support\`, and flipping it to None is a UI action that would have severed this session. | source + inferred |
| The setting is currently permissive enough on this box: 1144 is listening and attaches succeed. | measured |

## 4. The `run_script` sandbox, exactly

Mechanism (`source`): the child connects to Resolve **first**, then installs a
`sys.addaudithook` hook, strips dangerous modules out of `sys.modules` and out
of every `__globals__` reachable via `object.__subclasses__()` (an explicit
`__subclasses__` → `__globals__` escape defence), then `exec`s the user script
in a namespace whose `__builtins__` is a filtered copy.

**Blocked imports** (`_BLOCKED_IMPORTS`, raises `PermissionError: import not
allowed: <mod>`): `gc`, `ctypes`, `subprocess`, `os`, `posix`, `nt`,
`_posixsubprocess`, `signal`, `shutil`, `pathlib`, `importlib`, `runpy`,
`multiprocessing`, `_thread`, `socket`, `sys`, `traceback`, `linecache`.

**Removed builtins**: `open`, `breakpoint`, `exec`, `eval`, `compile`,
`input`, `memoryview`, `globals`, `locals`, `vars`. (`__import__`, `dir`,
`print`, `getattr`, `type` remain.)

**Blocked audit events**: `subprocess.Popen`, `ctypes.dlopen`,
`webbrowser.open`, `socket.connect`, `socket.bind`, `socket.sendto`; plus
`open` for any path outside the interpreter directory
(`PermissionError: filesystem access not allowed: <path>`).

Measured import matrix (`measured`):

| Import | Result |
| --- | --- |
| `json`, `re`, `time`, `math`, `io`, `types`, `datetime`, `collections`, `inspect` | ok |
| `os`, `sys`, `pathlib`, `subprocess`, `socket`, `ctypes`, `gc`, `shutil`, `importlib`, `multiprocessing`, `signal`, `_thread`, `traceback`, `linecache` | `PermissionError: import not allowed: <name>` |
| `threading`, `random`, `platform`, `tempfile`, `glob` | fail **transitively** — `PermissionError: import not allowed: os` |
| `logging` | `PermissionError: import not allowed: sys` |
| `zipfile` | `PermissionError: import not allowed: importlib` |
| `urllib.request` | `NameError: name '_os' is not defined` — collateral of the `__globals__` scrub, not a clean refusal |

So the practical sandbox is much tighter than its 18-name blocklist: anything
that imports `os` at module scope is unreachable, and `open` is gone outright.

### Limits, capture and shaping

| Fact | Flag |
| --- | --- |
| Timeout: default **10 s**, `timeout` parameter clamped with `min(int(timeout), 60)`. Requesting 61 s with a 75 s sleep returned `{"error": "Script timed out after 60s. Avoid long iterations; fetch only what you need."}` — clamped, not rejected. | source + measured |
| `print()` is captured: `sys.stdout` is swapped for an `io.StringIO` and returned as `response["output"]`. | source + measured |
| A raising script returns **structured JSON**, not a crash: `{"error": "<traceback>", "output": "<partial prints>"}`. The traceback is trimmed to frames whose filename is `<script>`, so no sandbox frames leak, and `linecache` is pre-seeded so the offending source line is shown. Exit code 1, payload on stderr. | source + measured |
| Measured example: a `raise ValueError("…")` inside a helper returned both frames (`line 5, in <module>` / `line 4, in boom`) plus the two `print` lines in `output`. | measured |
| A `result` that is not JSON-serialisable comes back as `repr(result)` — `{"result": "{'obj': <NotJson object at 0x…>}"}` — silently, with no type error. | source + measured |
| Empty script: `{"output": "(script completed with no output or result)"}`. | source |
| **No server-side result size cap.** `result = "x" * 2*1024*1024` came back whole (2,097,166 characters). The truncation seen in a Claude session is the *client's*: Claude Code spilled it to `…\tool-results\mcp-DaVinci_Resolve-run_script-<ts>.txt`. Tools with an `as_file` flag (`get_scripting_api`, `get_scripting_docs`) exist because of that client-side ceiling; they write to `%TEMP%\resolve-mcp\` and the server, not the client, picks the path. | measured + source |

`run_script_unsafe` shares the timeout clamp, the print capture, the
traceback trimming and the `repr` fallback, and has **no** sandbox: full
filesystem, network and subprocess access in the server's own process.

## 5. Handle staleness

| Fact | Flag |
| --- | --- |
| `project` is **re-fetched on every call** — `resolve.GetProjectManager().GetCurrentProject()` in the unsafe path, and the same line in the sandbox preamble. It cannot go stale. | source |
| `id(project)` differed on every call while `id(resolve)` stayed fixed, confirming the asymmetry. | measured |
| The injected `project` is a *new proxy object* each call, so `GetCurrentProject() is project` is `False` and `==` is `False` even for the same project. Identity comparison on Resolve proxies is meaningless here. | measured |
| A project switch made by another process is picked up with no reconnect: mid-session the current project changed from `2026-06-27_Dave_Ads (Copy)` to `mcp-tests-zinc` (this repo's live tier switched it) and the very next `run_script` reported `mcp-tests-zinc` / page `deliver`. | measured |
| The `resolve` handle is the only thing that can go stale, and `_reconnect_if_needed()` runs before every Resolve-touching tool call. No probe forced a handle death this session (it would mean killing Resolve). | source |

## 6. Coexistence with this repo's server

| Fact | Flag |
| --- | --- |
| Both attach the same way (`fusionscript.dll` → `fuscript.exe`:1144), and Resolve's script server accepts many clients: five `ResolvePython.exe` clients plus `Resolve.exe` itself were attached simultaneously. | measured |
| **Live tier ran to completion with the native server connected**: `uv sync` in the worktree, then `uv run pytest -m live` — `1 failed, 34 passed, 10 skipped, 2653 deselected in 205.67s`. The tests **ran**; they did not skip out on an unreachable Resolve. | measured |
| During that run, a `run_script` call through the native server answered normally and observed the live tier's own project switch (`mcp-tests-zinc`, page `deliver`). Two independent attaches, in flight at the same time, both working. | measured |
| The one failure was `tests/test_live_smoke.py::test_the_shipped_default_preset_is_a_built_in_on_this_machine` — a render job that ended `Failed` (`render_queue_failed`, `Failed to render background job.`), not an attach or connection error. Whether the native server's presence contributed is **not established**: a control run needs the native server disconnected, which this session could not do (it is this session's own MCP server). | measured (the failure), open (the cause) |

The live record for #265 is on the ticket, per the repo's rule.

## 7. What this means for design (no new facts)

- There is no session state to build on: every `run_script` is a cold
  interpreter. Anything expensive (imports, analysis) either goes in the
  script body every time, or lives outside the script.
- The 60 s ceiling is hard and the unsafe path leaks a thread when it is hit,
  so long work cannot run inside either tool.
- The `run_script` sandbox rules out this repo's whole worker layer (ffmpeg,
  audio-separator, torch, file I/O) — that work would need
  `run_script_unsafe`, which is an unsandboxed `exec` in Blackmagic's process.
- Both servers can attach at once, so the two are not mutually exclusive on
  one box.

## 8. Addendum: a second probe pass

A duplicate agent probed the same server independently and found four
facts the sections above lack. All measured on the same box and build.

| Fact | Flag |
| --- | --- |
| **The `run_script` blocklist leaks statically linked builtins.** `import winreg` and `import msvcrt` both succeed inside `run_script`; neither the blocked-imports set nor the `open` audit hook sees them, so the "sandboxed" tool has registry read *and write*. Also allowed and unlisted: `site`, `atexit`, `inspect`, bare `urllib`, `codecs`, `io`, `base64`, `csv`, `struct`, `string`, `textwrap`, `datetime`, `dataclasses`, `itertools`, `functools`, `types`, `copy`. | measured |
| **No C-extension module loads in the sandbox.** Every module that needs the file-based import path dies with the `_os` NameError — `hashlib`, `decimal`, `unicodedata`, `_socket`, `xml.etree.ElementTree`, `http.client` — a third failure mode beside the blocklist's `PermissionError` and the transitive `import not allowed: os`/`sys` that takes down `random`, `statistics`, `platform`, `typing`, `logging`, `tempfile`, `threading`. Section 4 lists the NameError as a one-off for `urllib.request`; it is general. | measured |
| **A timeout discards buffered output.** `print("before sleep")` then `time.sleep(15)` at `timeout=10` returned only `{"error": "Script timed out after 10s…"}`, no `output` key: the child is killed and its stdout dropped. The partial-output preservation in "Limits, capture and shaping" holds for exceptions, not timeouts. | measured |
| **1144 is only the rendezvous.** The scripting README (`Network and Headless Access`): "the return connection is dynamically allocated in the 49152..65535 range." netstat agrees — the server's `ResolvePython.exe` holds an ESTABLISHED connection to `127.0.0.1:49152`, listened on by `Resolve.exe`; 1144 shows only as short-lived `TIME_WAIT`. A firewall or port story built on 1144 alone is incomplete. | measured + README |

**Handle death is a hang, and the cause can live outside both servers.**
After Resolve was restarted mid-session, its scripting server `fuscript.exe`
survived as an orphan (parent pid dead) still holding port 1144. Every new
attach handshook with it and hung: the native `get_resolve_status` never
returned, `ResolveMCP.exe --test` timed out at 40 s, and the MCP client's
reconnect timed out at 30 s. Killing the orphan un-wedged the handshake, but
the live Resolve had never bound 1144, so a full Resolve relaunch was needed.
The native server has no probe, no deadline and no reconnect in front of the
attach; this repo's connection layer has all three. Measured 2026-09-15.

One caveat on section 6. The second pass could not reproduce a python.org
3.12 attach from the repo venv inside its own shell sandbox (no output, no
CPU for four minutes before it was killed) — inconclusive, not a
contradiction: the live-tier run in section 6 *is* that attach, made by the
repo's own loader under the registered 3.12, with the native server
connected throughout.
