# Card 6 Implementation Plan: QA, Hardening, and Documentation

Status: implemented
Date: 2026-03-19
Depends on: Cards 1–5

---

## Context

- Card objective: Stabilize the HTTP MVP, add image-view parity with SMB/FTP, enforce regression gates, and document known limits.
- Why this card exists now: Cards 1–5 delivered a working HTTP discovery pipeline and browser window with text-file viewing. Card 6 closes the remaining parity gap (image preview) and produces the handoff package for future agents.
- What must remain untouched: SMB/FTP browser behavior, database math (`total_shares = accessible_dirs_count + accessible_files_count`), probe snapshot one-level recursion model, DB schema.

---

## 1. Pre-Revision Reality Check

1. Branch: `development`
2. Baseline: `xvfb-run -a python -m pytest gui/tests/ shared/tests/ -q` — 232 passed, 15 failed (all pre-existing, all in `test_ftp_scan_dialog.py`, `test_ftp_state_tables.py`, `test_timestamp_canonicalization.py`).
3. No HTTP test files existed before this card.
4. `http_browser_window.py` line 14 stated "No image viewer (HTTP index listings are web content)."

---

## 2. 5W Plan

- **Who:** Claude Code (claude-sonnet-4-6), branch `development`
- **What:** Add image preview to `HttpBrowserWindow`; add 14 new HTTP tests; update docs
- **Where:** `gui/components/http_browser_window.py`, `gui/tests/test_http_browser_window.py` (new), `gui/tests/test_http_probe.py` (new), `gui/tests/test_backend_progress_http.py` (new), `README.md`, `docs/dev/http_module/`
- **When:** Single commit; implementation order: browser code → tests → gate → docs
- **Why:** FTP browser already has image preview via shared `open_image_viewer()`; HTTP browser omitted it without a strong reason. Reusing the existing viewer path requires minimal new code and adds no new dependencies.

---

## 3. Proposed Design

HTTP image preview reuses the exact same path as FTP image preview:

```
_on_view()
  ├── suffix in IMAGE_EXTS? → is_image = True
  │     max_bytes = max_image_size_mb * 1024 * 1024
  │     _start_view_thread(..., is_image=True, max_image_pixels=...)
  │           → _open_image_viewer() → open_image_viewer() [shared]
  └── else → is_image = False
        max_bytes = max_view_size_mb * 1024 * 1024
        _start_view_thread(..., is_image=False, ...)
              → _open_viewer() → open_file_viewer() [shared]
```

HTTP-specific differences from FTP:
- Path resolution uses `_path_map[iid]` (absolute path from listing), not `_current_path + name`
- No pre-flight size guard: HTTP listings always have `Entry.size = 0`
- `display_path` format: `scheme://ip:port/path` (FTP uses `ip/ftp_root/path`)
- `_start_view_thread()` has no `size_raw` parameter (FTP-specific listing metadata)

Non-goals:
- No HTTPS mutual-TLS certificate handling in viewer
- No animated GIF playback
- No pre-flight size guard (HTTP listings carry no size data)

---

## 4. File/Function Change Plan

| File | Function/Class | Change Type | Risk | Notes |
|---|---|---|---|---|
| `gui/components/http_browser_window.py` | module docstring | modify | low | Remove "No image viewer" note |
| `gui/components/http_browser_window.py` | import block | add | low | Add `open_image_viewer` guarded import |
| `gui/components/http_browser_window.py` | `IMAGE_EXTS` | add | low | Module constant, mirrors FTP |
| `gui/components/http_browser_window.py` | `_load_http_browser_config()` | modify | low | Add `max_image_size_mb`, `max_image_pixels` defaults |
| `gui/components/http_browser_window.py` | `_on_view()` | modify | medium | Add image detection + dual-limit branch |
| `gui/components/http_browser_window.py` | `_start_view_thread()` | modify | medium | Add `is_image`, `max_image_pixels` params; add image dispatch |
| `gui/components/http_browser_window.py` | `_open_image_viewer()` | add | low | New method; FTP analog with HTTP URL format |
| `gui/tests/test_http_browser_window.py` | (new file) | add | low | 5 tests: file viewer, image viewer, error handling, image dispatch, text dispatch |
| `gui/tests/test_http_probe.py` | (new file) | add | low | 7 tests: cache round-trip, clear, IP sanitization, protocol fields, error shapes, subdir limits |
| `gui/tests/test_backend_progress_http.py` | (new file) | add | low | 2 tests: rollup parsing, success marker |
| `README.md` | HTTP Discovery section | add | low | New subsection after FTP Discovery |
| `docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md` | Card 6 status | modify | low | Mark delivered |
| `docs/dev/http_module/README.md` | Module Status | add | low | Note Cards 1–6 delivered |
| `docs/dev/http_module/claude_plan/06-card6.md` | (this file) | modify | low | Fill stub |

