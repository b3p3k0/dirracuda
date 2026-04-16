# Baseline Contracts

**Status:** C0 populated — active contract freeze  
**Date:** 2026-04-15  
**Populated by:** C0 — Contract Freeze + Baseline  
**Scope:** Regression target for C1–C10 modularization

---

## 1) Public Import/API Contracts

### 1.1 DashboardWidget

**Module:** `gui.components.dashboard`  
**Canonical import:** `from gui.components.dashboard import DashboardWidget`

**Constructor (frozen):**
```python
DashboardWidget(
    parent: tk.Widget,
    db_reader: DatabaseReader,
    backend_interface: BackendInterface,
    config_path: str = None
)
```

**Public methods (frozen):**
| Method | Signature |
|---|---|
| `set_drill_down_callback` | `(callback: Callable[[str, Dict], None]) -> None` |
| `set_config_editor_callback` | `(callback: Callable[[str], None]) -> None` |
| `set_size_enforcement_callback` | `(callback: Callable[[], None]) -> None` |
| `start_scan_progress` | `(scan_type: str, countries: List[str]) -> None` |
| `update_scan_progress` | `(percentage: Optional[float], message: str) -> None` |
| `finish_scan_progress` | `(success: bool, results: Dict[str, Any]) -> None` |
| `enable_mock_mode` | `() -> None` |
| `disable_mock_mode` | `() -> None` |

**Public attributes (frozen — set in `__init__`):**
`parent`, `db_reader`, `backend_interface`, `config_path`, `scan_manager`, `settings_manager`, `theme`, `current_scan`, `current_scan_options`, `status_text`, `clamav_status_text`, `tmpfs_status_text`

**Production callers:**
| File | Line | Usage |
|---|---|---|
| `dirracuda` (main entry script) | 400 (import), 786 (instantiation) | `self.dashboard = DashboardWidget(root, db_reader, backend_interface, config_path)` |
| `gui/main.py` | 31 | Module-level import for app bootstrap |

**Test callers (9 files):**
`test_dashboard_runtime_status_lines.py`, `test_dashboard_scan_dialog_wiring.py`, `test_ftp_scan_dialog.py`, `test_theme_runtime_toggle.py`, `test_clamav_results_dialog.py`, `test_dashboard_bulk_ops.py`, `test_dashboard_reddit_wiring.py`, `test_extract_runner_clamav.py`, `test_dashboard_api_key_gate.py`

---

### 1.2 unified_browser_window

**Module:** `gui.components.unified_browser_window`  
**Canonical import:** `from gui.components.unified_browser_window import ...`  
**Also imported as alias:** `import gui.components.unified_browser_window as ubw`

**Frozen public functions:**
| Function | Signature |
|---|---|
| `open_ftp_http_browser` | `(host_type, parent, ip_address, port, *, initial_path=None, banner=None, scheme=None, config_path=None, db_reader=None, theme=None, settings_manager=None) -> None` |
| `open_smb_browser` | `(parent, ip_address, shares, auth_method="", *, config_path=None, db_reader=None, theme=None, settings_manager=None, share_credentials=None, on_extracted=None) -> None` |
| `open_file_viewer` | `(*args, **kwargs) -> Any` |
| `open_image_viewer` | `(*args, **kwargs) -> Any` |

**Frozen public classes:**
| Class | Notes |
|---|---|
| `UnifiedBrowserCore` | Abstract base — tests import directly |
| `FtpBrowserWindow` | Tests instantiate directly (`test_ftp_browser_window.py`) |
| `HttpBrowserWindow` | Tests instantiate directly (`test_http_browser_window.py`) |
| `SmbBrowserWindow` | Tests import and instantiate directly |

**Compatibility-exported private helpers (must remain importable):**
| Symbol | Reason |
|---|---|
| `_extract_smb_banner` | `test_smb_browser_window.py` imports directly from this module |
| `_coerce_bool` | Defined line 93; used at 10+ internal call sites |
| `_format_file_size` | Defined line 78; used at 6+ internal call sites |

Note: `_coerce_int` does NOT exist in this module.

