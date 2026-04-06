# Card 6: HTTP QA, Hardening, and Documentation

## Context

Cards 1–5 delivered a working HTTP discovery pipeline with a browser window that supports text file viewing but lacks image preview parity with SMB/FTP. Card 6 closes this gap by adding image preview to `http_browser_window.py` using the existing shared `open_image_viewer()` path, then adds test coverage and docs to complete the HTTP module handoff.

---

## Part 1: `gui/components/http_browser_window.py` — Image Preview Parity

Seven surgical changes, all within one file.

### 1.1 Add `open_image_viewer` import (after line 34)

```python
try:
    from gui.components.image_viewer_window import open_image_viewer
except ImportError:
    from image_viewer_window import open_image_viewer
```

### 1.2 Add `IMAGE_EXTS` constant (after config loader, before class definition)

```python
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
```

Identical to `ftp_browser_window.py` line 77.

### 1.3 Expand `_load_http_browser_config()` defaults

Change the `"viewer"` dict (currently lines 48–50) from:
```python
"viewer": {
    "max_view_size_mb": 5,
},
```
to:
```python
"viewer": {
    "max_view_size_mb": 5,
    "max_image_size_mb": 15,
    "max_image_pixels": 20_000_000,
},
```

### 1.4 Rewrite `_on_view()` to add image branch

After retrieving `abs_path` and `display_name` (already present), add image detection and dual-limit logic mirroring FTP `_on_view()`. Key HTTP differences:
- Path comes from `self._path_map.get(iid, "")` (already the case)
- No `size_raw` pre-flight guard — HTTP listings always carry `size=0`; omit entirely
- Pass `is_image` and `max_image_pixels` to `_start_view_thread()`

New call signature — use all keyword args so tests can assert via `call_args.kwargs` cleanly:
```python
self._start_view_thread(
    remote_path=abs_path,
    display_name=display_name,
    max_bytes=max_view_bytes,
    is_image=is_image,
    max_image_pixels=max_image_pixels,
)
```

### 1.5 Rewrite `_start_view_thread()` signature and body

Add `is_image: bool` and `max_image_pixels: int` parameters. In `_read_thread()`:
- If `is_image`: dispatch `self._open_image_viewer(remote_path, result.data, result.size, result.truncated, max_image_pixels)`
- Else: keep existing `self._open_viewer(remote_path, result.data, result.size)`

