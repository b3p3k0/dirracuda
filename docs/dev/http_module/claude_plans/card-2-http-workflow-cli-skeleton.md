# Card 2: HTTP Workflow + CLI Skeleton — Implementation Plan

## Context

Card 1 added the HTTP scan dialog (`http_scan_dialog.py`) and a placeholder
`_start_http_scan()` in `dashboard.py` that shows an info box instead of
starting a scan. Card 2 wires a real (skeleton) execution path end-to-end:
CLI entrypoint → workflow → GUI scan manager → dashboard launch. No real HTTP
verification, no DB writes — those belong to Cards 3–4. The goal is a
launchable, progress-streaming, cleanly completing HTTP scan skeleton that
proves the full pipeline works and preserves SMB/FTP behaviour unchanged.

---

## Files to Create

### 1. `httpseek` (root, executable)
Copy of `ftpseek` with FTP references swapped to HTTP:
- `prog="httpseek"`, description "HTTP server discovery and assessment"
- Imports `create_http_workflow` from `shared.http_workflow` and `HttpDiscoveryError` from `commands.http.models`
- Same arg set as ftpseek: `--country`, `--config`, `--filter`, `--verbose`, `--quiet`, `--no-colors`
- Same migration guard block
- `workflow = create_http_workflow(args); workflow.run(args)`
- Must be executable: `chmod +x httpseek` after creation

### 2. `shared/http_workflow.py`
Copy of `shared/ftp_workflow.py` with FTP → HTTP substitutions:
```python
class HttpWorkflow:
    STEP_COUNT = 2

    def run(self, args):
        # step 1: "HTTP Discovery"
        from commands.http.operation import run_discover_stage
        candidates, shodan_total = run_discover_stage(self)
        # step 2: "HTTP Access Verification"
        from commands.http.operation import run_access_stage
        accessible = run_access_stage(self, candidates)
        directories_found = int(getattr(self, "last_accessible_directory_count", 0))

        out.raw(f"📊 Hosts Scanned: {shodan_total}")
        out.raw(f"🔓 Hosts Accessible: {accessible}")
        out.raw(f"📁 Accessible Directories: {directories_found}")
        out.raw("🎉 HTTP scan completed successfully")   # ← parsed by progress.py

def create_http_workflow(args) -> HttpWorkflow:
    # factory identical to create_ftp_workflow()
```

### 3. `commands/http/__init__.py`
Empty package init.

### 4. `commands/http/models.py`
```python
class HttpDiscoveryError(Exception):
    """Raised by Shodan query on API failure. Caught at CLI boundary."""
```
(Stub dataclasses `HttpCandidate`, `HttpScanResult` optional; can be bare stubs.)

### 5. `commands/http/operation.py`
Skeleton stubs — no real HTTP verification yet:
```python
def run_discover_stage(workflow) -> tuple[list, int]:
    """Card 4 will implement real Shodan query + HTTP verification."""
    workflow.output.info("HTTP Discovery: skeleton mode (no Shodan query yet)")
    return [], 0

def run_access_stage(workflow, candidates) -> int:
    """Card 4 will implement real HTTP(S) access checking."""
    workflow.output.info("HTTP Access Verification: skeleton mode")
    workflow.last_accessible_directory_count = 0
    return 0
```

---

## Files to Modify

### 6. `gui/utils/backend_interface/interface.py`

**In `__init__` after `self.ftp_cli_script` line (~line 60):**
```python
self.http_cli_script = self.backend_path / "httpseek"
```

**New method `_build_http_cli_command()` after `_build_ftp_cli_command()` (~line 155):**
```python
def _build_http_cli_command(self, *args) -> List[str]:
    """Build CLI command for httpseek using same interpreter as GUI."""
    interpreter = sys.executable or "python3"
    cli_args = [str(arg) for arg in args]
    if "--config" not in cli_args:
        cli_args.extend(["--config", str(self.config_path)])
    command_list = [interpreter, str(self.http_cli_script), *cli_args]
    if os.getenv("XSMBSEEK_DEBUG_SUBPROCESS"):
        _logger.debug("CLI command: %s with %d args", str(self.http_cli_script), len(cli_args))
    return command_list
```

