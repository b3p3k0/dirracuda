# C0 — Contract Freeze + Baseline (Plan Only)

**Card:** C0 from `docs/dev/modularization_15Apr/TASK_CARDS.md`  
**Date:** 2026-04-15  
**Status:** PLAN ONLY — docs-only file creation allowed; no code changes, no commits

---

## Key Constraints (pre-work summary)

1. Root-cause fixes only — no symptom suppression or workarounds
2. Legacy compatibility (DB migrations, data contracts) is first-class, not optional
3. Runtime-state guards required — no structural DB assumptions at import time
4. No performance regressions on UI/hot paths (scan progress loop, browser file list rendering)
5. Type coercion/validation logic must be explicit and safe — semantics preserved exactly
6. One card at a time — no bulk moves; stop if diff grows beyond plan scope
7. No commits without explicit HI go-ahead
8. All moved symbols must have compatibility shims at original module paths
9. Monkeypatch dotted-string paths and module-attribute patches are frozen breaking-change boundaries
10. "Done" = AUTOMATED PASS + MANUAL PASS — never just one gate closed

---

## Issue

The two largest modules (`dashboard.py` ~3331 lines, `unified_browser_window.py` ~3238 lines) need to be modularized across C1–C10. Before any code moves, this C0 card must freeze the observable contracts so that every subsequent card has a clear regression target.

## Root Cause

Without an explicit contract snapshot, any import moved or symbol renamed during C1–C10 can silently break callers or invalidate monkeypatch paths in tests — regressions that may not surface until a later card or during manual HI validation.

---

## Plan

### Output 1 — Public Import/API Contract Inventory

#### 1a. `gui.components.dashboard.DashboardWidget`

**Canonical import path:** `from gui.components.dashboard import DashboardWidget`

**Production callers (all non-test files):**
| File | Lines | Usage |
|---|---|---|
| `dirracuda` (main entry script) | 400 (import), 786 (instantiation) | `self.dashboard = DashboardWidget(root, db_reader, backend_interface, config_path)` |
| `gui/main.py` | 31 (import) | Module-level import for app bootstrap |

**Test callers (9 test files):**
- `gui/tests/test_dashboard_runtime_status_lines.py`
- `gui/tests/test_dashboard_scan_dialog_wiring.py`
- `gui/tests/test_ftp_scan_dialog.py`
- `gui/tests/test_theme_runtime_toggle.py`
- `gui/tests/test_clamav_results_dialog.py`
- `gui/tests/test_dashboard_bulk_ops.py`
- `gui/tests/test_dashboard_reddit_wiring.py`
- `gui/tests/test_extract_runner_clamav.py`
- `gui/tests/test_dashboard_api_key_gate.py`

**Constructor (frozen signature):**
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

**Public attributes (frozen — set in `__init__`, referenced externally):**
`parent`, `db_reader`, `backend_interface`, `config_path`, `scan_manager`, `settings_manager`, `theme`, `current_scan`, `current_scan_options`, `status_text`, `clamav_status_text`, `tmpfs_status_text`

---

#### 1b. `gui.components.unified_browser_window` — Public Symbols

**Canonical import path:** `from gui.components.unified_browser_window import ...`  
Also imported as module alias: `import gui.components.unified_browser_window as ubw`

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
| `FtpBrowserWindow` | Subclass — tests instantiate directly (`test_ftp_browser_window.py`) |
| `HttpBrowserWindow` | Subclass — tests instantiate directly (`test_http_browser_window.py`) |
| `SmbBrowserWindow` | Subclass — tests import and instantiate directly |

**Private helpers — compatibility-exported (must remain importable from this module):**
| Symbol | Reason to keep in shim |
|---|---|
| `_extract_smb_banner` | `test_smb_browser_window.py` imports it directly from this module |
| `_coerce_bool` | Defined here (line 93), used internally at 10+ call sites |
| `_format_file_size` | Defined here (line 78), used internally at 6+ call sites |

Note: `_coerce_int` does NOT exist in `unified_browser_window.py` — removed from contract.

**Module-level names required for dotted-string and `ubw.*` attribute patches (see Output 2c/2d):**
`threading`, `queue`, `tk`, `ttk`, `messagebox`, `open_file_viewer`, `open_image_viewer`,
`show_clamav_results_dialog`, `FtpBrowserWindow`, `HttpBrowserWindow`

Note: `tk.*` and `ttk.*` sub-members are patched as dotted paths (e.g. `unified_browser_window.tk.Toplevel`),
so `tk` and `ttk` must be imported at module scope in any shim — not inside functions.

---

### Output 2 — Patch-Sensitive Test Inventory