**Production callers (6 files):**
| File | Symbols used |
|---|---|
| `gui/components/ftp_server_picker.py:180` | `open_ftp_http_browser` |
| `gui/components/reddit_browser_window.py:35` | `open_ftp_http_browser` |
| `gui/components/server_list_window/actions/batch_operations.py:1171,1203` | `open_ftp_http_browser` |
| `gui/components/server_list_window/actions/batch_operations.py:1254` | `open_smb_browser` |
| `gui/components/server_list_window/details.py:190` | `open_ftp_http_browser` |
| `gui/components/server_list_window/details.py:234` | `open_smb_browser` |

**Test callers (6 files):**
| File | Symbols used |
|---|---|
| `gui/tests/test_smb_virtual_root.py` | `SmbBrowserWindow`, `open_smb_browser` |
| `gui/tests/test_browser_clamav.py` | Multiple classes/functions |
| `gui/tests/test_ftp_browser_window.py` | `FtpBrowserWindow` |
| `gui/tests/test_smb_browser_window.py` | `_extract_smb_banner` |
| `gui/tests/test_http_browser_window.py` | `HttpBrowserWindow` |
| `gui/tests/test_action_routing.py:887,917` | Module imported as `ubw` |

---

## 2) Patch-Sensitive Test Contracts

Any card that moves code must preserve these paths exactly. A shim must re-import these names at module scope.

### 2a. Dashboard dotted-string patches (must survive C6–C9)

```
gui.components.dashboard.messagebox.showerror               (test_dashboard_bulk_ops.py:66, test_dashboard_reddit_wiring.py:290)
gui.components.dashboard.messagebox.showinfo                (test_dashboard_api_key_gate.py:83, test_dashboard_bulk_ops.py:67)
gui.components.dashboard.messagebox.showwarning             (test_dashboard_scan_dialog_wiring.py:56)
gui.components.dashboard.show_unified_scan_dialog           (test_dashboard_scan_dialog_wiring.py:53)
gui.components.dashboard.show_reddit_grab_dialog            (test_dashboard_reddit_wiring.py)
gui.components.dashboard.run_ingest                         (test_dashboard_reddit_wiring.py:272)
gui.components.dashboard.threading.Thread                   (test_dashboard_reddit_wiring.py:348)
gui.components.dashboard.create_quarantine_dir              (test_extract_runner_clamav.py:415, test_clamav_results_dialog.py:262)
gui.components.dashboard.extract_runner.run_extract         (test_clamav_results_dialog.py:263)
gui.components.dashboard.DashboardWidget._extract_single_server  (test_extract_runner_clamav.py:521)
gui.components.dashboard.dispatch_probe_run                 (test_dashboard_bulk_ops.py)
gui.components.dashboard.probe_patterns.attach_indicator_analysis
gui.components.dashboard.get_probe_snapshot_path_for_host
gui.components.dashboard.tk.Toplevel
gui.components.dashboard.tk.Label
gui.components.dashboard.tk.Button
gui.components.dashboard.ttk.Progressbar
```

**C9 shim constraint:** The shim must import at module scope: `messagebox`, `threading`, `tk`, `ttk`, `extract_runner`, `dispatch_probe_run`, `probe_patterns`, `get_probe_snapshot_path_for_host`, `show_unified_scan_dialog`, `show_reddit_grab_dialog`, `run_ingest`, `create_quarantine_dir`. The `DashboardWidget` class must be re-exported as the same class object (not a wrapper) so `DashboardWidget._extract_single_server` remains accessible.

### 2b. Dashboard instance-level patches (via `dash` object)

```
dash._prompt_for_shodan_api_key   (method on DashboardWidget instance)
dash._check_external_scans        (method on DashboardWidget instance)
dash.indicator_patterns            (attribute on instance)
dash.db_reader                     (attribute on instance)
```

These survive via the constructor contract — the instance must have these attributes/methods after `__init__`.

### 2c. Browser dotted-string patches (must survive C2–C5)