**New method `run_http_scan()` after `run_ftp_scan()` (~line 378):**
```python
def run_http_scan(
    self,
    countries: List[str],
    progress_callback=None,
    log_callback=None,
    filters: str = None,
    verbose: bool = True,
) -> Dict:
    """Execute HTTP scan workflow via httpseek CLI subprocess."""
    if self.mock_mode:
        return mock_operations.mock_http_scan_operation(countries, progress_callback)

    cmd = self._build_http_cli_command()
    if verbose:
        cmd.append("--verbose")
    if countries:
        cmd.extend(["--country", ",".join(countries)])
    if filters:
        cmd.extend(["--filter", filters])

    return process_runner.execute_with_progress(
        self, cmd, progress_callback, log_callback=log_callback
    )
```

### 7. `gui/utils/backend_interface/mock_operations.py`

**New function `mock_http_scan_operation()` after `mock_ftp_scan_operation()`:**
```python
def mock_http_scan_operation(
    countries: List[str],
    progress_callback=None,
) -> Dict[str, Any]:
    """Mock HTTP scan for --mock mode and tests."""
    steps = [
        (5.0,   "Starting HTTP scan (mock)"),
        (15.0,  "[1/2] HTTP Discovery"),
        (30.0,  "📊 Progress: 3/10 (30.0%)"),
        (50.0,  "📊 Progress: 5/10 (50.0%)"),
        (70.0,  "📊 Progress: 7/10 (70.0%)"),
        (80.0,  "[2/2] HTTP Access Verification"),
        (90.0,  "📊 Progress: 9/10 (90.0%)"),
        (100.0, "🎉 HTTP scan completed successfully"),
    ]
    for pct, msg in steps:
        if progress_callback:
            progress_callback(pct, msg)
        time.sleep(0.3)
    return {"success": True, "hosts_scanned": 0, "hosts_accessible": 0, "accessible_shares": 0}
```

### 8. `gui/utils/scan_manager.py`

**New method `start_http_scan()` after `start_ftp_scan()` (~line 925):**
Mirror of `start_ftp_scan()` with:
- `create_lock_file(country, "http")`
- `"protocol": "http"` in scan_results
- `target=self._http_scan_worker`
- Error message: `"Failed to start HTTP scan: {exc}"`

**New method `_http_scan_worker()` after `_ftp_scan_worker()`:**
Mirror of `_ftp_scan_worker()` with HTTP config key paths:
```python
# Shodan API key (same global path as SMB/FTP)
if api_key:
    config_overrides["shodan"] = {"api_key": api_key}

# HTTP Shodan query limits
if max_results is not None:
    config_overrides.setdefault("http", {}).setdefault("shodan", {}).setdefault("query_limits", {})["max_results"] = max_results

# HTTP discovery concurrency
if disc_conc is not None:
    config_overrides.setdefault("http", {}).setdefault("discovery", {})["max_concurrent_hosts"] = disc_conc

# HTTP verification timeouts (no auth_timeout — HTTP has no auth step)
verif_overrides = {}
for key in ("connect_timeout", "request_timeout"):
    val = scan_options.get(key)
    if val is not None:
        verif_overrides[key] = val
if verif_overrides:
    config_overrides.setdefault("http", {})["verification"] = verif_overrides

# TLS / verification flags (pass-through; no behavior in Card 2)
for key in ("verify_http", "verify_https", "allow_insecure_tls"):
    val = scan_options.get(key)
    if val is not None:
        config_overrides.setdefault("http", {}).setdefault("verification", {})[key] = val

# Bulk probe (pass-through only)
bulk = scan_options.get("bulk_probe_enabled")
if bulk is not None:
    config_overrides.setdefault("http", {})["bulk_probe_enabled"] = bulk
```
Then calls `self.backend_interface.run_http_scan(...)` with same signature as `run_ftp_scan`.

### 9. `gui/components/dashboard.py`

