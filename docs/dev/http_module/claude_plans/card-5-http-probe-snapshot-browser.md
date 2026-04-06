# Card 5: HTTP Probe Snapshot + Browser Download MVP

## Context

Cards 1–4 delivered HTTP scan discovery, persistence, and count extraction. Card 5 closes
the final UX gap: operators can discover HTTP index-of servers but have no in-app way to
browse them, probe for snapshot data, or download files. This card adds the HTTP
browse/probe/download flow with full FTP/SMB UX parity, reusing all established
probe-cache, quarantine, and browser-window patterns.

---

## Pre-revision reality-check commands

Run before starting to confirm Card 4 baseline is clean:

```bash
# Verify verifier functions reusable
python -c "from commands.http.verifier import count_dir_entries; print(count_dir_entries('<a href=\"dir/\">d</a><a href=\"f.txt\">f</a>'))"

# Verify HTTP DB tables present
sqlite3 smbseek.db ".tables" | tr ' ' '\n' | grep http

# Confirm probe cache dirs exist
ls ~/.smbseek/

# Full test suite baseline (all must pass before Card 5 starts)
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short 2>&1 | tail -20

# Check branch state (Card 4 hotfix should be clean)
git diff --name-only HEAD
git log --oneline -5
```

---

## File Touch List

### New files (4)

| File | Purpose |
|------|---------|
| `shared/http_browser.py` | `HttpNavigator` + `_parse_dir_entries()` helper: stateless per-request HTTP navigator (list, download, read) |
| `gui/utils/http_probe_cache.py` | Load/save/clear JSON cache at `~/.smbseek/http_probes/<ip>.json` |
| `gui/utils/http_probe_runner.py` | `run_http_probe()`: walk root + 1 level, persist snapshot |
| `gui/components/http_browser_window.py` | `HttpBrowserWindow`: Tkinter browser UI (mirrors `FtpBrowserWindow`) |

### Modified files (6)

| File | Change |
|------|--------|
| `gui/utils/database_access.py` | Add `get_http_server_detail(ip_address)` to `DatabaseReader` returning `{scheme, port}` |
| `gui/components/dashboard.py` | Add `host_type == "H"` branch in `_probe_single_server()` (~L1362) before SMB path |
| `gui/components/server_list_window/actions/batch_operations.py` | Add `host_type == "H"` branch in `_launch_browse_workflow()`; add `"H"` to delete validation |
| `gui/components/server_list_window/details.py` | Add `host_type == "H"` in probe cache load, probe worker, and `_open_browse_window()` |
| `gui/components/server_list_window/actions/batch.py` | Add `"H"` to FTP extract skip; add `host_type == "H"` probe branch in `_execute_probe_target()` |
| `gui/components/server_list_window/actions/batch_status.py` | Extend `host_type == "F"` probe-status guard (~L612) to include `"H"` |

---

## Data / Snapshot Schema — HTTP Probe Cache JSON

Cache path: `~/.smbseek/http_probes/<sanitized_ip>.json`

Schema mirrors `ftp_probe_runner` output so `probe_patterns.py` and the unified probe
display work without changes.

```json
{
  "ip_address": "1.2.3.4",
  "port": 80,
  "scheme": "http",
  "protocol": "http",
  "run_at": "2026-03-19T12:00:00.000000Z",
  "limits": {
    "max_entries": 5000,
    "max_directories": 50,
    "max_files": 200,
    "timeout_seconds": 10
  },
  "shares": [
    {
      "share": "http_root",
      "root_files": ["readme.html", "robots.txt"],
      "root_files_truncated": false,
      "directories": [
        {
          "name": "uploads/",
          "subdirectories": ["2024/"],
          "subdirectories_truncated": false,
          "files": ["report.pdf", "data.csv"],
          "files_truncated": false
        }
      ],
      "directories_truncated": false
    }
  ],
  "errors": []
}
```

Key decisions:
- `share` always `"http_root"` (synthetic, mirrors FTP `"ftp_root"`)
- `root_files` = filenames at `/` (not dirs) — basename only, no leading slash
- `directories[].name` = **display name without trailing slash** (e.g. `"uploads"` not `"uploads/"`)
  The renderer in `details.py:374` already appends `/` when displaying: `f"📁 {dir_name}/"`.
  Storing without trailing slash prevents `"uploads//"` double-slash in the probe detail popup.
- `directories[].files` = filenames inside that subdir (basename only, one level only)
- `directories[].subdirectories` = display names (no trailing slash) of nested dirs at second level
- `errors` = list of **dicts** `{"share": "http_root", "message": "..."}` matching the shape
  expected by the renderer at `details.py:416-419` which calls `err.get("share")` and `err.get("message")`.
  Plain strings are NOT acceptable here — the renderer would throw `AttributeError: 'str' object has no attribute 'get'`.

---

## shared/http_browser.py — HttpNavigator + Parser Helper

### `_parse_dir_entries(body, current_path="/")` — module-level helper

`count_dir_entries()` in `verifier.py` returns counts and dir paths but no filenames, and
it drops root-absolute hrefs (correct for scan-time counting; `/icons/` inflates counts).
The browser/probe runner needs full entry names and must handle root-absolute same-host links
without misrouting them onto the current subdirectory.