```
gui.components.unified_browser_window.threading.Thread          (test_browser_clamav.py:333, test_http_browser_window.py:209, test_ftp_browser_window.py:155)
gui.components.unified_browser_window.messagebox.showinfo       (test_browser_clamav.py:209, :216)
gui.components.unified_browser_window.messagebox.showwarning    (test_browser_clamav.py:542)
gui.components.unified_browser_window.messagebox.showerror      (test_browser_clamav.py)
gui.components.unified_browser_window.open_file_viewer          (test_ftp_browser_window.py:79)
gui.components.unified_browser_window.open_image_viewer         (test_http_browser_window.py:93)
gui.components.unified_browser_window.queue.Queue               (test_browser_clamav.py:661)
gui.components.unified_browser_window.show_clamav_results_dialog (test_browser_clamav.py:241)
gui.components.unified_browser_window.FtpBrowserWindow          (test_action_routing.py:825)
gui.components.unified_browser_window.HttpBrowserWindow         (test_action_routing.py:855)
gui.components.unified_browser_window.tk.Toplevel               (test_http_browser_window.py:313)
gui.components.unified_browser_window.tk.Frame                  (test_http_browser_window.py:314)
gui.components.unified_browser_window.tk.Text                   (test_http_browser_window.py:315)
gui.components.unified_browser_window.tk.Button                 (test_http_browser_window.py:316)
gui.components.unified_browser_window.tk.StringVar              (test_http_browser_window.py:317)
gui.components.unified_browser_window.tk.IntVar                 (test_http_browser_window.py:318)
gui.components.unified_browser_window.tk.Spinbox                (test_http_browser_window.py:321)
gui.components.unified_browser_window.tk.Label                  (test_http_browser_window.py:322)
gui.components.unified_browser_window.ttk.Scrollbar             (test_http_browser_window.py:319)
gui.components.unified_browser_window.ttk.Treeview              (test_http_browser_window.py:320)
```

**C2–C5 shim constraint:** Shim must import at module scope: `threading`, `messagebox`, `queue`, `tk`, `ttk`. Must define or re-export as callable names (not lazy references): `open_file_viewer`, `open_image_viewer`, `show_clamav_results_dialog`, `FtpBrowserWindow`, `HttpBrowserWindow`.

### 2d. Browser module-attribute patches via `ubw` alias (must survive C2–C5)

All of these names must remain accessible as attributes of `gui.components.unified_browser_window`:

```
ubw.open_ftp_http_browser
ubw.open_smb_browser
ubw.open_file_viewer
ubw.open_image_viewer
ubw.FtpBrowserWindow
ubw.HttpBrowserWindow
ubw.threading
ubw.queue
ubw.tk
ubw.ttk
ubw.messagebox
ubw.show_clamav_results_dialog
```

### 2e. Unrelated module patches (not changed in C1–C10)

```
gui.components.app_config_dialog.messagebox.showerror
gui.components.app_config_dialog.messagebox.showwarning
gui.components.app_config_dialog.normalize_database_path
gui.components.app_config_dialog.AppConfigDialog
gui.components.unified_scan_dialog.messagebox.showerror
gui.components.unified_scan_dialog.run_preflight
gui.components.unified_scan_dialog.UnifiedScanDialog
gui.components.reddit_browser_window.store.init_db
gui.utils.extract_runner.SMBConnection
gui.utils.extract_runner.build_clamav_post_processor
gui.utils.extract_runner.run_extract
gui.utils.http_probe_cache.HTTP_CACHE_DIR
gui.utils.ftp_probe_cache.FTP_CACHE_DIR
gui.main.messagebox.showwarning
gui.main.bootstrap_tmpfs_quarantine
gui.main.consume_tmpfs_startup_warning
shared.db_migrations.run_migrations
shared.clamav_scanner.shutil.which
shared.clamav_scanner.subprocess.Popen
shared.smb_browser.SMBNavigator
commands.access.operation.SMB_AVAILABLE
commands.access.operation.share_enumerator.preflight_access_backend
```

---

## 3) Runtime Call Paths (Critical)

These are the key runtime paths that connect callers to the modules being modularized. Shim conversions must not break any of these.