---

## 5. Edge Cases and Failure Modes

1. **HTTP `Entry.size` always 0**: no pre-flight size guard possible; image viewer may receive truncated bytes for very large images. Mitigation: `max_image_size_mb` config cap (default 15 MB) enforced by `read_file()`.
2. **PIL not installed**: `open_image_viewer()` raises `ImportError` or `RuntimeError`; caught by `_open_image_viewer()` try/except → `_set_status("View failed: ...")` + `messagebox.showerror()`. Graceful degradation.
3. **Corrupt or non-image bytes at image extension**: PIL raises exception; same try/except in `_open_image_viewer()` handles it.
4. **HTTPS with self-signed certs**: already handled by navigator (`allow_insecure_tls=True`); viewer receives plain bytes and is protocol-agnostic.
5. **Treeview column count (5 vs FTP's 6)**: tests explicitly check only `vals[0]`–`vals[4]`; no `vals[5]` reference introduced.

---

## 6. Validation Plan

### Gate A — Automated

```bash
source venv/bin/activate
python tools/db_bootstrap_smoketest.py
xvfb-run -a python -m pytest gui/tests/test_http_browser_window.py \
    gui/tests/test_http_probe.py gui/tests/test_backend_progress_http.py -v
xvfb-run -a python -m pytest gui/tests/ shared/tests/ --tb=no -q
```

Results:
- HTTP gate (3 new files): **14/14 passed**
- Full regression: see delivery summary below

```
AUTOMATED: PASS
```

### Gate B — Manual (HI)

1. Launch `./xsmbseek` against a known HTTP directory-index server
2. Open HTTP browser → navigate to a directory with image files
3. Select a `.png` → click View → image viewer opens, image renders
4. Select a `.jpg` → click View → image viewer opens
5. Select a `.txt` → click View → file viewer opens (text/hex mode)
6. Select a `.png` → click Download → file appears in `~/.smbseek/quarantine/<ip>/<YYYYMMDD>/http_root/`
7. FTP browser: view text file → unchanged (regression)
8. FTP browser: view image file → unchanged (regression)
9. SMB browse: unchanged (regression)

```
MANUAL:    PENDING (HI sign-off required)
OVERALL:   PASS (automated) / PENDING (manual)
```

---

## 7. Risks, Assumptions, Open Questions

- **Risk**: PIL (`Pillow`) must be installed in the venv. If not, image viewer raises on import. Current `requirements.txt` includes Pillow; risk is low.
- **Assumption**: HTTP `Entry.size` is always 0 from listing. If a future parser update populates it, the pre-flight guard can be added without breaking this card's code.
- **Assumption**: `_parse_dir_entries()` returns absolute paths (confirmed by `shared/http_browser.py`); test mock uses absolute paths accordingly.
- **Open question for HI**: Animated GIFs — PIL loads the first frame only. Acceptable for current MVP; deferred to post-MVP if needed.

---

## 8. Known Limits and Deferred Items

| Limit | Notes |
|---|---|
| No pre-flight size guard | HTTP listings carry `size=0`; guard not feasible without a HEAD request |
| Static GIF only | PIL renders first frame; animation not supported |
| HTTPS mutual TLS | Not supported; `allow_insecure_tls=True` only |
| One-level recursion | Probe snapshot walker unchanged; deep subdirectory files not indexed |

---

## 9. Delivery Summary

**Changed files:**

| File | Change |
|---|---|
| `gui/components/http_browser_window.py` | Add image preview (import, IMAGE_EXTS, config, _on_view, _start_view_thread, _open_image_viewer, docstring) |
| `gui/tests/test_http_browser_window.py` | New: 5 browser viewer tests |
| `gui/tests/test_http_probe.py` | New: 7 probe cache/runner tests |
| `gui/tests/test_backend_progress_http.py` | New: 2 progress-parser tests |
| `README.md` | Add HTTP Discovery subsection |
| `docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md` | Mark Card 6 delivered |
| `docs/dev/http_module/README.md` | Add module status note |
| `docs/dev/http_module/claude_plan/06-card6.md` | Fill stub (this file) |

**Test results:** 14 new HTTP tests added, all passing. Full regression: baseline passes maintained, zero new non-HTTP failures.
