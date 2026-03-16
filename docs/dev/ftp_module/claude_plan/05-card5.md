# Card 5: FTP Probe Snapshot and Browser Download MVP

> This file is the implementation plan for `docs/dev/ftp_module/claude_plan/05-card5.md`.
> On implementation start, write this content verbatim to that path.

---

## 1. Context and Card 5 Scope

### Why this card exists

Cards 1–4 delivered FTP discovery end-to-end: dashboard split (Card 1), FTP CLI/workflow skeleton (Card 2), DB schema and persistence (Card 3), and reliable Shodan + anonymous verification pipeline (Card 4). Operators can now run an FTP scan and see discovered hosts persisted to the database.

Card 5 closes the loop between discovery and analyst workflow: the operator needs to **browse** a discovered FTP server's directory tree and **download** files safely, just as they can with SMB via `FileBrowserWindow`. It also adds the **probe snapshot** pattern so the indicator-matching and caching machinery already built for SMB works for FTP too.

### Card 5 definition of done (verbatim from task cards)

1. Operator can browse FTP directory tree from app.
2. Operator can download files to quarantine.
3. FTP probe snapshot is written and re-openable.

### Regression scope (verbatim from task cards)

1. SMB browse/download unchanged.
2. Cancel/timeout behavior remains responsive.

### Out of scope (verbatim from task cards)

- Full normalized artifact DB persistence.
- Content ranking / value scoring.

---

## 2. Current-State Analysis

### What exists and is directly reusable

| Component | Location | How Card 5 Uses It |
|---|---|---|
| `SMBNavigator` | `shared/smb_browser.py` | Structural template; import its `Entry`, `ListResult`, `DownloadResult`, `ReadResult` dataclasses directly |
| `FileBrowserWindow` | `gui/components/file_browser_window.py` | Direct template for `FtpBrowserWindow`; mirrors layout, thread model, cancel wiring |
| `build_quarantine_path` | `shared/quarantine.py` | Used as-is by FTP download; pass `share_name="ftp_root"` |
| `log_quarantine_event` | `shared/quarantine.py` | Used as-is for activity logging |
| `probe_runner.py` snapshot format | `gui/utils/probe_runner.py` | FTP probe snapshot mirrors exact `shares` list structure so `probe_patterns.py` needs no changes |
| `probe_patterns.py` | `gui/utils/probe_patterns.py` | Reused unchanged; FTP snapshot uses synthetic `"ftp_root"` share name |
| `probe_cache.py` | `gui/utils/probe_cache.py` | API exactly mirrored in new `ftp_probe_cache.py`; different cache dir |
| `get_ftp_servers()` | `gui/utils/database_access.py` | FTP server picker reads from this |
| `try_anon_login` pattern | `commands/ftp/verifier.py` | Confirms `ftplib.FTP.login()` (anonymous) is the correct approach |
| Header `"📡 FTP Servers"` button | `gui/components/dashboard.py` | Launch point: calls `_open_drill_down("ftp_server_list")` |

### What does not yet exist (Card 5 must create)

| New File | Purpose |
|---|---|
| `shared/ftp_browser.py` | `FtpNavigator` class — read-only FTP list/download/cancel |
| `gui/utils/ftp_probe_cache.py` | Cache helpers for `~/.smbseek/ftp_probes/` |
| `gui/utils/ftp_probe_runner.py` | `run_ftp_probe()` — walks FTP root, generates snapshot |
| `gui/components/ftp_browser_window.py` | `FtpBrowserWindow` — interactive Tkinter FTP browser |
| `gui/components/ftp_server_picker.py` | `FtpServerPickerDialog` — pick a server from DB to browse |

### What is modified (not created)

| Modified File | Change |
|---|---|
| `conf/config.json.example` | Add `ftp_browser` section |
| `gui/components/dashboard.py` | Add `"📡 FTP Servers"` button to header; calls `_open_drill_down("ftp_server_list")` |

### Key absence confirmed: no FTP server list window yet

The `server_list_window/` package is deeply SMB-specific (imports `FileBrowserWindow`, `SMBSeekWorkflowDatabase`, SMB credential derivation from `details.py`). Do **not** modify it. Use the lightweight `FtpServerPickerDialog` instead for the MVP launch path.

---

## 3. Proposed Design

### Architecture

```
Dashboard ("📡 FTP Servers" button click → _open_drill_down → drill_down_callback → xsmbseek)
  └─► FtpServerPickerDialog  [gui/components/ftp_server_picker.py]
        └─► FtpBrowserWindow  [gui/components/ftp_browser_window.py]
              ├─ FtpNavigator  [shared/ftp_browser.py]    — interactive browse/download
              ├─ run_ftp_probe()  [gui/utils/ftp_probe_runner.py]  — background on-open
              │    ├─ FtpNavigator (separate instance)
              │    └─ save_ftp_probe_result()  [gui/utils/ftp_probe_cache.py]
              └─ build_quarantine_path / log_quarantine_event  [shared/quarantine.py]
```

### Key design decisions

**Decision 1: Import dataclasses from `shared/smb_browser.py`, do not redefine.**
`Entry`, `ListResult`, `DownloadResult`, `ReadResult` are protocol-agnostic. Importing them avoids duplication and lets shared tooling (e.g., any future unified browser) work with both SMB and FTP data.

**Decision 2: FTP probe snapshot uses `"ftp_root"` as a synthetic share name.**
`probe_patterns.py`'s `_iter_snapshot_paths()` iterates `snapshot["shares"]`. By putting FTP's root listing into a single share entry named `"ftp_root"`, the entire indicator-matching pipeline works unchanged. Zero modifications to `probe_patterns.py`.

**Decision 3: Two separate `FtpNavigator` instances — probe + interactive.**
`run_ftp_probe()` creates its own internal navigator, walks, and disconnects. `FtpBrowserWindow._navigator` is a separate long-lived connection created lazily on first navigation. No shared state, no cross-thread contention.

**Decision 4: `FtpBrowserWindow` uses `/` path separator throughout.**
FTP is POSIX-path native. The SMB `\` normalization in `smb_browser.py` does not apply. All path joins use `PurePosixPath`. Display, navigation, and download all use `/`.

**Decision 5: MLSD-first with `LIST` fallback for directory listing.**
MLSD is the modern standard (RFC 3659) and returns structured `type/size/modify` facts. Many real-world servers (vsftpd in compatibility mode, old IIS, FileZilla pre-1.0) do not implement it. The LIST fallback parses Unix `ls -l` and Windows/DOS `DIR` formats.

**Decision 6: After any cancelled RETR, set `self._ftp = None` and reconnect on next use.**
Partial RETR leaves the control connection in an indeterminate state. The `_ensure_connected()` guard with a NOOP keepalive handles this transparently.

**Decision 7: No new DB writes in Card 5.**
The probe snapshot goes to the JSON cache only (`~/.smbseek/ftp_probes/`). No new schema changes, no new DB table writes. This is explicitly out of scope.

**Decision 8: `FtpServerPickerDialog` stays open after launching a browser.**
This lets the operator open multiple FTP servers simultaneously, consistent with how the SMB server list window works.

---

## 4. Exact Patch Plan (Ordered, Low-Risk Sequence)

### Step 1 — `shared/ftp_browser.py` (NEW)

Depends on: `shared/smb_browser.py` (existing)

```
shared/
  ftp_browser.py   ← NEW