| Path | Trigger | Invariant |
|---|---|---|
| `dirracuda:400` import → `gui.components.dashboard.DashboardWidget` | App startup | Import must succeed at `gui.components.dashboard` exactly |
| `dirracuda:786` instantiation → `DashboardWidget(root, db_reader, backend_interface, config_path)` | App startup `_create_dashboard()` | Constructor must accept same 4 args; instance must have `set_drill_down_callback`, `set_config_editor_callback`, `set_size_enforcement_callback` |
| `gui/main.py:31` import → `DashboardWidget` | GUI module load | Same import path must be valid |
| `server_list_window/details.py:234` → `open_smb_browser(...)` | User opens SMB browser from server list | Function signature must remain stable |
| `server_list_window/details.py:190` → `open_ftp_http_browser(...)` | User opens FTP/HTTP browser from server list | Function signature must remain stable |
| `batch_operations.py:1254` → `open_smb_browser(...)` | Batch open SMB | Same |
| `batch_operations.py:1171,1203` → `open_ftp_http_browser(...)` | Batch open FTP/HTTP | Same |
| `ftp_server_picker.py:180` → `open_ftp_http_browser(...)` | FTP picker launches browser | Same |
| `reddit_browser_window.py:35` → `open_ftp_http_browser(...)` | Reddit grab launches browser | Same |

---

## 4) Parser/Output Contract Notes

The GUI ↔ backend output parsing is handled by `gui/utils/backend_interface/` and `gui/utils/scan_manager.py`. These are **not modified in C1–C10** and are listed here for completeness.

| Output shape | Consumer | Refactor constraint |
|---|---|---|
| `PROGRESS:N:message` lines on stdout | `gui/utils/backend_interface/interface.py` | Not touched in C1–C10 |
| SMB/FTP/HTTP rollup markers | `gui/utils/scan_manager.py` | Not touched in C1–C10 |
| Dashboard `update_scan_progress(pct, msg)` | Called by scan_manager during active scan | Method signature frozen (see §1.1) |
| Dashboard `finish_scan_progress(success, results)` | Called by scan_manager on scan completion | Method signature frozen (see §1.1) |

---

## 5) Baseline Validation Commands

**Exit-code rule:** Never pipe pytest to a filter as the final step. Use redirect + explicit `RESULT=$?`.
```bash
# Canonical pattern:
./venv/bin/python -m pytest [args] > /tmp/out.txt 2>&1; RESULT=$?
cat /tmp/out.txt
echo "pytest exit=${RESULT}"
```

### Full suite (run before AND after every card)
```bash
xvfb-run -a ./venv/bin/python -m pytest --tb=short -q > /tmp/pytest_full.txt 2>&1; RESULT=$?
cat /tmp/pytest_full.txt
echo "pytest exit=${RESULT}"
```

### Dashboard contract tests
```bash
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  -v > /tmp/pytest_dash.txt 2>&1; RESULT=$?
cat /tmp/pytest_dash.txt
echo "pytest exit=${RESULT}"
```

### Browser contract tests
```bash
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_smb_virtual_root.py \
  gui/tests/test_browser_clamav.py \
  gui/tests/test_action_routing.py \
  -v > /tmp/pytest_browser.txt 2>&1; RESULT=$?
cat /tmp/pytest_browser.txt
echo "pytest exit=${RESULT}"
```

### Messagebox guardrail
```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_messagebox_guardrail.py -v > /tmp/pytest_guard.txt 2>&1; RESULT=$?
cat /tmp/pytest_guard.txt
echo "pytest exit=${RESULT}"
```

### Import smoke
```bash
./venv/bin/python -c "
from gui.components.dashboard import DashboardWidget
from gui.components.unified_browser_window import (
    open_ftp_http_browser, open_smb_browser, open_file_viewer, open_image_viewer,
    UnifiedBrowserCore, FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow,
    _extract_smb_banner, _coerce_bool, _format_file_size,
)
import gui.components.unified_browser_window as ubw
assert hasattr(ubw, 'threading'), 'ubw.threading missing'
assert hasattr(ubw, 'messagebox'), 'ubw.messagebox missing'
assert hasattr(ubw, 'queue'), 'ubw.queue missing'
assert hasattr(ubw, 'tk'), 'ubw.tk missing'
assert hasattr(ubw, 'ttk'), 'ubw.ttk missing'
print('IMPORT SMOKE: PASS')
"
```