Add a `_parse_dir_entries(body, current_path)` helper inside `shared/http_browser.py`:

```python
def _parse_dir_entries(body: str, current_path: str = "/") -> Tuple[List[str], List[str]]:
    """
    Parse an Apache/nginx directory listing and return
    (dir_abs_paths, file_abs_paths) — all paths are absolute from root.

    Normalization rules (applied inside list_dir before creating Entry objects):
    - Relative hrefs (e.g. "pub/", "file.txt"):
        joined with current_path using PurePosixPath arithmetic.
        "pub/" at current_path "/data/" → "/data/pub/"
    - Root-absolute hrefs (e.g. "/pub/"):
        used as-is, NOT stripped or joined with current_path.
        "/pub/" at current_path "/data/" stays "/pub/"  (NOT "/data/pub/")
    - Skipped: "../", "..", "?..." sort links, "//" protocol-relative, "://" external.

    Returning absolute paths means HttpNavigator.list_dir() and probe runner
    can always call list_dir(path) directly without secondary path arithmetic.
    HttpBrowserWindow.Up navigation uses PurePosixPath(current_path).parent.
    """
```

This keeps all parsing co-located with `HttpNavigator`. `http_probe_runner.py` imports
`_parse_dir_entries` from `shared.http_browser` — no separate parser module needed, and
`commands.http.verifier` remains unchanged.

### `HttpNavigator`

Reuses `Entry`, `ListResult`, `DownloadResult`, `ReadResult` from `shared.smb_browser`.
Reuses `try_http_request`, `validate_index_page` from `commands.http.verifier`.
Uses `_parse_dir_entries()` (above) for listing.

```python
class HttpNavigator:
    """Stateless per-request HTTP navigator. Each public method is self-contained."""

    def __init__(
        self, *,
        ip: str,
        port: int,
        scheme: str,
        allow_insecure_tls: bool = True,
        connect_timeout: float = 10.0,
        request_timeout: float = 15.0,
        max_entries: int = 5000,
        max_file_bytes: int = 26_214_400,   # 25 MB
    ) -> None: ...

    def list_dir(self, path: str = "/") -> ListResult:
        """
        Fetch path, validate as index page, parse hrefs into Entry objects.
        Uses _parse_dir_entries(body, current_path=path) — NOT count_dir_entries() —
        to get absolute paths and filenames.

        Entry field convention (critical for correct routing):
          Entry.name   = display label only: PurePosixPath(abs_path).name
                         (e.g. "pub" for dirs, "file.txt" for files)
          Entry is accompanied by a parallel list of abs_paths in ListResult.extra
          OR: HttpBrowserWindow keeps a separate {iid → abs_path} dict keyed by
          Treeview iid, populated from list_dir result, used for all navigate/view/download.
          Never reconstruct the abs_path by joining current_path + Entry.name — same-name
          files in different dirs would silently download the wrong target.

        Returns ListResult(entries=[], warning="...") on non-index page (not an error).
        """

    def download_file(
        self, remote_path: str, dest_dir: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> DownloadResult:
        """
        Stream-download file to dest_dir / basename.
        Enforces max_file_bytes; removes partial on failure/cancel.
        Strips executable bits (chmod & 0o666).
        Sets mtime from Last-Modified header if present.
        Respects self._cancel_event.
        """

    def read_file(self, remote_path: str, max_bytes: int = 5 * 1024 * 1024) -> ReadResult:
        """Fetch and return up to max_bytes as bytes."""

    def cancel(self) -> None:
        """Set internal threading.Event to abort in-flight op."""
```

**Key difference from FtpNavigator**: stateless — no persistent connection. No
`connect()`/`disconnect()` lifecycle needed.

---

## gui/utils/http_probe_runner.py — run_http_probe()

```python
def run_http_probe(
    ip: str,
    port: int = 80,
    scheme: str = "http",
    allow_insecure_tls: bool = True,
    max_entries: int = 5000,
    max_directories: Optional[int] = None,
    max_files: Optional[int] = None,
    connect_timeout: int = 10,
    request_timeout: int = 15,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict
```

Behavior:
1. Fetch root `/` with `try_http_request()`
2. If not `validate_index_page()`: return snapshot with empty shares + error entry
3. Call `_parse_dir_entries(body, current_path="/")` → `dir_abs_paths`, `file_abs_paths`
4. `root_files` = last-segment names from `file_abs_paths[:max_files]`; set `root_files_truncated`
5. For each `dir_abs_path` in `dir_abs_paths[:max_directories]`:
   - Check `cancel_event.is_set()` → break if set
   - Call `try_http_request(ip, port, scheme, ..., path=dir_abs_path)`
   - If `validate_index_page()`: call `_parse_dir_entries(body, current_path=dir_abs_path)`
     to get sub-entries; record `files` (last-segment names) and `subdirectories` (abs paths)
   - On any failure: append `{"share": "http_root", "message": "<reason>"}` to `errors` list,
     continue (never append raw strings — renderer calls `.get("share")`)