No `size_raw` parameter needed — HTTP uses `result.size` directly (FTP's `size_raw or result.size` fallback is FTP-specific).

### 1.6 Add `_open_image_viewer()` method (after `_open_viewer()`)

Direct copy of `FtpBrowserWindow._open_image_viewer()` with HTTP URL format:
```python
display_path = f"{self.scheme}://{self.ip_address}:{self.port}{remote_path}"
```
(FTP uses `f"{self.ip_address}/ftp_root{remote_path}"`)

Same save callback, error handling, and `_set_status` calls.

### 1.7 Update module docstring

Change line 14 from:
```
  - No image viewer (HTTP index listings are web content).
```
to:
```
  - Image viewer: common raster formats (.png/.jpg/.gif/.bmp/.webp/.tif/.tiff) via shared image_viewer_window.
```

---

## Part 2: `gui/tests/test_http_browser_window.py` (new file)

Pattern after `test_ftp_browser_window.py`; factory function and mock shapes are the same but assertions use HTTP-specific URL format and column layout:

```python
def _make_window() -> HttpBrowserWindow:
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.port = 80
    win.scheme = "http"
    win.window = MagicMock()
    win.theme = None
    win._set_status = MagicMock()
    win._start_download_thread = MagicMock()
    return win
```

**5 test cases:**

1. `test_open_viewer_uses_shared_file_viewer_and_save_callback_downloads`
   - Asserts `file_path == "http://10.20.30.40:80/pub/readme.txt"` (URL format, not FTP's ip/ftp_root/ format)
   - Save callback calls `_start_download_thread([("/pub/readme.txt", 123)])`

2. `test_open_image_viewer_uses_shared_image_viewer_and_save_callback_downloads`
   - Asserts `file_path == "http://10.20.30.40:80/media/logo.png"`
   - Checks `content`, `max_pixels`, `truncated`, save callback

3. `test_open_image_viewer_shows_error_when_viewer_raises`
   - Patches `open_image_viewer` to raise `RuntimeError("bad image")`
   - Asserts `_set_status("View failed: bad image")` and `messagebox.showerror("View Error", "bad image", parent=win.window)`

4. `test_on_view_uses_image_limits_and_dispatches_view_thread`
   - Sets `win._path_map = {"item-1": "/pub/photo.jpg"}`
   - HTTP tree has 5 columns (`name`, `type`, `size`, `modified`, `path_raw`) — no `vals[5]`
   - `tree.item.return_value = ("photo.jpg", "file", "—", "—", "/pub/photo.jpg")`
   - Since `_start_view_thread` is called with all keyword args (see 1.4), assert via `call_args.kwargs`:
     `kw = win._start_view_thread.call_args.kwargs`
   - Asserts `kw["remote_path"] == "/pub/photo.jpg"`, `kw["display_name"] == "photo.jpg"`, `kw["max_bytes"] == 15*1024*1024`, `kw["is_image"] is True`, `kw["max_image_pixels"] == 123456`
   - Note: no `size_raw` arg (HTTP-specific; FTP passes `size_raw=1024`)

5. `test_on_view_uses_text_limits_and_dispatches_view_thread`
   - Same setup but with a `.txt` file: `win._path_map = {"item-2": "/pub/readme.txt"}`
   - `tree.item.return_value = ("readme.txt", "file", "—", "—", "/pub/readme.txt")`
   - Assert via `call_args.kwargs`: `kw["max_bytes"] == 5*1024*1024`, `kw["is_image"] is False`
   - Assert `kw["max_image_pixels"] == 20_000_000` (passed through to `_start_view_thread` but ignored when `is_image=False`)

---

## Part 3: `gui/tests/test_http_probe.py` (new file)

Pattern after `test_ftp_probe.py`. Uses `monkeypatch` to redirect `HTTP_CACHE_DIR`. HTTP runner imports are:
- `try_http_request`, `validate_index_page` from `commands.http.verifier`
- `_parse_dir_entries` from `shared.http_browser`

Patch targets: `gui.utils.http_probe_runner.try_http_request`, `gui.utils.http_probe_runner.validate_index_page`, `gui.utils.http_probe_runner._parse_dir_entries`

**7 test cases:**

1. `test_cache_save_and_load` — round-trip; verify `protocol == "http"`, `shares[0]["share"] == "http_root"`
2. `test_cache_clear` — load returns None after clear
3. `test_cache_ip_sanitization` — file named `"1.2.3.4.json"`, no `..`, `/`, `\`
4. `test_snapshot_protocol_and_scheme_fields` — patch `try_http_request` (returns `(200, b"<body>", False, None)`) + `validate_index_page` (returns `True`) + `_parse_dir_entries` (returns `([], [])`); assert `snapshot["protocol"] == "http"` and `snapshot["scheme"] == "http"`
5. `test_root_fetch_failure_recorded_in_errors` — patch `try_http_request` to return `(0, b"", False, "connection refused")`; assert `snapshot["errors"]` non-empty and all items are dicts with key `"share"`
6. `test_errors_are_dicts_not_strings` — verify all items in `snapshot["errors"]` are `dict` instances (HTTP-specific; FTP errors are plain strings)
7. `test_directory_listing_limits_subdirs_and_files_independently` — HTTP runner uses `try_http_request` + `_parse_dir_entries` directly (no navigator class); patch them to: root returns `(["/pub/"], [])`, subdir returns `(["/a/","/b/","/c/"], ["/pub/f1.txt","/pub/f2.txt","/pub/f3.txt"])`; call `run_http_probe(..., max_directories=2, max_files=2)`; assert `subdirectories_truncated is True`, `files_truncated is True`, and lists are capped at 2 entries each

---

## Part 4: `gui/tests/test_backend_progress_http.py` (new file)

Pattern after `test_backend_progress_ftp.py`. HTTP success marker (from `shared/http_workflow.py:51`): `"🎉 HTTP scan completed successfully"`.

**2–3 test cases:**

```python
_HTTP_OUTPUT_WITH_ANSI = (
    f"{_BLUE}[1/2] HTTP Discovery{_RESET}\n"
    f"{_BLUE}[2/2] HTTP Access Verification{_RESET}\n"
    "📊 Hosts Scanned: 15\n"
    "🔓 Hosts Accessible: 4\n"
    "📁 Accessible Directories: 9\n"
    "🎉 HTTP scan completed successfully\n"
)
```

1. `test_parse_rollup_with_ansi` — asserts `hosts_scanned == 15`, `hosts_accessible == 4`, `accessible_shares == 9`
2. `test_success_marker_detected` — asserts `result["success"] is True`

---

## Part 5: Documentation

### 5.1 `docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md`
Add a brief "Delivered:" note or status line at the end of the Card 6 section. No structural changes.

### 5.2 `docs/dev/http_module/claude_plan/06-card6.md`
Fill the existing stub using the 00-CARD_TEMPLATE.md structure. Key sections:
- Status: delivered
- File/function change table (see Parts 1–4 above)
- Known limits: no pre-flight size guard (HTTP listings carry `size=0`), PIL required for image rendering, HTTPS with mutual TLS not supported
- Validation: Gate A = test counts, Gate B = manual HI checklist

### 5.3 `README.md`
Add `### HTTP Discovery **(EXPERIMENTAL)**` subsection after the FTP Discovery section. Covers: Shodan trigger, HTTP/HTTPS verification, browser window, image preview (new in Card 6), quarantine path `~/.smbseek/quarantine/<ip>/<YYYYMMDD>/http_root/`.

### 5.4 `docs/dev/http_module/README.md` (if exists)
Add brief "Module Status" note: Cards 1–6 delivered, image preview added in Card 6.

---

## Constraints Preserved

| Concern | Behavior |
|---|---|
| `_path_map` routing | `abs_path` always from `self._path_map.get(iid)` — unchanged |
| HTTP file size | `Entry.size` always 0; no pre-flight size guard added |
| FTP/SMB code | Zero changes to ftp_browser_window.py or any SMB code |
| Database math | `total_shares = accessible_dirs_count + accessible_files_count` — untouched |
| Schema | No schema changes |
| Treeview columns | HTTP tree has 5 columns; tests use only `vals[0]`–`vals[4]` |
| `display_path` | HTTP: `scheme://ip:port/path`; FTP: `ip/ftp_root/path` |

---

## Implementation Order

1. Edit `gui/components/http_browser_window.py` (all 7 sub-steps)
2. Create `gui/tests/test_http_browser_window.py` (5 tests) — run immediately
3. Create `gui/tests/test_http_probe.py` (7 tests) — run immediately
4. Create `gui/tests/test_backend_progress_http.py` (2 tests) — run immediately
5. Fill `docs/dev/http_module/claude_plan/06-card6.md`
6. Add HTTP section to `README.md`
7. Update `docs/dev/http_module/README.md`
8. Mark Card 6 done in `HTTP_PHASE_TASK_CARDS.md`
9. Gate: run only the 3 new HTTP test files first — `xvfb-run -a python -m pytest gui/tests/test_http_browser_window.py gui/tests/test_http_probe.py gui/tests/test_backend_progress_http.py -v` — all must pass before proceeding
10. Full regression: `xvfb-run -a python -m pytest gui/tests/ shared/tests/ --tb=no -q`

---

## Critical Files

| File | Purpose |
|---|---|
| `gui/components/http_browser_window.py` | Core behavioral change |
| `gui/components/ftp_browser_window.py` | Pattern source — do not modify |
| `gui/components/image_viewer_window.py` | Shared viewer — do not modify |
| `gui/tests/test_ftp_browser_window.py` | Test pattern source |
| `gui/tests/test_ftp_probe.py` | Test pattern source |
| `gui/utils/http_probe_runner.py` | Import targets for probe tests |
| `gui/utils/http_probe_cache.py` | Cache module for probe tests |
| `gui/utils/backend_interface/progress.py` | `parse_final_results()` for progress test |

---

## Verification

```bash
source venv/bin/activate
python tools/db_bootstrap_smoketest.py
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q
```

Expected: all baseline passes still passing + ≥14 new HTTP tests (5 browser + 7 probe + 2 progress), with zero new non-HTTP failures introduced. Pre-existing failures must remain unchanged.

Manual HI checklist:
- Launch dashboard, open HTTP browser for a known IP
- View a `.txt` file → file viewer opens with text content
- View a `.png` file → image viewer opens with image rendered
- View a `.jpg` file → image viewer opens
- Download a file → appears in `~/.smbseek/quarantine/<ip>/<YYYYMMDD>/http_root/` (per `shared/quarantine.py`)
- FTP browser: view text file → unchanged (regression)
- FTP browser: view image file → unchanged (regression)
- SMB browse: unchanged (regression)