### Line count (record before C1, compare at C10)
```bash
wc -l gui/components/dashboard.py gui/components/unified_browser_window.py
```

### Card-specific additions
| Card | Additional command |
|---|---|
| C1 | `xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -k "coerce or filesize" -v` + `python -c "import gui.utils.coercion, gui.utils.filesize; print('OK')"` |
| C2–C5 | `./venv/bin/python -c "import gui.browsers; print('OK')"` |
| C6–C8 | Dashboard contract tests only; do NOT test `import gui.dashboard` (package not created until C9) |
| C9 | `./venv/bin/python -c "import gui.dashboard; print('OK')"` |
| C10 | Full suite + line count comparison vs. §4.1 baseline |

### Manual (HI)
Required for C4, C5, C7, C8, C9, C10. See §6 gate definitions for per-card criteria.

---

## 6) Card Gates (C1–C10)

**Conventions:**
- AUTOMATED PASS = full suite exits 0 + import smoke PASS
- AUTOMATED FAIL = any test regression OR import smoke failure
- MANUAL = HI verifies runtime behavior in live app
- OVERALL = both required gates PASS

| Card | AUTOMATED PASS | MANUAL required | MANUAL PASS |
|---|---|---|---|
| C1 | Suite passes; `gui.utils.coercion` + `gui.utils.filesize` importable; coercion semantics unchanged | No | — |
| C2 | `gui.browsers` importable; shim re-exports all frozen symbols; browser contract tests pass | No | — |
| C3 | `from gui.browsers.core import UnifiedBrowserCore` works; shim re-exports it; browser tests pass | No | — |
| C4 | `gui.browsers.ftp_browser` + `gui.browsers.http_browser` importable; both classes accessible from shim and new path | Yes | Open FTP + HTTP browser in live app; verify file listing, navigation, download |
| C5 | `gui.browsers.smb_browser` + `gui.browsers.factory` importable; all browser symbols in shim; browser tests pass | Yes | Open SMB browser; verify share listing, navigation, file access |
| C6 | Status extraction exists; dashboard contract tests pass; NO `import gui.dashboard` check | No | — |
| C7 | Scan lifecycle extraction exists; `start/update/finish_scan_progress` callable on `DashboardWidget` instance; dashboard tests pass | Yes | Launch SMB scan from GUI; verify progress updates, scan completes, results appear |
| C8 | Batch ops extraction exists; `test_dashboard_bulk_ops.py` passes; dashboard tests pass | Yes | Trigger bulk extract from server list; verify ClamAV dialog and results |
| C9 | `gui.dashboard` importable; `gui.components.dashboard` shim re-exports all §2a names at module scope; all dashboard tests pass; `dirracuda` starts | Yes | Full app startup; scan; view results; open browser; all controls functional; test with pre-migration DB |
| C10 | Full suite passes (≥ baseline count); line counts reduced vs. §4.1; coverage delta documented | Yes | Full regression: SMB + FTP + HTTP scan, server list, browser, extract, ClamAV, Reddit grab |

---

## 7) Risks and Mitigations

| ID | Risk | Cards | Mitigation |
|---|---|---|---|
| R1 | Import breakage — moved symbols break dotted import paths or `ubw.*` patches | C2–C9 | Preserve original module as shim; re-export ALL frozen symbols; import smoke after every card |
| R2 | Behavior drift — scan lifecycle call expectations change during extraction | C6–C9 | Dashboard contract tests before/after each card; HI gate on scan behavior for C7 |
| R3 | Legacy DB regressions — startup assumptions break on pre-migration DB | C1, C6–C9 | No schema changes unless scoped; legacy smoke check (old DB) required for C9 |
| R4 | Known failures accumulating | C1–C10 | §3.3 documents pre-existing failures; PASS/FAIL evidence required per card; fix low-cost issues immediately |
| R5 | Performance regression — extra indirection adds latency | C3–C9 | Keep shim calls lightweight; validate responsiveness in HI gates |
| R6 | Type coercion drift — `_coerce_bool` consolidation changes semantics | C1 | Focused coercion unit tests; preserve current accepted value set; test edge inputs before replacing call sites |
| R7 | Oversized card scope | C3–C9 | Stop if diff grows beyond plan scope; prefer mechanical move + shim over behavior changes |
| R8 | Tooling blockers — headless display; gitignore hides files | C1–C10 | Use `xvfb-run -a` for GUI tests; `git add -f` for `docs/dev/` and `gui/tests/` paths |
| R9 | Shim re-export incomplete — `ubw.*` attribute missing from shim | C2–C9 | Import smoke checks `ubw.threading`, `ubw.messagebox`, `ubw.queue`, `ubw.tk`, `ubw.ttk` |
| R10 | Test command false-pass — filter in pipe masks pytest exit code | C0–C10 | All commands redirect to file (`> /tmp/x.txt 2>&1; RESULT=$?`); never `\| tail` or `\| grep` as final stage |