All `monkeypatch.setattr` paths that are **frozen** — must remain valid at these exact paths after any card in C1–C10.

#### 2a. Dashboard dotted-string patches (frozen — must survive C6–C9):
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
**Risk:** When `dashboard.py` becomes a shim (C9), all these names must be importable at the `gui.components.dashboard` module level. The shim must import `messagebox`, `threading`, `tk`, `ttk`, `extract_runner`, `dispatch_probe_run`, `probe_patterns`, `get_probe_snapshot_path_for_host`, `show_unified_scan_dialog`, `show_reddit_grab_dialog`, `run_ingest`, `create_quarantine_dir` at module level — not hidden inside functions.

#### 2b. Dashboard instance-level patches (via `dash` object — survive C6–C9 by constructor contract):
```
dash._prompt_for_shodan_api_key   (method on DashboardWidget instance)
dash._check_external_scans        (method on DashboardWidget instance)
dash.indicator_patterns            (attribute on instance)
dash.db_reader                     (attribute on instance)
```

#### 2c. Browser dotted-string patches (frozen — must survive C2–C5):
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
**Risk:** When `unified_browser_window.py` becomes a shim (C2–C5), all these names must be importable at the module level via dotted-string path. The shim must import `threading`, `messagebox`, `queue`, `tk`, `ttk` at module level AND define or re-export `open_file_viewer`, `open_image_viewer`, `show_clamav_results_dialog`, `FtpBrowserWindow`, `HttpBrowserWindow` as real callable names — not lazy references.

#### 2d. Browser module-attribute patches via `ubw` alias (must survive C2–C5):
These use `monkeypatch.setattr(ubw, "symbol_name", ...)` where `ubw` is the imported module.
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
ubw.tk            (tkinter reference)
ubw.ttk           (tkinter.ttk reference)
ubw.messagebox    (showinfo / showwarning / showerror)
ubw.show_clamav_results_dialog
```

#### 2e. Related module patches (NOT in dashboard or browser — do not change in C1–C10):
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

### Output 3 — Baseline Automated Command Set (for C1–C10)

**Critical:** All validation commands must preserve pytest's exit code.
- `pytest | tee file; echo $?` — WRONG: `$?` is tee's exit code, not pytest's
- `pytest | tee file; echo ${PIPESTATUS[0]}` — works only within the same pipeline subshell; fragile across compound statements
- Redirect approach (used throughout this document — most portable, no ambiguity): redirect stdout+stderr to file, capture `$?` immediately, display separately

**Canonical pattern used throughout this document:**
```bash
./venv/bin/python -m pytest [args] > /tmp/out.txt 2>&1; RESULT=$?
cat /tmp/out.txt
echo "pytest exit=${RESULT}"   # 0 = PASS, non-zero = FAIL
```

---

Run these before AND after every card. All must exit 0 before proceeding.

```bash
# 1. Full test suite
./venv/bin/python -m pytest --tb=short -q > /tmp/c0_full.txt 2>&1; RESULT=$?
cat /tmp/c0_full.txt
echo "pytest exit=${RESULT}"

# 2. Coverage snapshot (record before C1, compare in C10)
./venv/bin/python -m pytest --cov=shared --cov=gui --cov-report=term-missing -q > /tmp/c0_coverage.txt 2>&1; RESULT=$?
cat /tmp/c0_coverage.txt
echo "pytest exit=${RESULT}"

# 3. Dashboard contract tests
./venv/bin/python -m pytest \
  gui/tests/test_dashboard_runtime_status_lines.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_dashboard_api_key_gate.py \
  gui/tests/test_dashboard_bulk_ops.py \
  gui/tests/test_dashboard_reddit_wiring.py \
  -v > /tmp/c0_dash.txt 2>&1; RESULT=$?
cat /tmp/c0_dash.txt
echo "pytest exit=${RESULT}"

# 4. Browser contract tests
./venv/bin/python -m pytest \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_smb_virtual_root.py \
  gui/tests/test_browser_clamav.py \
  gui/tests/test_action_routing.py \
  -v > /tmp/c0_browser.txt 2>&1; RESULT=$?
cat /tmp/c0_browser.txt
echo "pytest exit=${RESULT}"

# 5. Messagebox guardrail
./venv/bin/python -m pytest gui/tests/test_messagebox_guardrail.py -v > /tmp/c0_guard.txt 2>&1; RESULT=$?
cat /tmp/c0_guard.txt
echo "pytest exit=${RESULT}"

# 6. Import smoke (verify all frozen public symbols importable)
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

