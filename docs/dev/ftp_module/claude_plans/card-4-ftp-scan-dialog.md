# Plan: FTP Pre-Launch Configuration Dialog

## Context
The FTP scan button launches a global scan immediately without any configuration step. The SMB button opens a `ScanDialog` first. This plan adds an equivalent `FtpScanDialog`, wires it into the dashboard, and extends `_ftp_scan_worker` to apply runtime config overrides from the dialog — all without touching `ftpseek` CLI args or parser-sensitive output lines.

**Environment:** `/home/kevin/DEV/smbseek-smb`, `./venv/bin/python` everywhere.

---

## Files to Modify / Create

| Action | File |
|--------|------|
| **NEW** | `gui/components/ftp_scan_dialog.py` |
| **MODIFY** | `gui/components/dashboard.py` |
| **MODIFY** | `gui/utils/scan_manager.py` |
| **NEW** | `gui/tests/test_ftp_scan_dialog.py` |

`interface.py` — no changes needed; `run_ftp_scan()` already accepts `verbose` as a parameter.

---

## 1. `gui/components/ftp_scan_dialog.py` (new)

Model after `scan_dialog.py` (ScanDialog + `show_scan_dialog()` at lines 2054–2075).

### Constructor
```python
class FtpScanDialog:
    def __init__(self, parent, config_path, scan_start_callback,
                 settings_manager=None):
```

### Region map
Import and reuse `ScanDialog.REGIONS` (class constant defined at `scan_dialog.py:47`) — do **not** fork the country lists:
```python
from gui.components.scan_dialog import ScanDialog
REGIONS = ScanDialog.REGIONS  # module-level alias for use in FtpScanDialog
```

### Dialog fields

| Field | Widget | Default |
|-------|--------|---------|
| Country codes (manual entry) | Entry | `""` |
| Region checkboxes | Checkbuttons (from `REGIONS`) | all unchecked |
| Max Shodan results | Spinbox | `1000` |
| API key override | Entry (`show="*"`) | `""` (optional) |
| Discovery concurrency | Spinbox | `10` |
| Access concurrency | Spinbox | `4` |
| Connect timeout (s) | Spinbox | `5` |
| Auth timeout (s) | Spinbox | `10` |
| Listing timeout (s) | Spinbox | `15` |
| Verbose | Checkbutton | **`False`** (matches SMB/FTP UX parity) |

**Custom Shodan filter** — **dropped from this card**. Requires proper CLI plumbing in `ftpseek` (`--filter` flag + query builder update). Deferred to next card.

### Country validation
Replicate SMB's `_parse_and_validate_countries()` logic exactly: dedupe, sort, enforce max-country guard. Return `None` (not `""`) when no country/region is selected (global scan).

### Dialog defaults
Load concurrency and timeout defaults from effective config via `load_config(config_path)` at dialog init time. Fall back to hardcoded values (10/4/5/10/15) only if config key is absent.

```python
ftp_cfg = load_config(config_path).get("ftp", {})
default_disc = ftp_cfg.get("discovery", {}).get("max_concurrent_hosts", 10)
default_acc  = ftp_cfg.get("access",    {}).get("max_concurrent_hosts", 4)
verif = ftp_cfg.get("verification", {})
default_connect  = verif.get("connect_timeout",  5)
default_auth     = verif.get("auth_timeout",     10)
default_listing  = verif.get("listing_timeout",  15)
```

### `_build_scan_options()` — key names match SMB conventions

```python
{
    "country":                        Optional[str], # None = global scan; comma-sep codes otherwise
    "max_shodan_results":             int,
    "api_key_override":               Optional[str], # None if blank
    "discovery_max_concurrent_hosts": int,
    "access_max_concurrent_hosts":    int,
    "connect_timeout":                int,
    "auth_timeout":                   int,
    "listing_timeout":                int,
    "verbose":                        bool,
}
```

### `show_ftp_scan_dialog(parent, config_path, scan_start_callback, settings_manager=None)`
Opens dialog modally; calls `scan_start_callback(scan_options)` on Start, no-op on Cancel.

---

## 2. `gui/components/dashboard.py` (modify)

### Changes

1. **Add import:**
   ```python
   from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
   ```

2. **Replace `_handle_ftp_scan_button_click()`** (lines 1821–1827):
   Instead of calling `_start_ftp_scan_placeholder()`, call `show_ftp_scan_dialog(...)` with `scan_start_callback=self._start_ftp_scan`. Pass `self.parent`, `self.config_path`, `self.settings_manager`.

3. **Add `_start_ftp_scan(self, scan_options: dict)`** — mirrors `_start_new_scan()` (lines 745–791):
   - Call `_check_external_scans()` first (race-check, same as SMB `_start_new_scan` pattern — see line 745)
   - If `scan_button_state != "idle"` after that check, bail
   - Call `self.scan_manager.start_ftp_scan(scan_options=scan_options, backend_path=..., progress_callback=..., log_callback=...)`
   - On success: store `self.current_scan_options`, call `_reset_log_output()`, `_update_scan_button_state("scanning")`, `_show_scan_progress()`, `_monitor_scan_completion()`
   - On failure: show `messagebox.showerror`

4. **Remove** `_start_ftp_scan_placeholder()` and `_build_ftp_scan_options()` — both replaced by dialog + callback.

---

## 3. `gui/utils/scan_manager.py` (modify)