---

## 8) Assumptions and Open Questions

### Assumptions
1. `gui/tests/` and `docs/dev/` may be gitignored — use `git add -f` when staging.
2. GUI tests require a display; `xvfb-run` is present (confirmed at C0).
3. `_extract_smb_banner` is compatibility-exported — tests import it directly from this module; any shim must re-export it.
4. `_coerce_int` does NOT exist in `unified_browser_window.py` — C1 scope is `_coerce_bool` and `_format_file_size` only.
5. `dirracuda` (main entry script, no `.py` extension) is a production caller — C9 shim must not break the import at line 400 or instantiation at line 786.
6. Module-level names `threading`, `messagebox`, `tk`, `ttk`, `queue` must remain importable from `gui.components.unified_browser_window` after any shim conversion.
7. Baseline test suite had 2 pre-existing failures (see §3.3) — these are not introduced by C0 and must not be fixed within C1–C10 card scope.

### Open Questions / Blockers for C1
- **B4:** Full `ubw.*` module-attribute patch list not exhaustively verified. Before C2 starts: grep `test_action_routing.py`, `test_browser_clamav.py`, `test_ftp_browser_window.py`, `test_http_browser_window.py` for every `monkeypatch.setattr(ubw, ...)` call — update §2d with confirmed complete list.

---

## Appendix A — Baseline Metrics (C0)

**Date recorded:** 2026-04-15

### A.1 Line Counts
| File | Lines |
|---|---|
| `gui/components/dashboard.py` | 3331 |
| `gui/components/unified_browser_window.py` | 3238 |
| **Combined** | **6569** |

### A.2 Test Results
| Metric | Value |
|---|---|
| Collected | 996 |
| Passed | 994 |
| Failed | 2 |
| Skipped | 0 |
| pytest exit | 1 (due to pre-existing failures) |

### A.3 Pre-existing Failures (§3.3)

Both failures are in `gui/tests/test_database_access_protocol_writes.py`. Root cause: `http_servers` table missing `probe_host` column — schema migration gap unrelated to modularization.

```
FAILED test_database_access_protocol_writes.py::test_manual_upsert_inserts_smb_ftp_http_rows
  sqlite3.OperationalError: table http_servers has no column named probe_host

FAILED test_database_access_protocol_writes.py::test_manual_upsert_http_same_ip_different_ports_create_distinct_rows
  sqlite3.OperationalError: table http_servers has no column named probe_host
```

**Disposition:** Pre-existing, out of C1–C10 scope. Do not fix within modularization cards. These 2 failures are expected at every card baseline run; any additional failure is a regression introduced by that card.

### A.4 Import Smoke
`IMPORT SMOKE: PASS` — all frozen public symbols importable from canonical paths.

---

## Appendix B — Test File Inventory

### B.1 gui/tests/ (45 files)

