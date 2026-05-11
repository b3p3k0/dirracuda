# C7 – Desktop Experimental Web UI Tab And Control Dialog

## Context

C6 delivered the complete server-rendered web UI on `feature/secure-webui`. C7 wires the desktop app into that service: a minimal `Web UI` tab in the Experimental Features dialog and a control dialog for status / start / stop / open browser / copy URL. Service state is tracked via a pidfile at `~/.dirracuda/state/webui.pid` — not only in process memory — so start/stop controls survive app close and reopen. The tab appears between `Reddit` and `Dorkbook`, giving the final order: SearXNG, Reddit, Web UI, Dorkbook, Keymaster.

## Scope

**New files:**
- `webui/service_control.py` — pidfile-based process state layer; no GUI imports
- `gui/components/experimental_features/webui_tab.py` — minimal tab (description + one button)
- `gui/components/webui_control_dialog.py` — modal control dialog

**Modified files:**
- `webui/server.py` — add `--host`/`--port` argparse so `service_control.start()` can pass non-default bind params
- `gui/components/experimental_features/registry.py` — insert `webui` entry at index 2
- `gui/components/dashboard_experimental.py` — add `open_webui_control` to context dict and function
- `gui/tests/test_experimental_features_dialog.py` — update two shifted index assertions; add five new webui tests

## Step-by-step implementation

### 1. `webui/server.py`

Add `argparse` so the service can be launched with explicit bind params:

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5480)
    args = parser.parse_args()
    run(args.host, args.port)
```

The `run()` function signature is unchanged; callers importing it directly are unaffected.

### 2. `webui/service_control.py`

Module-level constants:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVER_PATH = str(_REPO_ROOT / "webui" / "server.py")
_PID_FILE = Path.home() / ".dirracuda" / "state" / "webui.pid"
```

Path follows `webui/config.py`'s `_DEFAULT_CONFIG_PATH` pattern (hardcoded, no `shared/` dependency). `~/.dirracuda/state/` is the Layout v2 state directory (`DirracudaPaths.state_dir`).

**Ownership tri-state:**

```python
class _Ownership(enum.Enum):
    OURS = "ours"
    ALIEN = "alien"
    UNKNOWN = "unknown"
```

**`_pid_alive(pid)`** — try `psutil.pid_exists()`; fall back to `os.kill(pid, 0)`. On `PermissionError`/EPERM return `True` (process exists but not signalable). Return `False` only on `ProcessLookupError`/ESRCH.

**`_check_ownership(pid)`** — layered, exact-token match against `_SERVER_PATH`:

1. Try psutil: `tokens = psutil.Process(pid).cmdline()` → `_SERVER_PATH in tokens` → `OURS`/`ALIEN`; on `ImportError` fall through (psutil is not in `requirements.txt`)
2. Linux fallback (no deps): `Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")` → decode tokens → same exact-token check → `OURS`/`ALIEN`; on `FileNotFoundError`/`OSError` fall through
3. Return `_Ownership.UNKNOWN` — neither method available (e.g. macOS without psutil)

**`is_running(host, port)`** pipeline:

1. `_read_pid()` → None → `False`
2. `_pid_alive()` → dead → clear pidfile → `False`
3. `_check_ownership()`: `ALIEN` → clear pidfile → `False`; `OURS`/`UNKNOWN` → continue (do not clear on ambiguity)
4. Health endpoint: `GET http://host:port/health` via `urllib.request`, timeout 2 s → `True`/`False`

**`start(host, port)`** — idempotent via `is_running`; launches:

```python
cmd = [sys.executable, _SERVER_PATH, "--host", host, "--port", str(port)]
subprocess.Popen(cmd, cwd=str(_REPO_ROOT), start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

Windows path uses `CREATE_NEW_PROCESS_GROUP` instead of `start_new_session`. Writes pidfile after `Popen`.

**`stop()`** — reads host/port from pidfile record internally; pipeline:

1. `_read_pid_record()` → None → `False`
2. `_pid_alive()` → dead → clear pidfile → `False`
3. `_check_ownership()`:
   - `ALIEN` → clear pidfile → `False` (confirmed PID reuse; do not signal)
   - `UNKNOWN` → `False` — do not signal, do not clear (architecture guardrail: ambiguous state is manual-stop-required)
   - `OURS` → `os.kill(pid, signal.SIGTERM)` on all platforms → clear pidfile → `True`

`CTRL_BREAK_EVENT` is not used — it is only valid for console-group processes.

### 3. `gui/components/experimental_features/webui_tab.py`

Follows `reddit_tab.py` pattern:

```python
class WebUITab:
    def __init__(self, parent, context)
    def _build(self, frame)          # description label + "Open Web UI Control" (button_primary)
    def _invoke_open_control(self)   # context.get("open_webui_control") and call it; no-op if absent