```

**Module-level imports:**
```python
import ftplib
import io
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional

from shared.smb_browser import Entry, ListResult, DownloadResult, ReadResult
```

> `ReadResult` is confirmed exported in `shared/smb_browser.py` (class definition near top of file, `__all__` at the bottom). Import directly — no local fallback definition.

**`FtpCancelledError`** — simple `Exception` subclass, signals clean abort from RETR callback.

**`FtpNavigator` constructor parameters** (match `SMBNavigator` naming):

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `connect_timeout` | `float` | `10.0` | Socket connect timeout (seconds) |
| `request_timeout` | `float` | `15.0` | Per-operation socket timeout |
| `max_entries` | `int` | `5000` | Max entries returned by `list_dir` |
| `max_depth` | `int` | `12` | Max path depth enforced |
| `max_path_length` | `int` | `1024` | Max path string length |
| `max_file_bytes` | `int` | `26_214_400` | 25 MB download ceiling |

**Instance state:**
```python
self._ftp: Optional[ftplib.FTP] = None
self._host: str = ""
self._port: int = 21
self._cancel_event = threading.Event()
```

**`connect(host, port=21) → None`**
1. `self._cancel_event.clear()`
2. `ftp = ftplib.FTP(timeout=self.connect_timeout)`
3. `ftp.connect(host=host, port=port, timeout=self.connect_timeout)`
4. `ftp.encoding = 'utf-8'`
5. `ftp.login()` — anonymous (USER anonymous / PASS anonymous@)
6. `ftp.set_pasv(True)` — always passive; active mode blocked by NAT
7. `ftp.sock.settimeout(self.request_timeout)`
8. Store `self._ftp = ftp`, `self._host = host`, `self._port = port`

**`_ensure_connected() → None`**
```python
def _ensure_connected(self) -> None:
    if self._ftp is not None:
        try:
            self._ftp.voidcmd("NOOP")   # keepalive / connection check
            return
        except Exception:
            self._ftp = None
    self.connect(self._host, self._port)
```
Call at the top of `list_dir`, `download_file`, `read_file`, `get_file_size`.

**`list_dir(path: str) → ListResult`**
1. Call `_ensure_connected()`
2. Call `_enforce_limits(path)` (depth + length checks)
3. Try `MLSD`:
   ```python
   raw = list(self._ftp.mlsd(path, facts=["type", "size", "modify"]))
   ```
   Filter `type in ("cdir", "pdir")` and names `"."` / `".."`.
   Parse `modify` with `datetime.strptime(facts["modify"], "%Y%m%d%H%M%S")` → `.timestamp()` for `modified_time`.
   If `len(entries) >= self.max_entries`: set `truncated = True`, break.
4. On `ftplib.error_perm` (MLSD not supported): fall back to `_list_via_LIST(path)`.
5. If `self._cancel_event.is_set()`: append `"Operation cancelled."` to `warning`.
6. Return `ListResult(entries, truncated, warning)`.

**`_list_via_LIST(path) → Tuple[List[Entry], bool, Optional[str]]`**

Collect lines via `self._ftp.retrlines(f"LIST {path}", lines.append)`.
Parse each line with two regexes:

```python
_UNIX_RE = re.compile(
    r'^([d\-lbcps])[rwxsStT\-]{9}\s+\d+\s+\S+\s+\S+\s+'
    r'(\d+)\s+'                                # size
    r'(\w{3}\s+\d{1,2}\s+[\d:]{4,5})\s+'     # date
    r'(.+)$'                                   # name
)
_DOS_RE = re.compile(
    r'^(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}[AP]M)\s+'
    r'(<DIR>|\d+)\s+(.+)$'
)
```

For Unix: `is_dir = line[0] == 'd'`, `size = int(match.group(2))`, name from group 4.
For DOS: `is_dir = match.group(3) == '<DIR>'`, `size = int(group(3)) if not is_dir else 0`.
`modified_time` from LIST is imprecise (no seconds) — parse best-effort, store `None` on failure.
Catch `UnicodeDecodeError` per line; skip malformed entries with a warning.

**Exception types** (define at module level before `FtpNavigator`):
```python
class FtpCancelledError(Exception):
    """Raised by RETR callback when cancel_event is set."""

class FtpFileTooLargeError(Exception):
    """Raised when remote file exceeds configured size limit."""
```

**`download_file(remote_path, dest_dir, progress_callback=None) → DownloadResult`**

`DownloadResult` is imported from `shared/smb_browser.py` and has no error/status fields — it is success-only. All error paths **raise**; callers catch.

1. `_ensure_connected()`
2. `_enforce_limits(remote_path)`
3. Size pre-flight: `self.get_file_size(remote_path)` → if `size > max_file_bytes`: **raise `FtpFileTooLargeError(f"...")`**.
4. Resolve dest: `dest_path = Path(dest_dir) / PurePosixPath(remote_path).name`
5. If `dest_path.exists()`: raise `FileExistsError`.
6. Define `_callback(chunk)` that: checks `_cancel_event` → **raise `FtpCancelledError`** if set; writes to open file, accumulates `bytes_written`, calls `progress_callback(bytes_written, file_size)`.
7. `self._ftp.sock.settimeout(self.request_timeout)`
8. `self._ftp.retrbinary(f"RETR {remote_path}", _callback, blocksize=65536)`
9. On `FtpCancelledError` propagating out: `dest_path.unlink(missing_ok=True)`, **`self._ftp = None`** (connection is dirty), re-raise.
10. On any other exception: `dest_path.unlink(missing_ok=True)`, re-raise.
11. On success: strip executable bits (`chmod & 0o666`), return `DownloadResult(saved_path=dest_path, size=bytes_written, elapsed_seconds=elapsed, mtime=None)`.

> **Critical:** Setting `self._ftp = None` after cancel is mandatory. The server has an incomplete transfer in flight. `_ensure_connected()` will reconnect transparently on the next operation.

Callers (`_download_thread_fn` in `FtpBrowserWindow`) must catch `FtpCancelledError`, `FtpFileTooLargeError`, `FileExistsError`, and `Exception` separately to give appropriate status messages.

**`read_file(remote_path, max_bytes=5_242_880) → ReadResult`**
Same pattern as `download_file` but writes to `io.BytesIO` instead of a file.
Raise `StopIteration` from callback when `bytes_read >= max_bytes`, catch it outside.
Return `ReadResult(data=buf.getvalue(), size=bytes_read, truncated=truncated)`.

**`get_file_size(remote_path) → Optional[int]`**
```python
def get_file_size(self, remote_path: str) -> Optional[int]:
    try:
        self._ftp.voidcmd("TYPE I")
        resp = self._ftp.sendcmd(f"SIZE {remote_path}")
        return int(resp.split()[-1])
    except (ftplib.error_perm, ValueError, AttributeError):
        return None