| File | Description |
|---|---|
| `test_action_routing.py` | Action routing and protocol isolation tests (Card 5) |
| `test_app_config_dialog.py` | Regression tests for app config dialog validation popups |
| `test_app_config_dialog_clamav.py` | ClamAV config in AppConfigDialog |
| `test_app_config_dialog_dorks.py` | Discovery dork config behavior |
| `test_app_config_dialog_tmpfs.py` | tmpfs quarantine config behavior |
| `test_backend_error_parser_dependencies.py` | Backend error parser dependency checks |
| `test_backend_interface_commands.py` | Command-building regression tests for backend |
| `test_backend_progress_ftp.py` | FTP-style CLI output parsing |
| `test_backend_progress_http.py` | HTTP-style CLI output parsing |
| `test_batch_summary_dialog.py` | Shared batch summary dialog helpers |
| `test_browser_clamav.py` | Browser-download ClamAV integration |
| `test_clamav_results_dialog.py` | ClamAV results dialog presentation |
| `test_dashboard_api_key_gate.py` | Dashboard scan-start Shodan API key gate |
| `test_dashboard_bulk_ops.py` | Dashboard post-scan bulk operations |
| `test_dashboard_reddit_wiring.py` | Reddit Grab button wiring in DashboardWidget |
| `test_dashboard_runtime_status_lines.py` | Dashboard runtime status line composition |
| `test_dashboard_scan_dialog_wiring.py` | Dashboard quick-scan dialog callback wiring |
| `test_data_import_engine.py` | DataImportEngine timestamp normalization (Card 2.5) |
| `test_database_access_protocol_union.py` | DatabaseReader protocol UNION API |
| `test_database_access_protocol_writes.py` | DatabaseReader protocol-aware write helpers — **2 pre-existing failures** |
| `test_database_access_scan_cohort.py` | Protocol-agnostic immediate scan cohort selection |
| `test_db_path_sync_precedence.py` | Database path synchronization |
| `test_db_tools_dialog.py` | DBToolsDialog import-button state behavior |
| `test_db_tools_engine.py` | DBToolsEngine database management operations |
| `test_extract_runner_clamav.py` | ClamAV post-processor integration |
| `test_ftp_browser.py` | shared.ftp_browser.FtpNavigator |
| `test_ftp_browser_window.py` | FTP browser viewer integration |
| `test_ftp_probe.py` | FTP probe cache and runner |
| `test_ftp_scan_dialog.py` | FtpScanDialog and dashboard wiring |
| `test_http_browser_window.py` | HTTP browser viewer integration |
| `test_http_probe.py` | HTTP probe cache and runner |
| `test_messagebox_guardrail.py` | Guardrail: must use gui.utils.safe_messagebox |
| `test_probe_cache_dispatch.py` | probe_cache_dispatch.load_probe_result_for_host |
| `test_probe_runner_subdirectories.py` | SMB probe directory sampling behavior |
| `test_probe_snapshot_summary.py` | Probe snapshot summary helpers |
| `test_reddit_browser_window.py` | reddit_browser_window.py |
| `test_safe_messagebox.py` | gui.utils.safe_messagebox |
| `test_scan_manager_config_path.py` | ScanManager config-path propagation |
| `test_server_list_card4.py` | Row_key selection and per-row-field filter (Card 4) |
| `test_server_list_details_probe_section.py` | Server details probe-section rendering |
| `test_smb_browser_window.py` | _extract_smb_banner helper |
| `test_smb_virtual_root.py` | SMB virtual root UX (Card U6) |
| `test_theme_runtime_toggle.py` | Light/dark mode theme runtime toggle |
| `test_tmpfs_warning_dialog_schedule.py` | tmpfs startup warning dialog lifecycle |
| `test_unified_scan_dialog_validation.py` | Unified scan dialog max-results validation |

### B.2 shared/tests/ (33 files)