### Extend `_ftp_scan_worker()` (lines 919–950)

Build `config_overrides` from `scan_options` and wrap execution in `_temporary_config_override`, mirroring the SMB worker (lines 318–362). Key name alignment with SMB conventions:

```python
config_overrides = {}

# Shodan API key (shared global path, same as SMB)
api_key = scan_options.get("api_key_override")
if api_key:
    config_overrides["shodan"] = {"api_key": api_key}

# FTP Shodan query limits
max_results = scan_options.get("max_shodan_results")
if max_results is not None:
    config_overrides.setdefault("ftp", {}).setdefault("shodan", {}) \
        .setdefault("query_limits", {})["max_results"] = max_results

# FTP concurrency (key names match SMB: discovery_max_concurrent_hosts)
disc_conc = scan_options.get("discovery_max_concurrent_hosts")
if disc_conc is not None:
    config_overrides.setdefault("ftp", {}).setdefault("discovery", {}) \
        ["max_concurrent_hosts"] = disc_conc

acc_conc = scan_options.get("access_max_concurrent_hosts")
if acc_conc is not None:
    config_overrides.setdefault("ftp", {}).setdefault("access", {}) \
        ["max_concurrent_hosts"] = acc_conc

# FTP timeouts
ftp_verification = {}
for key in ("connect_timeout", "auth_timeout", "listing_timeout"):
    val = scan_options.get(key)
    if val is not None:
        ftp_verification[key] = val
if ftp_verification:
    config_overrides.setdefault("ftp", {})["verification"] = ftp_verification

# Verbose (default False for UX parity)
verbose = scan_options.get("verbose", False)

# Execute
if config_overrides:
    with self.backend_interface._temporary_config_override(config_overrides):
        result = self.backend_interface.run_ftp_scan(
            countries=countries,
            progress_callback=self._handle_backend_progress,
            log_callback=self._handle_backend_log_line,
            verbose=verbose,
        )
else:
    result = self.backend_interface.run_ftp_scan(
        countries=countries,
        progress_callback=self._handle_backend_progress,
        log_callback=self._handle_backend_log_line,
        verbose=verbose,
    )
```

---

## 4. `gui/tests/test_ftp_scan_dialog.py` (new)

Tests use `xvfb-run`-compatible patterns (same as existing FTP tests). Where full Tk init is unavoidable, patch `tkinter.Toplevel`.

| Test | What it checks |
|------|---------------|
| `test_dialog_scan_options_keys` | `_build_scan_options()` returns all 9 expected keys with correct types |
| `test_country_passed_through` | Entry set to "US,GB" → `country="US,GB"` |
| `test_defaults` | Default values match spec (verbose=False, discovery=10, access=4, timeouts 5/10/15) |
| `test_optional_api_key_empty` | Blank api_key → `api_key_override=None` |
| `test_show_dialog_cancel_no_callback` | Cancel flow → `scan_start_callback` NOT called |
| `test_scan_manager_ftp_overrides` | `_ftp_scan_worker` with full scan_options: assert `run_ftp_scan` is called inside `_temporary_config_override` context (mock backend_interface) |
| `test_dashboard_ftp_button_opens_dialog` | Dashboard FTP button click → `show_ftp_scan_dialog` is called (not immediate launch); patch show_ftp_scan_dialog and assert called |
| `test_start_ftp_scan_bails_on_race` | `_start_ftp_scan()` calls `_check_external_scans()` first; if that flips state away from idle, `scan_manager.start_ftp_scan` is NOT called (parity with `_start_new_scan` race guard) |

---

## Config Override Path Summary

| scan_options key | Config path overridden |
|---|---|
| `api_key_override` | `config["shodan"]["api_key"]` |
| `max_shodan_results` | `config["ftp"]["shodan"]["query_limits"]["max_results"]` |
| `discovery_max_concurrent_hosts` | `config["ftp"]["discovery"]["max_concurrent_hosts"]` |
| `access_max_concurrent_hosts` | `config["ftp"]["access"]["max_concurrent_hosts"]` |
| `connect_timeout` | `config["ftp"]["verification"]["connect_timeout"]` |
| `auth_timeout` | `config["ftp"]["verification"]["auth_timeout"]` |
| `listing_timeout` | `config["ftp"]["verification"]["listing_timeout"]` |

**Custom Shodan filter** — deferred to next card (requires `--filter` flag in `ftpseek` + FTP Shodan query builder update).

---

## Invariants / Constraints

- One-scan-at-a-time lock unchanged — `_check_external_scans()` + `is_scan_active()` guard covers both SMB and FTP.
- No changes to `ftpseek`, `shared/ftp_workflow.py`, or `commands/ftp/operation.py` — parser-sensitive output lines untouched.
- SMB `ScanDialog` and `_start_new_scan()` left entirely unchanged.

---

## Verification

```bash
# 1. Baseline before changes
./venv/bin/python -m pytest gui/tests/ shared/tests/ -q

# 2. After implementation — full regression
./venv/bin/python -m pytest gui/tests/ shared/tests/ -q

# 3. Targeted FTP dialog tests
./venv/bin/python -m pytest gui/tests/test_ftp_scan_dialog.py -v

# 4. Manual smoke test (mock mode)
./xsmbseek --mock
# Click "📡 Start FTP Scan" → FtpScanDialog opens → configure → Start → scan proceeds
```