Replace `_start_http_scan()` placeholder body (~lines 2006–2021):
```python
def _start_http_scan(self, scan_options: dict) -> None:
    """Start HTTP scan with options from dialog. Mirrors _start_ftp_scan()."""
    self._check_external_scans()
    if self.scan_button_state != "idle":
        return

    backend_path_obj = getattr(self.backend_interface, "backend_path", None)
    backend_path = str(backend_path_obj) if backend_path_obj else "."

    started = self.scan_manager.start_http_scan(
        scan_options=scan_options,
        backend_path=backend_path,
        progress_callback=self._handle_scan_progress,
        log_callback=self._handle_scan_log_line,
        config_path=self.config_path,
    )

    if started:
        self.current_scan_options = scan_options
        self._reset_log_output(scan_options.get("country"))
        self._update_scan_button_state("scanning")
        self._show_scan_progress(scan_options.get("country"))
        self._monitor_scan_completion()
    else:
        messagebox.showerror(
            "HTTP Scan Error",
            "Could not start HTTP scan.\n"
            "A scan may already be running.",
            parent=self.parent,
        )
```

### 10. `gui/utils/backend_interface/progress.py`

In `parse_final_results()`, add HTTP success marker to the success-detection block (~line 566):
```python
if ("🎉 SMBSeek security assessment completed successfully!" in cleaned_output or
    ("✓ Found" in cleaned_output and "accessible SMB servers" in cleaned_output) or
    "✓ Discovery completed:" in cleaned_output or
    "🎉 FTP scan completed successfully" in cleaned_output or
    "🎉 HTTP scan completed successfully" in cleaned_output):   # ← add this line
    results["success"] = True
```

---

## Assumptions

1. `conf/config.json` has no `http` key yet — that's fine. The skeleton workflow
   doesn't read any HTTP config keys. Config overrides are additive in memory
   only (via `_temporary_config_override`), so no schema errors occur.
2. The `httpseek` script will live in the repo root alongside `ftpseek` and `smbseek`.
3. No migration guard needed in Card 2 (no DB writes). The guard block in `httpseek`
   is kept for consistency but does nothing harmful — same pattern as `ftpseek`.
4. No new tests are added in Card 2 (test coverage is Card 6 scope), but existing
   tests must not regress.

---

## Verification

### Automated regression gate
```bash
cd /home/kevin/DEV/smbseek-smb
source venv/bin/activate
set -o pipefail && xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short | tail -30
```
Expected: all previously passing tests still pass (no regressions).
`pipefail` ensures a non-zero pytest exit code propagates through the pipe.

### Automated HTTP smoke assertions
```bash
source venv/bin/activate

# 1) Help must succeed
python httpseek --help >/dev/null

# 2) Skeleton run must succeed
output="$(python httpseek --country US --verbose 2>&1)"
status=$?
echo "$output"
test "$status" -eq 0

# 3) Success marker must be present
echo "$output" | grep -q "🎉 HTTP scan completed successfully"
```
Each command exits non-zero on failure — no silent false-greens.
All three must pass before the card is considered done.

### Manual GUI check
1. `./xsmbseek` → click HTTP scan button → fill dialog → Start
2. Verify: scan button changes to "scanning", log shows HTTP Discovery step,
   then HTTP Access Verification step, then scan completes cleanly
3. Verify: no error dialogs; scan button returns to idle
4. Verify: FTP scan button still works (no regressions)
5. Verify: SMB scan button still works (no regressions)

### Mock mode check
```bash
./xsmbseek --mock
```
Launch HTTP scan → progress bar animates through mock steps → completes.

---

## Changed Files Summary

| File | Action | Rationale |
|------|--------|-----------|
| `httpseek` | Create | HTTP CLI entrypoint; mirrors `ftpseek` |
| `shared/http_workflow.py` | Create | Skeleton workflow; emits GUI-parseable output |
| `commands/http/__init__.py` | Create | Package init |
| `commands/http/models.py` | Create | `HttpDiscoveryError`; stubs for Card 4 |
| `commands/http/operation.py` | Create | Skeleton stubs for `run_discover_stage`, `run_access_stage` |
| `gui/utils/backend_interface/interface.py` | Modify | Add `http_cli_script`, `_build_http_cli_command()`, `run_http_scan()` |
| `gui/utils/backend_interface/mock_operations.py` | Modify | Add `mock_http_scan_operation()` |
| `gui/utils/scan_manager.py` | Modify | Add `start_http_scan()`, `_http_scan_worker()` |
| `gui/components/dashboard.py` | Modify | Replace placeholder with real `scan_manager.start_http_scan()` call |
| `gui/utils/backend_interface/progress.py` | Modify | Add HTTP success marker to `parse_final_results()` |