6. Compute `accessible_files_count = len(root_files) + sum(len(d["files"]) for d in directories)`
   (root + all first-level subdir files — matches one-level recursion scope)
7. Build snapshot dict (schema above):
   - `directories[].name` = `PurePosixPath(dir_abs_path).name` — no trailing slash
     (renderer appends `/` at `details.py:374`; storing with slash causes double-slash)
   - `directories[].subdirectories` = `[PurePosixPath(p).name for p in sub_abs_paths]` — no trailing slash
8. Call `save_http_probe_result(ip, snapshot)` before returning
9. Return snapshot dict

Note: `_parse_dir_entries()` is imported from `shared.http_browser` — probe runner does not
contain any HTML parsing logic of its own.

---

## gui/utils/http_probe_cache.py — Cache Module

Exact mirror of `ftp_probe_cache.py` with `ftp` → `http`:

```python
HTTP_CACHE_DIR = Path.home() / ".smbseek" / "http_probes"

def get_http_cache_path(ip: str) -> Path
def load_http_probe_result(ip: str) -> Optional[Dict[str, Any]]
def save_http_probe_result(ip: str, result: Dict[str, Any]) -> None
def clear_http_probe_result(ip: str) -> None
```

---

## gui/utils/database_access.py — get_http_server_detail()

`scheme` is absent from the UNION ALL query (adding it would require changing all three
arms). Use a point lookup instead.

Add to `DatabaseReader`:

```python
def get_http_server_detail(self, ip_address: str) -> Optional[Dict[str, Any]]:
    """
    Return {scheme, port} for the most-recently-seen http_servers row for ip_address.
    Returns None if no row found or HTTP tables absent.
    """
    try:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT scheme, port FROM http_servers WHERE ip_address = ? "
                "ORDER BY last_seen DESC LIMIT 1",
                (ip_address,)
            ).fetchone()
            if row:
                return {"scheme": row[0] or "http", "port": row[1] or 80}
            return None
    except Exception:
        return None
```

Call sites:
- `batch_operations._launch_browse_workflow()` for `host_type == "H"`
- `details.py` `_open_browse_window()` for `host_type == "H"`
- `details.py` probe worker for `host_type == "H"`
- `dashboard._probe_single_server()` for `host_type == "H"`
- `batch._execute_probe_target()` for `host_type == "H"`

If `db_reader` is unavailable, fall back to `scheme="http"`, `port=80`.

---

## gui/components/http_browser_window.py — HttpBrowserWindow

Constructor mirrors `FtpBrowserWindow`:

```python
HttpBrowserWindow(
    parent: tk.Widget,
    ip_address: str,
    port: int = 80,
    scheme: str = "http",
    banner: Optional[str] = None,
    config_path: Optional[str] = None,
    db_reader=None,
    theme=None,
    settings_manager=None,
)
```

On `__init__`:
1. Load probe cache: `http_probe_cache.load_http_probe_result(ip_address)` → populate tree
2. Spawn background daemon thread calling `run_http_probe()` to freshen cache

UI layout (mirrors FtpBrowserWindow exactly):
- Banner display (read-only, 4-line max), URL label showing current path
- Treeview: Name (display label = basename), Type, Size (`"—"`), Modified (`"—"`),
  plus a hidden `path_raw` column holding the full absolute path (e.g. `/pub/file.txt`)
- Buttons: Up, Refresh, View, Download to Quarantine, Cancel
- Status bar

Path tracking — CRITICAL:
- Maintain `self._path_map: Dict[str, str]` mapping `treeview_iid → abs_path`
- Populated by `_populate_tree()` after each `list_dir()` call
- ALL navigate/view/download actions read abs_path from `self._path_map[selected_iid]`
- Never reconstruct path by joining current_path + display_name (ambiguous for same-name files)

Navigation:
- Double-click dir → `self._path_map[iid]` → `HttpNavigator.list_dir(abs_path)` in background thread
- Double-click file → `self._path_map[iid]` → `HttpNavigator.read_file(abs_path)` → text viewer
- Up → `str(PurePosixPath(self._current_path).parent)` or `"/"`

Download:
- Selected file → `self._path_map[iid]` → `build_quarantine_path(ip_address, "http_root", ...)`
  → `HttpNavigator.download_file(abs_path, dest_dir)` → `log_quarantine_event(quarantine_dir, ...)`

Cancel: Sets `threading.Event` passed to active `HttpNavigator`.

Config defaults (same as FtpBrowserWindow):
```python
{
  "max_entries": 5000,
  "max_file_bytes": 26_214_400,
  "connect_timeout": 10,
  "request_timeout": 15,
  "quarantine_base": "~/.smbseek/quarantine",
  "viewer": {"max_view_size_mb": 5}
}
```

**Omit image viewer** (HTTP listings are web content; FTP/SMB image viewer stays unchanged).

---

## UI Integration Map — host_type "H" routing

### 1. database_access.py (new method)

`get_http_server_detail(ip_address)` — see above.

### 2. batch_operations.py `_launch_browse_workflow()` (~L426)

Add `elif host_type == "H":` before the SMB path:

```python
elif host_type == "H":
    detail = self.db_reader.get_http_server_detail(ip_addr) if self.db_reader else None
    port = int((detail or {}).get("port") or 80)
    scheme = (detail or {}).get("scheme") or "http"
    banner = target.get("data", {}).get("banner")
    from gui.components.http_browser_window import HttpBrowserWindow
    HttpBrowserWindow(
        parent=self.window,
        ip_address=ip_addr,
        port=port,
        scheme=scheme,
        banner=banner,
        config_path=config_path,
        db_reader=self.db_reader,
        theme=self.theme,
        settings_manager=self.settings_manager,
    )
    return
```

Also fix delete validation (~L181):
```python
# Before: in ("S", "F")
# After:  in ("S", "F", "H")
```

### 3. details.py — four touch points

**a) `_format_server_details()` (~L226-L229) — HTTP display branch:**

`protocol_label` currently defaults to `"FTP"` for all non-SMB rows. Add HTTP:
```python
# Before: protocol_label = "SMB" if host_type == "S" else "FTP"
# After:
protocol_label = {"S": "SMB", "F": "FTP", "H": "HTTP"}.get(host_type, "Unknown")
```

The `else:` branch at ~L283 renders `"FTP Access"` for all non-SMB rows. Add an
`elif host_type == "H":` branch before it:
```python
elif host_type == "H":
    port = server.get('port') or "80"
    banner = server.get('banner') or "N/A"
    dirs = server.get('accessible_dirs_count') or server.get('accessible_shares', 0)
    files = server.get('accessible_files_count') or 0
    access_section = f"""🌐 HTTP Access:
   Port: {port}
   Title/Banner: {banner}
   Directories: {dirs}
   Files: {files}"""
```

(Note: `accessible_dirs_count` and `accessible_files_count` may not be in `server_data`
directly; fall back to `accessible_shares` which holds `dirs + files` from the DB query.)

**b) Probe cache load (~L77):**
```python
if ip_address and host_type == "F":
    cached_probe = ftp_probe_cache.load_ftp_probe_result(ip_address)
elif ip_address and host_type == "H":
    cached_probe = http_probe_cache.load_http_probe_result(ip_address)
else:
    cached_probe = probe_cache.load_probe_result(ip_address) if ip_address else None
```

**b) `_open_browse_window()` (~L144) — bypass SMB share precheck for HTTP:**

`_open_browse_window()` is a nested closure; `db_reader` is not in its enclosing scope.
Create one locally via `settings_manager`, mirroring the pattern in `_persist_notes()` (~L128):

```python
def _open_browse_window() -> None:
    if host_type == "H":
        _db = None
        if settings_manager:
            try:
                _db = DatabaseReader(settings_manager.get_database_path())
            except Exception:
                pass
        detail = _db.get_http_server_detail(ip_address) if _db else None
        port = int((detail or {}).get("port") or 80)
        scheme = (detail or {}).get("scheme") or "http"
        from gui.components.http_browser_window import HttpBrowserWindow
        HttpBrowserWindow(
            parent=detail_window,
            ip_address=ip_address,
            port=port,
            scheme=scheme,
            db_reader=_db,
            theme=theme,
            settings_manager=settings_manager,
        )
        return
    # ... existing SMB share precheck continues below ...
    raw_shares = _parse_accessible_shares(server_data.get('accessible_shares_list', ''))
    ...
```

**c) Probe worker (~L608) — add H branch:**

```python
elif host_type == "H":
    detail = db_accessor.get_http_server_detail(ip_address) if db_accessor else None
    port = int((detail or {}).get("port") or 80)
    scheme = (detail or {}).get("scheme") or "http"
    result = http_probe_runner.run_http_probe(
        ip_address,
        port=port,
        scheme=scheme,
        allow_insecure_tls=True,
        max_entries=max(1, int(config["max_directories"]) * int(config["max_files"])),
        max_directories=int(config["max_directories"]),
        max_files=int(config["max_files"]),
        connect_timeout=int(config["timeout_seconds"]),
        request_timeout=int(config["timeout_seconds"]),
        cancel_event=cancel_event,
    )
```

Post-probe for H (after result, mirrors FTP pattern):
```python
elif host_type == "H":
    shares = result.get("shares", [])
    first_share = shares[0] if shares else {}
    dir_names = [d.get("name") for d in first_share.get("directories", [])
                 if isinstance(d, dict) and d.get("name")]
    root_files = first_share.get("root_files", [])
    total_files = len(root_files) + sum(
        len(d.get("files", [])) for d in first_share.get("directories", [])
        if isinstance(d, dict)
    )                                                  # root + first-level subdir files
    total = len(dir_names) + total_files               # dirs + files (matches DB formula)
    server_data["total_shares"] = total
    server_data["accessible_shares"] = total
    server_data["accessible_shares_list"] = ",".join(dir_names)
    if db_accessor:
        try:
            db_accessor.upsert_probe_cache_for_host(
                ip_address, "H",
                status='issue' if analysis.get("is_suspicious") else 'clean',
                indicator_matches=len(analysis.get("matches", [])),
                snapshot_path=str(http_probe_cache.get_http_cache_path(ip_address)),
                accessible_dirs_count=len(dir_names),
                accessible_dirs_list=",".join(dir_names),
                accessible_files_count=total_files,
            )
        except Exception:
            pass
```