def build_webui_tab(parent, context) -> tk.Widget
```

No status polling in the tab itself — that lives in the control dialog.

### 4. `gui/components/webui_control_dialog.py`

Modal `Toplevel` following `reddit_grab_dialog.py` discipline: `transient` → `grab_set` → `ensure_dialog_focus` → kick off `_refresh_status` thread.

Host/port sourced lazily from `webui.config.load_config()` (falls back to `127.0.0.1:5480` on any import or parse error) so the dialog reflects the operator's actual config.

All imports from `webui.service_control` and `webui.config` are **inside methods** — the dialog stays importable when webui deps are absent.

**`_on_stop`** re-checks `is_running()` after the stop attempt and shows a warning if the service is still running:

> "Service is still running. Ownership could not be confirmed — use system tools to stop the process."

Styles: `button_primary` for Start, `button_danger` for Stop, `button_secondary` for Open in Browser / Copy URL / Close. All messageboxes via `gui.utils.safe_messagebox`.

### 5. `gui/components/experimental_features/registry.py`

Insert `webui` at index 2 (after `reddit`, before `dorkbook`):

```python
from gui.components.experimental_features.webui_tab import build_webui_tab
...
ExperimentalFeature("webui", "Web UI", build_webui_tab),
```

Final order: `se_dork`(0), `reddit`(1), `webui`(2), `dorkbook`(3), `keymaster`(4).

### 6. `gui/components/dashboard_experimental.py`

Add to `handle_experimental_button_click` context dict:

```python
"open_webui_control": lambda: open_webui_control(widget),
```

Add function (lazy import, matching `open_keymaster` pattern):

```python
def open_webui_control(widget) -> None:
    from gui.components.webui_control_dialog import show_webui_control_dialog
    show_webui_control_dialog(widget.parent)
```

### 7. `gui/tests/test_experimental_features_dialog.py`

Update two shifted index assertions:
- `test_registry_dorkbook_after_reddit`: `features[2]` → `features[3]`
- `test_registry_keymaster_after_dorkbook`: `features[3]` → `features[4]`

Add five new tests:
1. `test_registry_webui_tab_exists` — `"webui" in feature_ids`
2. `test_registry_webui_label` — `"Web UI" in labels`
3. `test_registry_tab_order_exact` — full ID list equals `["se_dork","reddit","webui","dorkbook","keymaster"]`
4. `test_webui_tab_callback_invoked` — `WebUITab.__new__`, inject `open_webui_control` callback, assert called
5. `test_webui_tab_silent_when_no_callback` — empty context, `_invoke_open_control()` must not raise

## Design decisions

- **Pidfile at `~/.dirracuda/state/webui.pid`** — consistent with Layout v2 `state_dir`; same hardcoded-path convention as `webui/config.py`'s `_DEFAULT_CONFIG_PATH`; no `shared/` import from the webui package.
- **Ownership tri-state, not bool** — avoids false-alien or false-ours on psutil absence or process inspection errors. `UNKNOWN` is always treated as manual-stop-required, never as grounds to send a signal or clear the pidfile.
- **`/proc/<pid>/cmdline` Linux fallback** — provides ownership verification without psutil on the primary target platform.
- **Exact-token match on `_SERVER_PATH`** — substring checks for `"server.py"` and `"webui"` are too broad; exact absolute-path token match reduces false positives.
- **`SIGTERM` on all platforms** — `CTRL_BREAK_EVENT` is only valid for processes sharing a console group; detached `start_new_session` launches do not qualify.
- **No systemd in C7** — pidfile + health-check ownership satisfies the "survives close/reopen" requirement. C8 adds systemd if scoped.

## Validation

```bash
./venv/bin/python -m py_compile \
  webui/server.py \
  webui/service_control.py \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/webui_tab.py \
  gui/components/webui_control_dialog.py \
  gui/components/dashboard_experimental.py

xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q

./venv/bin/python -m pytest --tb=short -q
```

Expected: py_compile clean; 49 passed on the focused suite; full suite ≥ 197 webui tests passed, pre-existing `test_s10_se_dork_probe_task_lifecycle_success` failure unrelated to C7.

## HI test

- Launch `./dirracuda`
- Open Experimental Features dialog
- Confirm `Web UI` tab appears between `Reddit` and `Dorkbook`
- Click `Open Web UI Control`
- Verify status check runs, Start/Stop/Open in Browser/Copy URL buttons behave correctly