| File | Description |
|---|---|
| `test_access_auth_retry_and_failhard.py` | Auth retry and fail-hard behavior |
| `test_access_share_enumerator_pure_python.py` | Share enumerator pure Python |
| `test_access_share_tester_pure_python.py` | Share tester pure Python |
| `test_authenticated_host_selection.py` | Authenticated host selection |
| `test_clamav_scanner.py` | shared/clamav_scanner.py |
| `test_db_path_resolution.py` | Database path resolution |
| `test_discover_auth_fallback.py` | Auth fallback behavior |
| `test_discover_auth_pure_python.py` | Discovery auth pure Python |
| `test_discover_host_filter_metadata_only.py` | Host filter metadata-only |
| `test_discover_operation_metadata_exclusions.py` | Metadata exclusion tests in discovery |
| `test_discover_shodan_query_heartbeat.py` | Shodan query heartbeat |
| `test_ftp_config.py` | FTP-specific SMBSeekConfig getters |
| `test_ftp_operation.py` | FTP operation parallel paths |
| `test_ftp_state_tables.py` | ftp_user_flags and ftp_probe_cache migration (Card 1) |
| `test_http_endpoint_identity.py` | HTTP endpoint identity (ip + port) |
| `test_http_operation.py` | HTTP operation stage behavior |
| `test_http_query_config.py` | HTTP Shodan base-query configuration |
| `test_path_migration.py` | Path migration |
| `test_probe_gating.py` | Probe gating behavior |
| `test_quarantine_postprocess.py` | quarantine_postprocess.py |
| `test_quarantine_promotion.py` | quarantine_promotion.py |
| `test_redseek_client.py` | redseek/client.py |
| `test_redseek_explorer_bridge.py` | redseek/explorer_bridge.py |
| `test_redseek_parser.py` | redseek/parser.py |
| `test_redseek_service.py` | redseek/service.py |
| `test_redseek_store.py` | redseek/store.py (sidecar DB init, schema, CRUD, wipe) |
| `test_scan_session_metadata_defaults.py` | F5 scan session metadata transitions |
| `test_signature_loader_paths.py` | Signature loader paths |
| `test_smb_adapter_contract.py` | SMB adapter contract |
| `test_smb_parsing.py` | SMB parsing |
| `test_timestamp_canonicalization.py` | Timestamp canonicalization (Card 2.5) |
| `test_tmpfs_quarantine.py` | shared/tmpfs_quarantine.py |
| `test_verdict_conditions.py` | Verdict conditions |

---

## Change Log

| Card | Date | Change | AUTOMATED | MANUAL | OVERALL |
|---|---|---|---|---|---|
| C0 | 2026-04-15 | Contract freeze established; baseline recorded | PASS* | N/A | PASS* |
| C10 | 2026-04-15 | Final gate closeout: full validation suite A–E; Appendix C evidence recorded | PASS† | PENDING HI | PENDING HI |

*AUTOMATED gate: 994/996 passed; 2 pre-existing failures documented in §A.3; import smoke PASS.

†AUTOMATED gate: 1045/1047 passed; same 2 pre-existing failures only (see §A.3); compile smoke (A) PASS; import smoke (B) PASS — includes full §2a dashboard shim checks and §2c/§2d ubw attribute checks; line counts ↓98% (see §C.1).

---

## Appendix C — C10 Final Validation Results

**Date recorded:** 2026-04-15

### C.1 Line Counts (C10)

| File | Lines (C10) | Lines (C0 baseline) | Delta |
|---|---|---|---|
| `gui/components/dashboard.py` | 58 | 3331 | −3273 (−98%) |
| `gui/components/unified_browser_window.py` | 105 | 3238 | −3133 (−97%) |
| **Combined** | **163** | **6569** | **−6406 (−98%)** |

Both files are compatibility shims. Actual implementations: `gui/dashboard/widget.py` (DashboardWidget) and `gui/browsers/` package (browser classes).

### C.2 Test Results (C10)

| Metric | Value |
|---|---|
| Collected | 1047 |
| Passed | 1045 |
| Failed | 2 |
| Skipped | 0 |
| pytest exit | 1 (pre-existing failures only) |

Both failures are the same pre-existing `test_database_access_protocol_writes.py` failures documented in §A.3. No new failures introduced by C1–C10. Baseline grew from 996 collected (C0) to 1047 — net +51 tests added across C1–C9 gates.

### C.3 Coverage Snapshot (C10)

| Metric | Value |
|---|---|
| Total coverage | 54% |
| Statements | 37,748 |
| Missed | 17,350 |

First coverage capture — no C0 baseline to delta against. `pytest-cov` was not in `requirements.txt` at C0; installed temporarily into venv for C10 gate (not persisted to `requirements.txt`). The 54% reflects combined `shared/` + `gui/` packages excluding test files themselves. Notable low-coverage areas: `shared/workflow.py` (59%), `shared/tmpfs_quarantine.py` (62%), `shared/utils.py` (0% — dead code candidate).