### 4. batch.py — two touch points

**a) `_execute_extract_target()` (~L334):**
```python
# Before: if host_type == "F": return skipped
# After:
if host_type in ("F", "H"):
    return {
        "ip_address": ip_address,
        "action": "extract",
        "status": "skipped",
        "notes": f"{host_type} extract not yet supported",
    }
```

**b) `_execute_probe_target()` (~L184):**

Add `elif host_type == "H":` branch after FTP, mirroring the FTP pattern:
- Call `get_http_server_detail()` for scheme/port
- Call `http_probe_runner.run_http_probe(...)`
- Post-probe: compute `total_files = len(root_files) + sum(len(d["files"]) for d in directories)`
- Set `total_shares = len(dir_names) + total_files`; upsert DB with `accessible_files_count=total_files`

### 5. dashboard.py `_probe_single_server()` (~L1362)

Add `elif host_type == "H":` before the SMB path, mirroring the FTP block:
- Call `self.db_reader.get_http_server_detail(ip_address)` for scheme/port
- Call `http_probe_runner.run_http_probe(...)`
- Post-probe: compute `total_files = len(root_files) + sum(len(d["files"]) for d in directories)`
- Set `total_shares = len(dir_names) + total_files`
- `upsert_probe_cache_for_host(ip, "H", ..., accessible_dirs_count=len(dir_names), accessible_dirs_list=..., accessible_files_count=total_files)`
- Return `{"ip_address": ..., "action": "probe", "status": "success", "notes": f"{total} entries"}`

### 6. batch_status.py probe-status attach (~L612)

```python
# Before:
if host_type == "F":
    status = server.get("probe_status") or "unprobed"
else:
    status = server.get("probe_status") or self._determine_probe_status(ip)

# After:
if host_type in ("F", "H"):
    status = server.get("probe_status") or "unprobed"
else:
    status = server.get("probe_status") or self._determine_probe_status(ip)
```

Rationale: HTTP probe_status is DB-supplied (from `http_probe_cache` via UNION query),
same as FTP. `_determine_probe_status()` reads the SMB file-based cache and must not be
called for non-SMB rows.

---

## HTTP Share Math (Alignment with DB Formula)

The UNION query already computes:
```sql
COALESCE(hpc.accessible_dirs_count, 0) + COALESCE(hpc.accessible_files_count, 0) AS total_shares
```

`accessible_files_count` must reflect the full one-level recursion scope:
```python
total_files = len(root_files) + sum(
    len(d.get("files", [])) for d in first_share.get("directories", [])
    if isinstance(d, dict)
)
total = len(dir_names) + total_files
```

All in-memory probe-result handlers must use this formula:
```python
server_data["total_shares"] = total
server_data["accessible_shares"] = total
server_data["accessible_shares_list"] = ",".join(dir_names)  # dirs only (matches DB column)
```

`upsert_probe_cache_for_host(...)` call:
```python
accessible_dirs_count=len(dir_names),
accessible_dirs_list=",".join(dir_names),
accessible_files_count=total_files,   # root + all first-level subdir files
```

This ensures the "Shares > 0" filter counts dirs + files consistently between fresh DB
loads and probe-result updates, and matches what the probe runner actually explored.

---

## Download / Quarantine Flow

```
HttpBrowserWindow._download_selected()
  → build_quarantine_path(ip_address, "http_root", purpose="http", base_path=quarantine_base)
      → ~/.smbseek/quarantine/<ip>/<YYYYMMDD>/http_root/
  → HttpNavigator.download_file(remote_path, dest_dir, progress_callback)
      → urllib.request.urlopen(url, timeout=request_timeout, context=ssl_ctx)
      → stream 8 KB chunks into dest_file
      → check cancel_event between chunks; remove partial on cancel
      → os.chmod(dest, stat & 0o666)  # strip executable bits
      → set mtime from Last-Modified header if present
      → return DownloadResult(saved_path, size, elapsed_seconds)
  → log_quarantine_event(quarantine_dir, f"Downloaded {remote_path} -> {result.saved_path}")
  → status_bar.set(f"Saved to {saved_path}")
```

Reuses: `shared.quarantine.build_quarantine_path`, `shared.quarantine.log_quarantine_event`

---