```

**`disconnect() → None`**
```python
if self._ftp:
    try: self._ftp.quit()
    except Exception:
        try: self._ftp.close()
        except Exception: pass
    finally: self._ftp = None
```

**`cancel() → None`** — `self._cancel_event.set()`

**`_normalize_path(path) → str`** — strip trailing `/` unless root, ensure leading `/`.

**`_enforce_limits(path) → None`**
```python
depth = len([p for p in path.split("/") if p])
if depth > self.max_depth:
    raise ValueError(f"Path depth {depth} exceeds max_depth {self.max_depth}")
if len(path) > self.max_path_length:
    raise ValueError(...)
```

**`__all__`** = `["FtpNavigator", "FtpCancelledError"]`

---

### Step 2 — `gui/utils/ftp_probe_cache.py` (NEW)

Depends on: nothing (stdlib only)

```python
import json
from pathlib import Path
from typing import Any, Dict, Optional

FTP_CACHE_DIR = Path.home() / ".smbseek" / "ftp_probes"


def _sanitize_ip(ip: str) -> str:
    return ip.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_ftp_cache_path(ip: str) -> Path:
    return FTP_CACHE_DIR / f"{_sanitize_ip(ip)}.json"


def load_ftp_probe_result(ip: str) -> Optional[Dict[str, Any]]:
    path = get_ftp_cache_path(ip)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_ftp_probe_result(ip: str, result: Dict[str, Any]) -> None:
    FTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = get_ftp_cache_path(ip)
    try:
        path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def clear_ftp_probe_result(ip: str) -> None:
    try:
        get_ftp_cache_path(ip).unlink(missing_ok=True)
    except Exception:
        pass