# 7. Line count snapshot (record before C1, compare at C10)
wc -l gui/components/dashboard.py gui/components/unified_browser_window.py
```

**Card-specific additions (in addition to full baseline above):**
| Card | Additional command |
|---|---|
| C1 | `./venv/bin/python -m pytest gui/tests/ shared/tests/ -k "coerce or filesize" -v` + import `gui.utils.coercion`, `gui.utils.filesize` |
| C2–C5 | `python -c "import gui.browsers; print('OK')"` — verify package importable |
| C6–C8 | Re-run dashboard contract tests only; do NOT test `import gui.dashboard` (package not yet created) |
| C9 | `python -c "import gui.dashboard; print('OK')"` — first card that creates the package |
| C10 | Full baseline + coverage delta + line count comparison before/after |

---

### Output 4 — Proposed `BASELINE_CONTRACTS.md` Structure

**File location:** `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md`

```markdown
# Baseline Contracts

## Purpose
Frozen snapshot of public APIs and patch-sensitive symbols as of C0.
Regression target for C1–C10. Any card that breaks these contracts is a blocker.

## 1. DashboardWidget Public Contract
### 1.1 Canonical module path
### 1.2 Production callers (file:line — all non-test files)
### 1.3 Test callers (file:line)
### 1.4 Constructor signature
### 1.5 Public methods (table: name | signature)
### 1.6 Public attributes (table: name | type | set in __init__)
### 1.7 Dotted-string monkeypatch paths (must survive C9 shim)
### 1.8 Instance-level monkeypatch targets (survive via constructor contract)

## 2. unified_browser_window Public Contract
### 2.1 Canonical module path
### 2.2 Production callers (file:line — all non-test files)
### 2.3 Test callers (file:line — symbols imported per file)
### 2.4 Frozen public functions (table: name | signature)
### 2.5 Frozen public classes (table: name | base | test files that import it)
### 2.6 Compatibility-exported private helpers (name | reason to keep)
### 2.7 Module-level names required for ubw.* attribute patches
### 2.8 Dotted-string monkeypatch paths (must survive C5 shim)

## 3. Baseline Test Inventory
### 3.1 gui/tests/ — complete file list with one-line descriptions (44 files)
### 3.2 shared/tests/ — complete file list with one-line descriptions (33 files)
### 3.3 Known pre-existing failures at baseline (if any — must enumerate before C1)

## 4. Baseline Metrics
### 4.1 Line counts: dashboard.py, unified_browser_window.py, combined total
### 4.2 Test count: collected / passed / failed / skipped (from baseline run)
### 4.3 Coverage snapshot: gui%, shared%

## 5. Validation Command Set
(Exact copy of Output 3 — commands for pre/post card validation with correct exit-code handling)