## Risk List + Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **HTML parsing brittleness** — IIS/Python/Caddy index formats differ from Apache/nginx | Medium | `validate_index_page()` gate filters non-index pages; `list_dir()` returns empty `ListResult` with `warning` instead of crashing |
| **Root-absolute href misrouting** — stripping `/` from `/pub/` then joining with current path `/data/` would yield `/data/pub/` (wrong) | Resolved | `_parse_dir_entries()` preserves root-absolute hrefs as-is; `list_dir()` uses them directly as the new path without any join arithmetic. `count_dir_entries()` in verifier.py remains unchanged. |
| **Unknown file size** — HTTP listings rarely show file sizes | Low | Show `"—"` in Size column; enforce `max_file_bytes` limit during streaming download |
| **Redirect chains to login/auth page** | Low | `validate_index_page()` gates on `<title>Index of`; `list_dir()` returns empty result |
| **urllib timeout reliability** — `urlopen(timeout=...)` may not interrupt on all platforms | Medium | Keep timeouts short (10s/8s); probe runner checks `cancel_event` between subdirs; Cancel button sets event; partial results returned |
| **`scheme` absent from UNION query** | Resolved | `get_http_server_detail(ip)` point lookup added to `DatabaseReader`; fallback to `"http"` if no row |
| **`_probe_single_server()` routes HTTP into SMB path** | Resolved | `elif host_type == "H"` branch added before SMB path in `dashboard.py` |
| **HTTP rows enter SMB extract flow in batch.py** | Resolved | `host_type in ("F", "H")` skip guard |
| **`_determine_probe_status()` called for HTTP rows** | Resolved | `host_type in ("F", "H")` guard in `batch_status.py` |
| **`_open_browse_window()` validates SMB shares for HTTP** | Resolved | HTTP branch exits before `_parse_accessible_shares()`; `db_reader` created locally via `settings_manager` (not from closure scope) |
| **HTTP detail popup renders as FTP** — `_format_server_details()` L229 defaults non-SMB to "FTP" | Resolved | `protocol_label` dict lookup added; `elif host_type == "H"` access_section branch added in `details.py` |
| **share math mismatch: root-only vs root+subdir files** | Resolved | `accessible_files_count = len(root_files) + sum(len(d["files"]) for d in directories)` — matches one-level recursion scope |
| **Same-name files from different dirs silently download wrong target** — joining `current_path + display_name` would collide | Resolved | `HttpBrowserWindow._path_map` maps `treeview_iid → abs_path`; all actions use `_path_map[iid]`, never path reconstruction |
| **Probe error schema mismatch** — renderer at `details.py:416-419` calls `err.get("share")` / `err.get("message")` on each error; plain strings throw `AttributeError` | Resolved | Probe runner stores errors as `{"share": "http_root", "message": "..."}` dicts throughout |
| **Double trailing slash in probe display** — renderer at `details.py:374` appends `/` to `dir_name`; if stored as `"uploads/"` it renders `"uploads//"` | Resolved | `directories[].name` stored without trailing slash (`PurePosixPath(abs_path).name`) |

---

## Regression Checklist

### Automated

```bash
# Full test suite (all must pass)
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short

# DB smoke test
python3 tools/db_bootstrap_smoketest.py
```

### Manual

**SMB/FTP unchanged:**
- [ ] SMB scan starts/stops from dashboard
- [ ] FTP scan starts/stops from dashboard
- [ ] SMB row → Browse → `FileBrowserWindow` opens
- [ ] FTP row → Browse → `FtpBrowserWindow` opens
- [ ] FTP probe runs and updates `accessible_dirs_count`
- [ ] FTP row probe status loads from DB, not SMB file cache
- [ ] FTP extract shows "not yet supported" (unchanged)
- [ ] SMB extract still works

**HTTP browser flow:**
- [ ] HTTP row shows Browse action
- [ ] Browse → `HttpBrowserWindow` opens with scheme/port from DB lookup
- [ ] Background probe runs, tree populates with dirs + files
- [ ] Probe cache written to `~/.smbseek/http_probes/<ip>.json`
- [ ] Close and reopen → cached probe reloads immediately
- [ ] Navigate into subdir (double-click) → shows subdir contents
- [ ] Root-absolute href (e.g. `/pub/`) navigates correctly (not silently dropped)
- [ ] Up button → returns to parent path
- [ ] Download a file → appears in `~/.smbseek/quarantine/<ip>/<date>/http_root/`
- [ ] Cancel during download → partial file removed, status bar updates

**Probe count integration:**
- [ ] After HTTP probe, `accessible_dirs_count + accessible_files_count` matches `total_shares` in server list
- [ ] Server list "Shares > 0" filter correctly includes/excludes HTTP rows
- [ ] HTTP probe status reads from DB (not `_determine_probe_status()`)

**Dashboard probe path:**
- [ ] Dashboard bulk probe for HTTP row calls `run_http_probe()` (not SMB probe)
- [ ] HTTP probe result notes show entry count (dirs + files)

**Edge cases:**
- [ ] HTTP row with `scheme="https"` → browser navigates on HTTPS
- [ ] Non-standard port (e.g. 8080) → browser navigates correctly
- [ ] Empty index page (0 dirs, 0 files) → empty tree, no crash
- [ ] Non-index HTTP target → status "Not an index listing", no crash
- [ ] Delete HTTP row → no crash (validation accepts `"H"`)
- [ ] HTTP row extract → "not yet supported" skip (not SMB extract flow)

---

## Copy/Paste Implementation Prompt