```

> `default=str` in `json.dumps` handles any `datetime` objects that survived serialization from MLSD parsing.

---

### Step 3 — `gui/utils/ftp_probe_runner.py` (NEW)

Depends on: Step 1 (`FtpNavigator`), Step 2 (`ftp_probe_cache`)

**Purpose:** Generate a probe snapshot in the exact format that `probe_patterns.py` expects from SMB probes.

**`run_ftp_probe(ip, port, max_entries, connect_timeout, request_timeout, cancel_event, progress_callback) → dict`**

```python
def run_ftp_probe(
    ip: str,
    port: int = 21,
    max_entries: int = 5000,
    connect_timeout: int = 10,
    request_timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
```

Implementation:
1. Instantiate `FtpNavigator(...)`. If `cancel_event` provided, assign it: `nav._cancel_event = cancel_event`.
2. `nav.connect(ip, port)` — wrapped in try/except; on failure, populate `errors` and go to step 8.
3. `root_result = nav.list_dir("/")` — collect `root_files` (non-dirs), `root_dirs` (dirs).
4. For each dir in `root_dirs[:max_entries]` (cap at max_entries):
   - Check `cancel_event.is_set()` first; break if set.
   - `sub_result = nav.list_dir(f"/{dir_entry.name}")` — collect `sub_files`.
   - Append `{"name": dir_entry.name, "files": sub_files, "files_truncated": sub_result.truncated}` to `directories`.
   - Call `progress_callback(f"Listing /{dir_entry.name}...")` if provided.
5. `nav.disconnect()`.
6. Assemble snapshot:
```python
snapshot = {
    "ip_address": ip,
    "port": port,
    "protocol": "ftp",
    "run_at": datetime.now(timezone.utc).isoformat(),
    "limits": {"max_entries": max_entries},
    "shares": [
        {
            "share": "ftp_root",
            "root_files": root_files,
            "root_files_truncated": root_result.truncated,
            "directories": directories,
            "directories_truncated": len(root_dirs) > max_entries,
        }
    ],
    "errors": errors,
}
```
7. `save_ftp_probe_result(ip, snapshot)`.
8. Return `snapshot`.

**Why one level deep only:** Matches SMB `probe_runner.py` behaviour (root + immediate subdirectories). The full tree is explored interactively in `FtpBrowserWindow`. The probe is a fast background snapshot, not a recursive crawl.

---

### Step 4 — `gui/components/ftp_browser_window.py` (NEW)

Depends on: Steps 1–3, `shared/quarantine.py`, `shared/smb_browser.py` (for type hints)

**Imports:**
```python
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from shared.ftp_browser import FtpNavigator
from shared.quarantine import build_quarantine_path, log_quarantine_event
from gui.utils.ftp_probe_cache import load_ftp_probe_result
from gui.utils.ftp_probe_runner import run_ftp_probe
```

**`_load_ftp_browser_config(config_path: Optional[str]) → dict`**

Defaults:
```python
defaults = {
    "max_entries": 5000,
    "max_depth": 12,
    "max_path_length": 1024,
    "max_file_bytes": 26_214_400,   # 25 MB
    "connect_timeout": 10,
    "request_timeout": 15,
    "quarantine_base": "~/.smbseek/quarantine",
}
```
Merge with `json.loads(Path(config_path).read_text())["ftp_browser"]` on best-effort basis. Catch all exceptions — return defaults on any failure.

**`FtpBrowserWindow.__init__` signature:**
```python
def __init__(
    self,
    parent: tk.Widget,
    ip_address: str,
    port: int = 21,
    config_path: Optional[str] = None,
    db_reader=None,
    theme=None,
    settings_manager=None,
) -> None:
```

**Key instance state:**
```python
self.ip_address = ip_address
self.port = port
self.config = _load_ftp_browser_config(config_path)
self._current_path: str = "/"
self._cancel_event = threading.Event()
self._navigator: Optional[FtpNavigator] = None
self._nav_thread: Optional[threading.Thread] = None
self._download_thread: Optional[threading.Thread] = None
self.busy: bool = False
```

**`_build_window() → None`**

Layout (mirrors `FileBrowserWindow` closely):
```
Toplevel title: "FTP Browser — {ip}:{port}"
geometry: "900x620", minsize: 720x480
WM_DELETE_WINDOW → _on_close

[top_frame]:
  Label "Path:", path_var StringVar("/"), path_label (display-only)

[button_frame]:
  btn_up    "⬆ Up"
  btn_refresh "🔄 Refresh"
  btn_view  "👁 View"
  btn_download "⬇ Download to Quarantine"
  btn_cancel "Cancel" (initially DISABLED)

[tree_frame]:
  Treeview columns: ("name", "type", "size", "modified", "mtime_raw", "size_raw")
  show="headings", selectmode="extended"
  column widths: name=280, type=80, size=110(e), modified=180
  mtime_raw and size_raw: width=0, stretch=False  (hidden, for sort)
  vertical Scrollbar on right
  bind <Double-1> → _on_item_double_click

[status_var]:
  StringVar, Label at bottom
```

After `_build_window()`:
1. Load cached probe if available: `self._apply_probe_snapshot(load_ftp_probe_result(ip_address))`
2. Navigate to root: `self._navigate_to("/")`  — this creates and connects `_navigator` lazily.
3. Start background probe: `threading.Thread(target=self._run_probe_background, daemon=True).start()`

**`_navigate_to(path: str) → None`**
- Guard: if `self.busy`, return.
- Set `self.busy = True`, disable nav buttons, set status "Loading...".
- `self._nav_thread = threading.Thread(target=self._list_thread_fn, args=(path,), daemon=True)`
- `.start()`

**`_list_thread_fn(path: str) → None`** (runs in thread)
```python
def _list_thread_fn(self, path: str) -> None:
    try:
        if self._navigator is None:
            self._navigator = FtpNavigator(
                connect_timeout=int(self.config["connect_timeout"]),
                request_timeout=int(self.config["request_timeout"]),
                max_entries=int(self.config["max_entries"]),
                max_depth=int(self.config["max_depth"]),
                max_path_length=int(self.config["max_path_length"]),
                max_file_bytes=int(self.config["max_file_bytes"]),
            )
            self._navigator._cancel_event = self._cancel_event
            self._navigator.connect(self.ip_address, self.port)
        result = self._navigator.list_dir(path)
        self.window.after(0, self._on_list_done, path, result)
    except Exception as exc:
        self.window.after(0, self._on_list_error, str(exc))
```

**`_on_list_done(path, list_result) → None`** (on main thread via `after`)
- Set `self._current_path = path`; update `path_var`.
- Call `_populate_treeview(list_result)`.
- If `list_result.warning`: set status to warning text.
- Else: set status to `"{len} items"`.
- `self.busy = False`; re-enable buttons.

**`_on_list_error(msg) → None`**
- `self.busy = False`; re-enable buttons.
- `_set_status(f"Error: {msg}")`

**`_populate_treeview(list_result) → None`**
- Clear tree: `self.tree.delete(*self.tree.get_children())`.
- For each entry:
  - `type_label = "dir"` if `entry.is_dir` else `"file"`.
  - `size_str = _format_file_size(entry.size)` if not dir else `""`.
  - `modified_str = datetime.utcfromtimestamp(entry.modified_time).strftime(...)` if `entry.modified_time` else `""`.
  - Insert: `(entry.name, type_label, size_str, modified_str, entry.modified_time or "", entry.size or 0)`.

**`_format_file_size(size_bytes: int) → str`** — copy from `file_browser_window.py` exactly (avoid cross-import).

**`_on_item_double_click(_event) → None`**
- Guard: if `self.busy`, return.
- Get selected item's type_label.
- If `"dir"`: `target = str(PurePosixPath(self._current_path) / name)`; `_navigate_to(target)`.
- If `"file"`: `_on_view()`.

**`_on_up() → None`**
- If `self._current_path == "/"`: return.
- `parent_path = str(PurePosixPath(self._current_path).parent)`.
- `_navigate_to(parent_path)`.

**`_refresh() → None`**
- If `self.busy`: return.
- `_navigate_to(self._current_path)`.

**`_on_view() → None`**
- Get selected item (files only).
- Read via `self._navigator.read_file(remote_path, max_bytes=int(self.config["max_file_bytes"]))`.
- Run in thread; on completion show a simple `Toplevel` text viewer or `messagebox.showinfo` for MVP.
- For binary content: show `messagebox.showinfo("Binary file", "Binary content, cannot display.")`.

**`_on_download() → None`**
- Guard: if `self.busy`, return.
- Get selection; build list of `(remote_path, size)` for files only; warn if dirs selected (not supported in MVP).
- Per-file size pre-flight — **hard block, no "download anyway" option**:
  ```python
  limit = int(self.config["max_file_bytes"])
  for rpath, size_raw in file_list:
      if size_raw and size_raw > limit:
          size_mb = size_raw / (1024 * 1024)
          messagebox.showerror(
              "File too large",
              f"{PurePosixPath(rpath).name} is {size_mb:.1f} MB, "
              f"exceeding the {limit // (1024*1024)} MB limit.\n"
              f"Adjust ftp_browser.max_file_bytes in config to change this limit.",
          )
          return
  ```
  Rationale: `FtpNavigator.download_file()` raises `FtpFileTooLargeError` on the same threshold. Allowing "download anyway" at the GUI layer while the navigator still enforces the limit would guarantee failure. Both layers block consistently; config is the override mechanism.
- `self._start_download_thread(file_list)`.

**`_start_download_thread(file_list) → None`**
```python
self._cancel_event.clear()
self.btn_cancel.config(state=tk.NORMAL)
self.btn_download.config(state=tk.DISABLED)
self.busy = True
self._download_thread = threading.Thread(
    target=self._download_thread_fn, args=(file_list,), daemon=True
)
self._download_thread.start()
```

**`_download_thread_fn(file_list) → None`** (runs in thread)
```python
quarantine_dir = build_quarantine_path(
    ip_address=self.ip_address,
    share_name="ftp_root",
    base_path=Path(self.config["quarantine_base"]).expanduser(),
    purpose="ftp",
)
success_count = 0
for remote_path, _ in file_list:
    if self._cancel_event.is_set():
        break
    self.window.after(0, self._set_status, f"Downloading {PurePosixPath(remote_path).name}...")
    try:
        result = self._navigator.download_file(
            remote_path=remote_path,
            dest_dir=quarantine_dir,
            progress_callback=lambda done, total: self.window.after(
                0, self._set_status, f"Downloading... {done // 1024} KB"
            ),
        )
        log_quarantine_event(quarantine_dir, f"Downloaded {remote_path} → {result.saved_path}")
        success_count += 1
    except FtpCancelledError:
        # cancel_event already set; outer loop will break on next iteration
        self.window.after(0, self._set_status, "Download cancelled.")
        break
    except FtpFileTooLargeError as exc:
        # Should not normally reach here after GUI pre-flight; log as safety-net
        self.window.after(0, self._set_status, f"Skipped (too large): {PurePosixPath(remote_path).name}")
    except FileExistsError:
        self.window.after(0, self._set_status, f"Skipped (already exists): {PurePosixPath(remote_path).name}")
    except Exception as exc:
        self.window.after(0, self._set_status, f"Error downloading {PurePosixPath(remote_path).name}: {exc}")
self.window.after(0, self._on_download_done, success_count, len(file_list), str(quarantine_dir))
```

**`_on_download_done(success, total, quarantine_path) → None`**
- `self.busy = False`; re-enable buttons; disable Cancel.
- Status: `f"Downloaded {success}/{total} files → {quarantine_path}"`.
- If `success > 0`: `messagebox.showinfo("Download complete", ...)`.

**`_on_cancel() → None`**
- `self._cancel_event.set()`
- `self._navigator.cancel()` (if exists)
- `btn_cancel.config(state=tk.DISABLED)`
- `_set_status("Cancelling...")`

**`_on_close() → None`**
- `self._cancel_event.set()`
- If `self._navigator`: `self._navigator.disconnect()`
- `self.window.destroy()`

**`_set_status(msg: str) → None`** — `self.status_var.set(msg)` (must be called on main thread; all callers use `after(0, ...)`).

**`_run_probe_background() → None`** (daemon thread, started in `__init__`)
```python
def _run_probe_background(self) -> None:
    try:
        snapshot = run_ftp_probe(
            ip=self.ip_address,
            port=self.port,
            max_entries=int(self.config["max_entries"]),
            connect_timeout=int(self.config["connect_timeout"]),
            request_timeout=int(self.config["request_timeout"]),
            cancel_event=self._cancel_event,
            progress_callback=lambda msg: self.window.after(0, self._set_status, msg),
        )
        self.window.after(0, self._apply_probe_snapshot, snapshot)
    except Exception:
        pass  # Probe failure is non-fatal; browser still works
```

**`_apply_probe_snapshot(snapshot: Optional[dict]) → None`**
- If `None`: return.
- Extract `errors` list; if non-empty and window is still open, append to status.
- (Card 5 MVP: snapshot is written and re-openable; indicator analysis is a bonus — if `probe_patterns` is already wired in the SMB path, call `find_indicator_hits` and `_set_status` if suspicious.)

---

### Step 5 — `gui/components/ftp_server_picker.py` (NEW)

Depends on: Step 4 (`FtpBrowserWindow`), `gui/utils/database_access.py` (existing)

**`FtpServerPickerDialog.__init__` signature:**
```python
def __init__(
    self,
    parent: tk.Widget,
    db_reader,              # DatabaseReader instance
    config_path: Optional[str] = None,
    theme=None,
    settings_manager=None,
) -> None:
```

**Layout:**
```
Toplevel title: "FTP Servers"
geometry: "700x450", minsize: 500x300

[filter_frame]:
  Label "Filter:", filter_var StringVar, Entry (textvariable=filter_var)
  trace → _on_filter_changed
  Button "Refresh" → _load_servers

[tree_frame]:
  Treeview columns: ("ip", "port", "country", "banner", "last_seen")
  show="headings"
  bind <Double-1> → _on_open_browser

[btn_frame]:
  Button "Browse Selected" → _on_open_browser
  Button "Close" → dialog.destroy
```

**`_load_servers() → None`**
```python
servers = self._db_reader.get_ftp_servers() or []
self._all_rows = servers   # cache for filter
self._populate_tree(servers)
```

**`_populate_tree(rows) → None`**
- Clear tree.
- For each row: `tree.insert("", "end", values=(row["ip_address"], row.get("port", 21), row.get("country", ""), row.get("banner", "")[:60], row.get("last_seen", "")))`

**`_on_filter_changed(*args) → None`**
```python
q = self.filter_var.get().lower()
filtered = [r for r in self._all_rows if q in str(r.get("ip_address", "")).lower()]
self._populate_tree(filtered)
```

**`_on_open_browser() → None`**
```python
sel = self.tree.selection()
if not sel:
    return
vals = self.tree.item(sel[0], "values")
ip, port = vals[0], int(vals[1])
from gui.components.ftp_browser_window import FtpBrowserWindow
FtpBrowserWindow(
    parent=self._dialog,
    ip_address=ip,
    port=port,
    config_path=self._config_path,
    db_reader=self._db_reader,
    theme=self._theme,
    settings_manager=self._settings_manager,
)
```

---

### Step 6 — `conf/config.json.example` (MODIFY)

Add `"ftp_browser"` section immediately after `"file_browser"`:

```json
"ftp_browser": {
    "max_entries": 5000,
    "max_depth": 12,
    "max_path_length": 1024,
    "max_file_bytes": 26214400,
    "connect_timeout": 10,
    "request_timeout": 15,
    "quarantine_base": "~/.smbseek/quarantine"
}
```

**No inline comments.** JSON does not support `//` comments. Config is loaded with `json.load()` / `json.loads()` in `shared/config.py:167` and `file_browser_window.py:84` — a comment would cause a parse failure and silent fallback to defaults.

---

### Step 7 — Two-file change: `gui/components/dashboard.py` + `xsmbseek` (MODIFY)

The dashboard has no FTP metric card with an implemented action path. The existing drill-down routing pattern must be followed:

```
dashboard button click
  → self._open_drill_down("ftp_server_list")
  → self.drill_down_callback("ftp_server_list", {})
  → xsmbseek._open_drill_down_window("ftp_server_list", {})
  → FtpServerPickerDialog(...)
```

#### 7a — `gui/components/dashboard.py`

Add one button to `actions_frame` in `_build_dashboard()`, immediately after the existing `servers_button` (line ~337):

```python
# FTP Servers browser button (Card 5)
ftp_servers_button = tk.Button(
    actions_frame,
    text="📡 FTP Servers",
    command=lambda: self._open_drill_down("ftp_server_list"),
)
self.theme.apply_to_widget(ftp_servers_button, "button_secondary")
ftp_servers_button.pack(side=tk.LEFT, padx=(0, 5))
```

No new method needed — `_open_drill_down` already exists and calls `self.drill_down_callback(window_type, {})`.

#### 7b — `xsmbseek`

Add an `elif` branch in `_open_drill_down_window` (around line 690, after the `data_import` branch):

```python
elif window_type == "ftp_server_list":
    from gui.components.ftp_server_picker import FtpServerPickerDialog
    FtpServerPickerDialog(
        parent=self.root,
        db_reader=self.db_reader,
        config_path=str(self.config.get_config_path()),
        theme=self.dashboard.theme,
        settings_manager=self.settings_manager,
    )
```

Wrap in `try/except ImportError` so a broken Step 4/5 import does not crash the whole window dispatch path. On `ImportError`, fall through to the existing `messagebox.showinfo` placeholder.

**Window reuse policy (explicit):** Allow multiple picker instances (no `drill_down_windows` tracking for `ftp_server_list`). Rationale: the picker is a lightweight list dialog, not a stateful editor; operators may want to compare server lists. Each `FtpBrowserWindow` opened from within a picker is its own independent Toplevel. If single-instance behaviour is needed in Card 6, add to `drill_down_windows` using the same `restore_and_focus()` pattern as `server_list` (`xsmbseek:662-672`).

---

## 5. Verification Plan

### Manual end-to-end test sequence

**Pre-condition:** A real anonymous FTP server is available, or use a local vsftpd/FileZilla test instance. At minimum, use `python3 -m pyftpdlib -p 2121` as a local test server.

```bash
# Step A: Confirm FTP probe runs and saves snapshot
python3 -c "
from gui.utils.ftp_probe_runner import run_ftp_probe
snap = run_ftp_probe('127.0.0.1', port=2121)
print('protocol:', snap['protocol'])
print('shares:', snap['shares'][0]['share'])
print('errors:', snap['errors'])
"

# Step B: Confirm cache round-trip
python3 -c "
from gui.utils.ftp_probe_cache import load_ftp_probe_result, save_ftp_probe_result
save_ftp_probe_result('1.2.3.4', {'test': True})
print(load_ftp_probe_result('1.2.3.4'))
"

# Step C: Confirm FtpNavigator list and download
python3 -c "
from shared.ftp_browser import FtpNavigator
nav = FtpNavigator()
nav.connect('127.0.0.1', 2121)
result = nav.list_dir('/')
for e in result.entries[:5]:
    print(e.name, e.is_dir, e.size)
nav.disconnect()
"

# Step D: Confirm indicator patterns work on FTP snapshot
python3 -c "
from gui.utils.ftp_probe_runner import run_ftp_probe
from gui.utils.probe_patterns import compile_indicator_patterns, find_indicator_hits
snap = run_ftp_probe('127.0.0.1', port=2121)
patterns = compile_indicator_patterns(['*.txt', 'DECRYPT*'])
hits = find_indicator_hits(snap, patterns)
print('suspicious:', hits['is_suspicious'])
"

# Step E: GUI smoke test — launch picker and open browser
xvfb-run -a python3 -c "
import tkinter as tk
from gui.utils.database_access import DatabaseReader
from gui.components.ftp_server_picker import FtpServerPickerDialog
root = tk.Tk()
dr = DatabaseReader('smbseek.db')
dlg = FtpServerPickerDialog(root, dr)
root.after(3000, root.destroy)
root.mainloop()
"
```

### Automated regression run

```bash
PYTHON=/home/kevin/venvs/smbseek/venv-desktop/bin/python
xvfb-run -a $PYTHON -m pytest gui/tests/ shared/tests/ -v --cov=gui/components --cov=shared
```

All pre-existing tests must pass without modification. Zero new failures = gate passes.

### Snapshot file verification

```bash
ls -lh ~/.smbseek/ftp_probes/
cat ~/.smbseek/ftp_probes/127.0.0.1.json | python3 -m json.tool | head -30
```

Expected: valid JSON, `"protocol": "ftp"`, `"shares"[0]["share"] == "ftp_root"`.

### Quarantine path verification

After a download:
```bash
ls -lh ~/.smbseek/quarantine/<ip>/$(date +%Y%m%d)/ftp_root/
cat ~/.smbseek/quarantine/<ip>/$(date +%Y%m%d)/activity.log
```

Expected: downloaded file present, activity.log has timestamped entry.

### Cancel/timeout test

1. Start download of a large file.
2. Click Cancel within 2 seconds.
3. Expected: partial file removed, status updates to "Cancelling...", buttons re-enabled within 5s.
4. Expected: subsequent `list_dir("/")` works (reconnect transparent).

---

## 6. Regression Checklist

### SMB baseline (must pass unchanged)

- [ ] `./xsmbseek --mock` launches without error
- [ ] SMB scan start → progress visible in dashboard log
- [ ] SMB scan stop → "Stopped" state reached
- [ ] Server list window opens for an SMB host
- [ ] `FileBrowserWindow` opens, navigates a directory, and downloads a file to quarantine
- [ ] SMB probe snapshot saves to `~/.smbseek/probes/`
- [ ] Indicator pattern analysis runs on SMB snapshot without error
- [ ] DB tools dialog opens without error
- [ ] External scan lock detection disables scan buttons correctly

### FTP scan pipeline baseline (Cards 1–4, must not regress)

- [ ] FTP scan button visible and separate from SMB scan button
- [ ] FTP scan start → progress lines stream to dashboard
- [ ] FTP scan completes → `ftp_servers` table populated
- [ ] FTP server count metric card shows correct count

### Card 5 specific gates

- [ ] `FtpServerPickerDialog` opens when clicking `"📡 FTP Servers"` button in dashboard header
- [ ] Picker shows rows from `get_ftp_servers()`
- [ ] Double-click on picker row opens `FtpBrowserWindow`
- [ ] `FtpBrowserWindow` connects and lists root `/`
- [ ] Navigation: double-click directory → enters subdirectory
- [ ] Navigation: Up button → returns to parent
- [ ] Download: single file downloads to `~/.smbseek/quarantine/<ip>/YYYYMMDD/ftp_root/`
- [ ] Download: file is NOT executable (`chmod & 0o666` applied)
- [ ] Download: `activity.log` entry written
- [ ] Probe snapshot: `~/.smbseek/ftp_probes/<ip>.json` exists after window open
- [ ] Probe snapshot: valid JSON with `"protocol": "ftp"` and `"shares"[0]["share"] == "ftp_root"`
- [ ] Cancel: download cancels within 5 seconds, partial file removed
- [ ] Cancel: subsequent navigation in the same window works (reconnect)
- [ ] Close: window closes cleanly (no thread leaks, no FTP quit error shown to user)
- [ ] Config: `ftp_browser` section in `config.json.example` is valid JSON

### UI responsiveness gates

- [ ] Directory listing does not freeze the GUI (runs in thread)
- [ ] Download does not freeze the GUI (runs in thread)
- [ ] Status bar updates during download show progress
- [ ] Cancel button enables during download, disables at idle

---

## 7. Risks, Edge Cases, and Mitigations

### R2: MLSD not supported by target servers

**Risk:** `ftplib.error_perm` on every `mlsd()` call → falls back to LIST; LIST parsing may fail on edge-case output.
**Mitigation:** Two-regex LIST parser (Unix + DOS/Windows formats). Log `warning` in `ListResult` when fallback is used. Test with vsftpd (which supports MLSD) and pyftpdlib (which also supports MLSD). Manually test against a server that doesn't support MLSD to verify fallback.
**Validation:** Force MLSD failure in tests by subclassing and overriding; assert LIST fallback returns same `Entry` structure.

### R3: FTP connection dropped during idle browsing

**Risk:** Server timeout closes the connection; next `list_dir` hangs or raises.
**Mitigation:** `_ensure_connected()` sends a NOOP keepalive; if it fails, reconnects transparently using stored `self._host`/`self._port`. Status bar shows "Reconnecting..." during reconnect.
**Validation:** Manually set server idle timeout to 5s in vsftpd; navigate → wait → navigate again; verify reconnect works.

### R4: Download cancel leaves dirty FTP connection

**Risk:** After `FtpCancelledError` in RETR callback, subsequent operations on the same `self._ftp` object fail silently or raise unexpected errors.
**Mitigation:** On cancel: `self._ftp = None`. `_ensure_connected()` creates a fresh connection. Partial file is explicitly `unlink(missing_ok=True)`.
**Validation:** Cancel a download mid-flight; immediately navigate to `/`; verify new listing succeeds.

### R5: `probe_patterns.py` assumes `shares[*].share` is an SMB share name

**Risk:** If any code in the indicator analysis pipeline special-cases share names or paths, `"ftp_root"` may produce unexpected behaviour.
**Mitigation:** `probe_patterns.py`'s `_iter_snapshot_paths()` uses `share.get("share")` purely as a label in the path string (e.g., `//ip/ftp_root/filename.txt`). No logic branches on the share name value. Verified by reading the source.
**Validation:** Run `find_indicator_hits` on an FTP snapshot; confirm `matches[*]["path"]` has format `//ip/ftp_root/...`.

### R6: `build_quarantine_path` signature changed between cards

**Risk:** Card 3/4 may have modified `shared/quarantine.py`; the `purpose` parameter behavior may differ.
**Mitigation:** Read `shared/quarantine.py` at implementation time (it was readable in this session). Current signature: `build_quarantine_path(ip_address, share_name, *, base_path, purpose) → Path`. FTP call: `build_quarantine_path(ip, "ftp_root", base_path=..., purpose="ftp")`.
**Validation:** `python3 -c "from shared.quarantine import build_quarantine_path; print(build_quarantine_path('1.2.3.4', 'ftp_root', purpose='ftp'))"`.

### R7: `get_ftp_servers()` return type shape unknown at plan time

**Risk:** `FtpServerPickerDialog` accesses dict fields by name (e.g., `row["ip_address"]`); if the DB reader returns different field names, `KeyError` at runtime.
**Mitigation:** At implementation time, read `gui/utils/database_access.py` lines ~1337–1365 (already confirmed to return rows from `SELECT * FROM ftp_servers`). Use `.get("ip_address", "")` defensive access for all fields in the picker.
**Validation:** Print `get_ftp_servers()[0].keys()` in a test script before implementing picker column population.

### R8: Thread lifetime vs. window destruction

**Risk:** A long-running `_list_thread_fn` calls `self.window.after(0, ...)` after `self.window.destroy()`, causing `TclError: invalid command name`.
**Mitigation:** In `_on_close()`: set `self._cancel_event` (stops nav thread), `self._navigator.disconnect()`. In thread functions: wrap `after()` calls with `try/except tk.TclError`. This is the same pattern `FileBrowserWindow` uses.
**Validation:** Open window, navigate, immediately close while listing is in flight; verify no TclError in console output.

### R9: Two simultaneous FTP connections to same server

**Risk:** Some FTP servers limit simultaneous connections per IP. The probe runner opens a second connection while the interactive navigator is open.
**Mitigation:** Probe runs first (at window open), disconnects, then interactive navigator connects lazily. They should not overlap in practice. If they do, the second connection may fail; catch the exception in `_run_probe_background` and treat as non-fatal.
**Validation:** Run against a server with `max_per_ip=1` in vsftpd config; verify browser still works even if probe fails.

### R10: `_ensure_connected()` may trigger reconnect during a cancel

**Risk:** If `cancel()` is called, then the next `list_dir` calls `_ensure_connected()` which calls `connect()`, which clears `self._cancel_event`. Then the cancel becomes invisible.
**Mitigation:** In `cancel()`, do NOT clear the cancel event. In `connect()`, call `self._cancel_event.clear()` only if `cancel()` was not meant to persist (i.e., it's a startup connect). The proposed implementation does `self._cancel_event.clear()` in `connect()`. This is correct: reconnect after cancel is intentional navigation, so the cancel is cleared by the fresh connect. Document this explicitly.
**Validation:** Cancel download → click Refresh → verify new listing works.

---

## 8. Out-of-Scope Confirmation

The following are **explicitly deferred to Card 6 or later**, and **must not be implemented** in Card 5:

| Item | Rationale |
|---|---|
| Full normalized FTP artifact DB table (e.g., `ftp_files` or `ftp_shares`) | Card 5 DoD says "probe snapshot is written and re-openable" — JSON cache is sufficient |
| FTP content ranking / value scoring | Card 6+ scope |
| Recursive directory crawl in probe runner | MVP: one level deep only; full tree is interactive |
| Folder download (batch recursive) | Files only in MVP; folder download is complex (Card 6) |
| FTP server list window (full-featured like `server_list_window/`) | Picker dialog is sufficient for MVP launch path |
| Authentication beyond anonymous | Card 5 scope: anonymous FTP only (mirrors Card 4's verification scope) |
| Preview/view for binary files | Basic text preview is acceptable; image viewer wiring is deferred |
| SMB `server_list_window/` modifications | Keep SMB code untouched |

---

## 9. Ready-to-Implement Step List

Execute in this exact order. Mark each step complete before starting the next.

- [ ] **Step 0 — Pre-flight reads:** Read `gui/utils/database_access.py` lines 1337–1410 (confirm `get_ftp_servers()` return shape and field names). Read `conf/config.json.example` current structure (placement for `ftp_browser` section).
- [ ] **Step 1 — `shared/ftp_browser.py`:** Create `FtpNavigator` with `connect`, `list_dir` (MLSD + LIST fallback), `download_file`, `read_file`, `get_file_size`, `disconnect`, `cancel`, `_ensure_connected`, `_normalize_path`, `_enforce_limits`. Test standalone with a local FTP server.
- [ ] **Step 2 — `gui/utils/ftp_probe_cache.py`:** Create cache module. Test round-trip with `save_ftp_probe_result` + `load_ftp_probe_result`.
- [ ] **Step 3 — `gui/utils/ftp_probe_runner.py`:** Create `run_ftp_probe`. Verify snapshot JSON has `"protocol": "ftp"` and `"shares"[0]["share"] == "ftp_root"`. Run `find_indicator_hits` on snapshot.
- [ ] **Step 4 — `gui/components/ftp_browser_window.py`:** Create `FtpBrowserWindow`. Test headless: `xvfb-run -a python3 -c "..."`. Verify navigation, download, cancel, close.
- [ ] **Step 5 — `gui/components/ftp_server_picker.py`:** Create `FtpServerPickerDialog`. Test headless. Verify picker opens browser on double-click.
- [ ] **Step 6 — `conf/config.json.example`:** Add `ftp_browser` section. Validate JSON: `python3 -m json.tool conf/config.json.example`.
- [ ] **Step 7 — `gui/components/dashboard.py` + `xsmbseek`:** Add `"📡 FTP Servers"` button to header `actions_frame` calling `_open_drill_down("ftp_server_list")`. Add `elif window_type == "ftp_server_list"` branch in `xsmbseek._open_drill_down_window` that instantiates `FtpServerPickerDialog`. Test: `./xsmbseek --mock` → click "FTP Servers" button → picker opens.
- [ ] **Step 8 — Regression run:** `xvfb-run -a /home/kevin/venvs/smbseek/venv-desktop/bin/python -m pytest gui/tests/ shared/tests/ -v`. Zero new failures required.
- [ ] **Step 9 — Manual Card 5 DoD verification:** Walk through all gates in Section 6 Card 5 specific gates checklist.

---

## 10. Critical Files Table

| File | Status | Card 5 Role |
|---|---|---|
| `shared/smb_browser.py` | Existing | Import `Entry`, `ListResult`, `DownloadResult`, `ReadResult`; structural template for `FtpNavigator` |
| `gui/components/file_browser_window.py` | Existing | Structural template for `FtpBrowserWindow` — layout, threading, cancel, quarantine call sites |
| `shared/quarantine.py` | Existing | Reused as-is: `build_quarantine_path`, `log_quarantine_event` |
| `gui/utils/probe_runner.py` | Existing | Template for snapshot format; `run_ftp_probe` mirrors one-level-deep walk |
| `gui/utils/probe_cache.py` | Existing | API template for `ftp_probe_cache.py` |
| `gui/utils/probe_patterns.py` | Existing | Reused unchanged; FTP snapshot `"ftp_root"` share name is compatible |
| `gui/utils/database_access.py` | Existing | `get_ftp_servers()` feeds the picker; confirm field names at impl time |
| `commands/ftp/verifier.py` | Existing | Confirms anonymous `ftplib.FTP.login()` pattern |
| `gui/components/dashboard.py` | **Modify** | Add `"📡 FTP Servers"` button to header `actions_frame`; calls `_open_drill_down("ftp_server_list")` |
| `xsmbseek` | **Modify** | Add `elif window_type == "ftp_server_list"` branch in `_open_drill_down_window`; instantiates `FtpServerPickerDialog` |
| `conf/config.json.example` | **Modify** | Add `ftp_browser` section |
| `shared/ftp_browser.py` | **Create** | `FtpNavigator` — core FTP browse/download engine |
| `gui/utils/ftp_probe_cache.py` | **Create** | `~/.smbseek/ftp_probes/` cache helpers |
| `gui/utils/ftp_probe_runner.py` | **Create** | `run_ftp_probe()` — snapshot generator |
| `gui/components/ftp_browser_window.py` | **Create** | `FtpBrowserWindow` — Tkinter FTP browser |
| `gui/components/ftp_server_picker.py` | **Create** | `FtpServerPickerDialog` — server list MVP |

---

## Assumptions and Validation

| Assumption | How to Validate |
|---|---|
| `ReadResult` is exported from `shared/smb_browser.py` | Confirmed: class definition near top of file, `__all__` at bottom. No pre-check needed. |
| `build_quarantine_path` accepts `purpose="ftp"` | Confirmed from source read: `purpose` is accepted keyword-only arg with no validation. |
| `get_ftp_servers()` returns dicts with `ip_address` and `port` keys | Read `gui/utils/database_access.py` lines 1337–1365 at impl time; use `.get()` defensive access in picker. |
| `probe_patterns.find_indicator_hits` works on `"ftp_root"` snapshots | Confirmed: `_iter_snapshot_paths` uses share name purely as a path label string. |
| `ftplib.FTP.login()` with no args sends anonymous credentials | Confirmed by `commands/ftp/verifier.py:try_anon_login`. |
| `db_reader` available in `xsmbseek._open_drill_down_window` | Confirmed: `self.db_reader` is a `DashboardWidget` dep injected and accessible in `xsmbseek`. |

---

## Open Questions

1. **Does `dashboard.py` expose `self.db_reader` or does the FTP picker need to construct its own `DatabaseReader`?** Low risk — `DashboardWidget.__init__` takes `db_reader` as a parameter and stores it. Confirmed in the first 150 lines read during planning. No issue.

2. **Does `FileBrowserWindow` use `theme.apply_to_widget()` on the Toplevel?** Yes (line 172-173 of `file_browser_window.py`). `FtpBrowserWindow` should do the same if `theme` is provided; guard with `if self.theme: self.theme.apply_to_widget(self.window, "main_window")`.

3. **Dashboard launch point — resolved.** The dashboard has no implemented FTP metric-card action path. A new `"📡 FTP Servers"` button is added to the header `actions_frame` (same row as Servers, DB Tools, Config). It calls `_open_drill_down("ftp_server_list")` which routes through the existing `drill_down_callback` → `xsmbseek._open_drill_down_window`. This is the established window-lifecycle pattern; window management (focus/restore/tracking) can be added in Card 6 if needed.

---

## Pass/Fail Criteria (Tied to Card 5 DoD)

| DoD Item | Pass Criterion | Fail Criterion |
|---|---|---|
| Operator can browse FTP directory tree | `FtpBrowserWindow` opens, lists root, navigates subdirectories, Back works | Window crashes on open, navigation hangs, or UI freezes |
| Operator can download files to quarantine | Selected file appears at `~/.smbseek/quarantine/<ip>/YYYYMMDD/ftp_root/<filename>`, activity.log written | Download silently fails, file goes to wrong path, or is executable |
| FTP probe snapshot is written and re-openable | `~/.smbseek/ftp_probes/<ip>.json` exists, is valid JSON, has `"protocol": "ftp"` | File absent, invalid JSON, or wrong structure |
| SMB browse/download unchanged | All pre-existing `gui/tests/` and `shared/tests/` pass | Any pre-existing test fails |
| Cancel/timeout responsive | Cancel stops download within 5s, GUI responds, subsequent navigation works | Cancel hangs GUI, partial file left on disk, subsequent nav fails |