## 6. Change Log
| Card | Date | Change | AUTOMATED | MANUAL | OVERALL |
|---|---|---|---|---|---|
| C0 | 2026-04-15 | Contract freeze established | PASS | N/A | PASS |
```

---

### Output 5 — Top Risks + Mitigation Mapping

| Risk ID | Risk | Card Exposure | Mitigation |
|---|---|---|---|
| R1 | Import breakage — moved classes/functions break dotted import paths or `ubw.*` attribute patches | C2–C9 | Preserve original module as shim; re-export ALL frozen symbols including module-level names (`threading`, `messagebox`, `tk`, `ttk`, `queue`); import smoke test run after every card |
| R2 | Behavior drift — scan lifecycle call expectations change subtly during dashboard extraction | C6–C9 | Run dashboard contract tests before/after each card; HI manual gate on scan start/stop/progress for C7 |
| R3 | Legacy/data contract regressions — startup DB assumptions break on pre-migration DB | C1 (coercion), C6–C9 | No schema changes unless scoped; runtime-state guards; legacy smoke check (open app with old DB) required for C9 |
| R4 | Known failures accumulating | C1–C10 | Document all pre-existing failures in BASELINE_CONTRACTS.md §3.3 before C1; require PASS/FAIL evidence per card |
| R5 | Performance regression — extra indirection from extraction adds latency | C3–C9 | Keep shim calls lightweight; no extra polling; scan progress loop is hot path — validate in HI gate |
| R6 | Type coercion drift — `_coerce_bool` consolidated in C1 changes truthiness semantics | C1 | Focused coercion unit tests; preserve current accepted value set and bounds; test against edge inputs before replacing call sites |
| R7 | Oversized card scope — large extraction hides root causes | C3–C9 | Stop if diff grows beyond plan scope; prefer mechanical copy + shim before behavior changes |
| R8 | Tooling/execution blockers — headless display required for GUI tests; gitignore hides files | C1–C10 | Use `xvfb-run -a ./venv/bin/python -m pytest gui/tests/`; use `git add -f` for docs/dev/ files; report blockers with exact unblock commands |
| R9 | Shim re-export incomplete — a `ubw.*` module attribute missing from shim breaks test patches | C2–C9 | Import smoke test explicitly checks `ubw.threading` and `ubw.messagebox`; full attribute list in §2.7 of BASELINE_CONTRACTS.md |
| R10 | Test command false-pass — piping pytest through any filter returns the filter's exit code, not pytest's | C0–C10 | All validation commands redirect to file (`> /tmp/x.txt 2>&1; RESULT=$?`) and report `${RESULT}` explicitly — never use `\| tail` or `\| grep` as the final pipe stage for pass/fail gates |

---

### Output 6 — PASS/FAIL Gate Definitions per Card

**Gate conventions:**
- **AUTOMATED PASS** = full baseline command set exits 0 + import smoke PASS
- **AUTOMATED FAIL** = any test regression OR import smoke failure OR pytest exit ≠ 0
- **MANUAL gate** = HI verifies runtime behavior in live app
- **OVERALL** = both AUTOMATED and MANUAL (where required) must be PASS

| Card | AUTOMATED PASS criteria | AUTOMATED FAIL criteria | MANUAL required? | MANUAL PASS criteria |
|---|---|---|---|---|
| C0 | `BASELINE_CONTRACTS.md` written; baseline test run recorded with exact counts; line counts documented | Any test that was passing before is now failing | No | n/a |
| C1 | Full suite passes; `gui.utils.coercion` and `gui.utils.filesize` importable; all call sites in dashboard and browser updated; import smoke passes | Any test regression; coercion semantics changed (edge cases differ) | No | n/a |
| C2 | `gui.browsers` package importable; `gui.components.unified_browser_window` shim still imports all frozen symbols; browser contract tests pass; import smoke passes | Shim missing any frozen name; browser test regression | No | n/a |
| C3 | `from gui.browsers.core import UnifiedBrowserCore` succeeds; shim re-exports it; all browser contract tests pass; import smoke passes | Either path breaks; test regression | No | n/a |
| C4 | `gui.browsers.ftp_browser` and `gui.browsers.http_browser` importable; `FtpBrowserWindow`/`HttpBrowserWindow` accessible from both new and shim paths; browser contract tests pass | Any path breaks; test regression | Yes | Open FTP and HTTP browser windows in live app; verify file listing, navigation, download |
| C5 | `gui.browsers.smb_browser` and `gui.browsers.factory` importable; shim exports all frozen browser symbols including `ubw.*` names; all browser contract tests pass | Any path breaks; test regression | Yes | Open SMB browser; verify share listing, navigation, file access |
| C6 | `dashboard_status.py` (or equivalent) exists; status composition accessible; dashboard contract tests pass; import smoke passes; NO `import gui.dashboard` check (package not yet created) | Dashboard test regression; import smoke fails | No | n/a |
| C7 | `dashboard_scan.py` exists; `start_scan_progress`, `update_scan_progress`, `finish_scan_progress` still callable on `DashboardWidget` instance; dashboard contract tests pass; NO `import gui.dashboard` check | Scan lifecycle test regression; scan method missing from instance | Yes | Launch SMB scan from GUI; verify progress updates display, scan completes, results appear in server list |
| C8 | `dashboard_batch_ops.py` exists; `test_dashboard_bulk_ops.py` passes; dashboard contract tests pass; NO `import gui.dashboard` check | Bulk ops test regression | Yes | Trigger bulk extract from server list; verify ClamAV integration and results dialog |
| C9 | `gui.dashboard` package importable; `gui.components.dashboard` shim re-exports `DashboardWidget` and all dotted-string patch names at module level; all dashboard contract tests pass; import smoke passes | Shim incomplete — any dashboard test regresses; `dirracuda` (main script) fails to start | Yes | Full app startup: scan, view results, open browser, all dashboard controls functional; open app with pre-migration DB and verify startup succeeds |
| C10 | Full suite passes (same or better count vs. baseline); line counts reduced in target files vs. baseline; coverage delta documented | Net test regression vs. baseline; line count not reduced | Yes | Full manual regression: SMB scan, FTP scan, HTTP scan, server list, browser, extract, ClamAV, Reddit grab |

---

### Output 7 — Explicit Assumptions and Blockers

#### Assumptions
1. `gui/tests/` and `docs/dev/` may be gitignored — use `git add -f` when staging these paths.
2. GUI tests require a display — headless CI must use `xvfb-run -a ./venv/bin/python -m pytest gui/tests/`. Baseline test captures GUI and shared tests separately if no display available.
3. Baseline test run has zero pre-existing failures OR all pre-existing failures are documented in BASELINE_CONTRACTS.md §3.3 before C1 begins. C1 must not start until §3.3 is complete.
4. `_extract_smb_banner` is compatibility-exported from `gui.components.unified_browser_window` (not "internal" — test imports it directly). Any C2–C5 shim must re-export it.
5. `_coerce_int` does NOT exist in `unified_browser_window.py` — it is defined locally in scan dialogs and tmpfs utilities. C1 scope covers `_coerce_bool` and `_format_file_size` consolidation; `_coerce_int` may be scoped separately or excluded.
6. The `dirracuda` main entry script (no `.py` extension) is a production caller — shims in C9 must not break the import at line 400 or the instantiation at line 786.
7. Module-level names `threading`, `messagebox`, `tk`, `ttk`, `queue` must remain importable from `gui.components.unified_browser_window` after any shim conversion in C2–C5 — these are used as `ubw.threading`, `ubw.messagebox` etc. in attribute-level monkeypatches.

#### Blockers (must resolve before C1 starts)
| ID | Blocker | Resolution |
|---|---|---|
| B1 | Baseline test run not yet recorded | Run: `./venv/bin/python -m pytest --tb=short -q > /tmp/c0_baseline.txt 2>&1; RESULT=$?; cat /tmp/c0_baseline.txt; echo "pytest exit=${RESULT}"` — capture exact output and confirm exit code |
| B2 | xvfb-run may be absent (blocks GUI test baseline in headless env) | Run: `which xvfb-run` — if absent, document GUI tests as "manual-env only" in BASELINE_CONTRACTS.md |
| B3 | Line counts not yet recorded | Run: `wc -l gui/components/dashboard.py gui/components/unified_browser_window.py` |
| B4 | Full `ubw.*` module-attribute patch list not fully verified from grep output | Before C2 starts: read `gui/tests/test_action_routing.py`, `test_browser_clamav.py`, `test_ftp_browser_window.py`, `test_http_browser_window.py` and extract every `monkeypatch.setattr(ubw, ...)` call — update BASELINE_CONTRACTS.md §2.7 with confirmed complete list |

---

## Files to Change (C0 only)

| Action | File | Notes |
|---|---|---|
| CREATE | `docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md` | Docs-only — no code changes |

No other files. No imports moved. No module restructuring. No code edits.

---

## Validation Plan

C0 is complete (AUTOMATED PASS) when ALL of the following are true:

1. `BASELINE_CONTRACTS.md` exists at the specified path with all 6 sections populated
2. Baseline test run output is recorded in §4.2 (exact count, exit code confirmed 0)
3. Any pre-existing failures documented in §3.3 (or §3.3 explicitly states "none")
4. Baseline line counts recorded in §4.1
5. Import smoke test exits 0 with `IMPORT SMOKE: PASS`

**Exact commands to confirm C0 PASS:**
```bash
# 1. File exists
test -f docs/dev/modularization_15Apr/BASELINE_CONTRACTS.md && echo "FILE: OK" || echo "FILE: MISSING"