```text
Implement Card 5 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.

Branch: development (Card 4 complete at commit 086ec05)
Plan file: /home/kevin/.claude/plans/swirling-enchanting-biscuit.md — follow it exactly.

Deliver these files in order:

1. shared/http_browser.py
   - Module-level helper: _parse_dir_entries(body, current_path="/") -> (dir_abs_paths, file_abs_paths)
     - Returns absolute paths (not counts) from href parsing
     - Relative hrefs joined with current_path via PurePosixPath arithmetic
     - Root-absolute hrefs (starting with "/") used AS-IS — NOT stripped or joined
       (avoids misrouting: "/pub/" at "/data/" stays "/pub/", not "/data/pub/")
     - Skips: "../", "..", "?..." sort links, "//" protocol-relative, "://" external
     - Does NOT modify count_dir_entries() in verifier.py (unchanged)
   - HttpNavigator class (stateless, per-request)
     - Reuse Entry, ListResult, DownloadResult, ReadResult from shared.smb_browser
     - Reuse try_http_request, validate_index_page from commands.http.verifier
     - list_dir(path): uses _parse_dir_entries(body, current_path=path)
       Entry.name = absolute path (e.g. "/pub/"); display label = PurePosixPath(name).name
     - download_file(remote_path, dest_dir, progress_cb): streams 8 KB chunks,
       enforces max_file_bytes, strips exec bits, respects cancel_event, removes partial
     - read_file(path, max_bytes), cancel()

2. gui/utils/http_probe_cache.py
   - Exact mirror of ftp_probe_cache.py with ftp→http
   - Cache dir: ~/.smbseek/http_probes/<sanitized_ip>.json
   - Functions: get_http_cache_path, load_http_probe_result, save_http_probe_result,
     clear_http_probe_result

3. gui/utils/http_probe_runner.py
   - run_http_probe(ip, port, scheme, allow_insecure_tls, max_entries, max_directories,
     max_files, connect_timeout, request_timeout, cancel_event, progress_callback) -> dict
   - Imports _parse_dir_entries from shared.http_browser (no inline HTML parsing)
   - Fetches root /, validates, calls _parse_dir_entries(body, "/") for dir/file abs paths
   - root_files = last-segment names from file_abs_paths[:max_files]
   - For each dir_abs_path in dir_abs_paths[:max_directories]:
     - check cancel_event.is_set() before each fetch
     - fetch + validate; if valid: _parse_dir_entries(body, current_path=dir_abs_path)
       record files as last-segment basenames; subdirectories as last-segment names (no slash)
     - on any fetch/parse failure: append {"share": "http_root", "message": "<reason>"}
       to errors list (NOT raw strings — renderer calls err.get("share"))
     - directories[].name = PurePosixPath(dir_abs_path).name (NO trailing slash)
       renderer at details.py:374 appends "/" itself; storing with slash causes double-slash
   - accessible_files_count = len(root_files) + sum(len(d["files"]) for d in directories)
   - Snapshot schema: {ip_address, port, scheme, protocol="http", run_at, limits, shares, errors}
   - shares[0] = {share="http_root", root_files, root_files_truncated, directories,
     directories_truncated}
   - directories[i] = {name, subdirectories, subdirectories_truncated, files, files_truncated}
   - Calls save_http_probe_result(ip, snapshot) before return

4. gui/utils/database_access.py
   - Add get_http_server_detail(ip_address) to DatabaseReader
   - Returns {"scheme": str, "port": int} from http_servers table
   - Returns None if no row or HTTP tables absent; silently swallows all exceptions

5. gui/components/http_browser_window.py
   - HttpBrowserWindow(parent, ip_address, port, scheme, banner, config_path,
     db_reader, theme, settings_manager)
   - On init: load probe cache → populate tree; spawn background probe daemon thread
   - Treeview: Name (display label = basename only), Type, Size ("—"), Modified ("—"),
     hidden path_raw column holding full absolute path
   - Maintain self._path_map: Dict[str, str] mapping treeview_iid → abs_path
     Populated by _populate_tree() after each list_dir() result
     ALL navigate/view/download read abs_path via self._path_map[iid]
     NEVER reconstruct path as current_path + display_name (breaks same-name files)
   - Buttons: Up, Refresh, View, Download to Quarantine, Cancel
   - Download: self._path_map[iid] → build_quarantine_path(ip, "http_root", purpose="http")
     → HttpNavigator.download_file(abs_path, dest_dir)
     → log_quarantine_event(quarantine_dir, f"Downloaded {abs_path} -> {result.saved_path}")
     (purpose="http" matches FTP parity where FtpBrowserWindow uses purpose="ftp"; ensures
     consistent quarantine subdirectory naming and audit trail separation by protocol)
   - Text viewer only (no image viewer)
   - Cancel button sets threading.Event
   - Up: str(PurePosixPath(self._current_path).parent) or "/"
   - Mirror FtpBrowserWindow structure and threading patterns exactly

6. gui/components/server_list_window/actions/batch_operations.py
   - _launch_browse_workflow(): add elif host_type == "H" before SMB path
     - Call self.db_reader.get_http_server_detail(ip_addr) if self.db_reader
     - Open HttpBrowserWindow with scheme/port from detail or fallback defaults
   - Delete validation at ~L181: add "H" to ("S", "F") → ("S", "F", "H")

7. gui/components/server_list_window/details.py (four touch points)
   - _format_server_details (~L226-L229):
     - protocol_label: {"S": "SMB", "F": "FTP", "H": "HTTP"}.get(host_type, "Unknown")
     - Add elif host_type == "H" access_section before the FTP else branch
       showing port, banner/title, dirs count, files count
   - Probe cache load (~L77):
     - add elif host_type == "H" → http_probe_cache.load_http_probe_result
   - _open_browse_window (~L144): add if host_type == "H" BEFORE _parse_accessible_shares()
     - Instantiate db_reader locally via settings_manager (same pattern as _persist_notes ~L128)
     - Call db_reader.get_http_server_detail(ip_address)
     - Open HttpBrowserWindow, return — SMB share precheck never runs for HTTP
     - Also verify the Browse button binding in the details popup wires to _open_browse_window()
       regardless of host_type — confirm the button is not conditionally hidden or disabled
       for host_type "H" (check the button state logic near the Browse button creation)
   - Probe worker (~L608): add elif host_type == "H"
     - Instantiate db_accessor (already present in worker scope), call get_http_server_detail
     - Call run_http_probe(...)
     - Post-probe: total_files = len(root_files) + sum(len(d["files"]) for d in directories)
       total = len(dir_names) + total_files
       server_data["total_shares"] = server_data["accessible_shares"] = total
       server_data["accessible_shares_list"] = ",".join(dir_names)
     - upsert_probe_cache_for_host(ip, "H", ..., accessible_dirs_count=len(dir_names),
       accessible_dirs_list=",".join(dir_names), accessible_files_count=total_files)

8. gui/components/server_list_window/actions/batch.py
   - _execute_extract_target (~L334): if host_type in ("F", "H"): return skipped
   - _execute_probe_target (~L184): add elif host_type == "H" branch (mirrors F branch)
     - Call get_http_server_detail for scheme/port
     - Call run_http_probe(...)
     - total_files = len(root_files) + sum(len(d["files"]) for d in directories)
     - total = len(dir_names) + total_files; upsert with accessible_files_count=total_files

9. gui/components/dashboard.py
   - _probe_single_server (~L1362): add elif host_type == "H" before SMB path
     - Call self.db_reader.get_http_server_detail(ip_address) for scheme/port
     - Call http_probe_runner.run_http_probe(...)
     - Post-probe: total_files = len(root_files) + sum(len(d["files"]) for d in directories)
       total = len(dir_names) + total_files
     - upsert_probe_cache_for_host(ip, "H", ..., accessible_dirs_count=len(dir_names),
       accessible_dirs_list=..., accessible_files_count=total_files)
     - Return {"ip_address": ..., "action": "probe", "status": "success",
       "notes": f"{total} entries"}

10. gui/components/server_list_window/actions/batch_status.py
    - Probe-status attach (~L612): if host_type in ("F", "H"):
        status = server.get("probe_status") or "unprobed"
      HTTP rows read probe_status from DB, not _determine_probe_status()

Constraints:
- SMB and FTP browse/probe/extract flows must remain byte-for-byte unchanged
- HTTP browser is read-only (no remote write/delete)
- All downloads through build_quarantine_path(..., purpose="http") / log_quarantine_event(quarantine_dir, ...)
- purpose="http" in quarantine path creation (matches FTP parity: FtpBrowserWindow uses purpose="ftp")
- One-level recursion max in probe runner (root + one level of subdirs)
- _parse_dir_entries lives in shared/http_browser.py; no inline HTML parsing elsewhere
- count_dir_entries() in verifier.py is NOT modified
- No image viewer in HTTP browser window
- share math everywhere: total_files = root_files + subdir_files; total = dirs + total_files
- scheme always fetched via get_http_server_detail(); never assumed from server_data
- _open_browse_window() in details.py must create db_reader locally (not from closure scope)

Deliver:
- All new/modified files
- Brief per-file summary of what changed
- Manual test steps for HTTP browse, probe, and download flows
- Known limitations list
```

---

## Assumptions

1. `http_servers.scheme` column exists and is populated by Card 4 (confirmed: Card 4
   persists `HttpAccessOutcome.scheme` via `HttpPersistence.upsert_http_server()`).
2. `build_quarantine_path()` and `log_quarantine_event()` from `shared.quarantine` accept
   `"http_root"` as the share-name argument without modification.
3. `upsert_probe_cache_for_host(ip, "H", ...)` in `database_access.py` already handles
   `host_type="H"` gracefully (confirmed: L1033–1036 guards HTTP tables).
4. `probe_patterns.attach_indicator_analysis()` works on any snapshot with the standard
   `shares` list structure — no HTTP-specific changes needed.
5. `db_reader` is accessible in `details.py` probe worker via the `settings_manager`
   path (same as the existing FTP branch at ~L604).

## Out of Scope

- Full recursive site crawling / bulk mirror
- Normalized HTTP artifact DB tables (per-file rows)
- Authentication-gated HTTP indexes
- Image viewer in HTTP browser (deferred goal for Card 6 parity hardening)
- HTTP export via data export engine
- Pry integration for HTTP hosts
- Card 6 tests and documentation