# 2. Baseline test run (exit code must be 0 or all failures documented in §3.3)
./venv/bin/python -m pytest --tb=short -q > /tmp/c0_baseline.txt 2>&1; RESULT=$?
cat /tmp/c0_baseline.txt
echo "pytest exit=${RESULT}"

# 3. Line counts
wc -l gui/components/dashboard.py gui/components/unified_browser_window.py

# 4. Import smoke
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

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Baseline run reveals undocumented pre-existing failures | Medium | High — must document before C1 | Record all in §3.3; do not fix in C0 |
| `ubw.*` attribute patch list incomplete (B4) | Medium | High — incomplete shim breaks tests in C2–C5 | Explicit blocker B4; read test files before C2 |
| GUI tests fail in headless env at baseline | Medium | Medium — blocks GUI baseline capture | Document limitation; separate GUI/shared baseline runs |

---

## Assumptions

See Output 7 above (7 explicit assumptions). Key additions from corrections:
- `_extract_smb_banner` is COMPATIBILITY-EXPORTED — not "internal do-not-expose"
- `_coerce_int` is NOT in unified_browser_window.py — removed from frozen contract
- `import gui.dashboard` check belongs only in C9+ gates — not C6/C7/C8

---

## HI Test Needed?

**No** — C0 is documentation creation only. No runtime behavior is changed.

If the baseline test run (B1) reveals unexpected failures, HI decides:
- Pre-existing (document in §3.3 and proceed) — or
- Must-fix before C1 (treat as blocker, pause C1)
