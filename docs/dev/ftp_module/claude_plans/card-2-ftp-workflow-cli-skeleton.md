# Plan: Card 2 — FTP Workflow + CLI Skeleton (Revised)

## What this plan does

Write `docs/dev/ftp_module/claude_plan/02-card2.md` — a complete, self-contained
implementation guide for the agent executing Card 2.

This revision addresses six correctness issues found during review:
1. Dashboard post-start flow must mirror `_start_new_scan()` exactly.
2. Method name fix: `_update_scan_button_state`, not `_set_scan_button_state`.
3. Thread safety: progress via `_update_progress()`, not direct `progress_callback()`.
4. Lifecycle: `_process_scan_results()` + `_cleanup_scan()` in finally, like `_scan_worker()`.
5. `_last_scan_country` doesn't exist — use `None` (global scan).
6. Success detection: add FTP success pattern to `progress.py` (minimal, targeted).

---

## 02-card2.md content (full)

---

# Card 2: FTP Workflow + CLI Skeleton — Implementation Guide

## Context

Card 1 (done) wired a `Start FTP Scan` button in `dashboard.py` to a placeholder
messagebox in `_start_ftp_scan_placeholder()`. Card 2 replaces that placeholder
with a real execution path:

- A new `ftpseek` CLI script (skeleton, no real FTP I/O yet)
- A new `FtpWorkflow` class that emits GUI-compatible progress output
- A `run_ftp_scan()` method on `BackendInterface`
- A `start_ftp_scan()` + `_ftp_scan_worker()` on `ScanManager`
- Dashboard wiring that replaces the placeholder, calling the same post-start
  sequence as SMB (`_reset_log_output` → `_update_scan_button_state` →
  `_show_scan_progress` → `_monitor_scan_completion`)
- A small addition to `progress.py` so FTP skeleton completion is parsed as success

The SMB path is never touched. The one-active-scan lock is shared and unchanged.

---

## Architecture

```
[Dashboard: Start FTP Scan]
        |
        v
dashboard._start_ftp_scan_placeholder()      ← replaces messagebox placeholder
   calls same post-start sequence as _start_new_scan()
        |
        v
scan_manager.start_ftp_scan()                ← new method, mirrors start_scan()
        |
        v
_ftp_scan_worker() [daemon thread]
   → backend_interface.run_ftp_scan()        ← new method on BackendInterface
   → _process_scan_results(result)           ← REUSE existing method
   → _cleanup_scan()  [finally]              ← REUSE existing method
        |
        v
ftpseek --verbose --country XX              ← new CLI script (skeleton)
        |
        v
FtpWorkflow.run()
   emits [1/2], [2/2] step headers
   emits 📊 Progress: x/y (p.p%) lines
   emits 📊 Hosts Scanned: 0
   emits 🔓 Hosts Accessible: 0
   emits 📁 Accessible Shares: 0
   emits 🎉 FTP scan completed successfully
        |
        v
progress.py parse_final_results()           ← recognises FTP success string (small addition)
```

Progress streams back through the existing `process_runner.execute_with_progress()`
and `progress.parse_output_stream()` — those files are otherwise unchanged.

---

## Files to Create

### 1. `ftpseek` (project root, executable)

```python
#!/usr/bin/env python3
"""FTP scan CLI entry point (Card 2 skeleton — no real FTP I/O yet)."""

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from shared.ftp_workflow import create_ftp_workflow


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ftpseek",
        description="FTP server discovery and assessment (MVP skeleton)",
    )
    parser.add_argument(
        "--country", metavar="CODE", default=None,
        help="ISO 3166-1 alpha-2 country code(s), comma-separated (e.g. US,GB)",
    )
    parser.add_argument(
        "--config", metavar="FILE", default=None,
        help="Path to config file (default: conf/config.json)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    parser.add_argument("--quiet", "-q", action="store_true", default=False)
    parser.add_argument("--no-colors", action="store_true", default=False)
    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.verbose and args.quiet:
        parser.error("--verbose and --quiet are mutually exclusive")

    try:
        workflow = create_ftp_workflow(args)
        workflow.run(args)
    except KeyboardInterrupt:
        print("\nScan interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

After writing, mark executable:
```bash
chmod +x ftpseek
```

---

### 2. `commands/ftp/__init__.py`

```python
"""FTP command package (Card 2 skeleton)."""
```

---

### 3. `commands/ftp/models.py`

```python
"""Data models for FTP scan results."""
from dataclasses import dataclass, field
from typing import List


@dataclass
class FtpScanResult:
    """Summary result returned by FtpWorkflow.run()."""
    country: str
    hosts_scanned: int = 0
    hosts_accessible: int = 0
    errors: List[str] = field(default_factory=list)
    success: bool = True
```

---

### 4. `commands/ftp/operation.py`

Emits `📊 Progress: x/y (p.p%)` lines matched by the existing regex in
`gui/utils/backend_interface/progress.py` (line 41).

```python
"""
FTP scan operation skeleton (Card 2).

Emits GUI-compatible progress lines; no real FTP I/O.
Real discovery/auth/listing added in Cards 4-5.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.ftp_workflow import FtpWorkflow

_SKELETON_STEPS = 10


def run_discover_stage(workflow: "FtpWorkflow") -> int:
    """Placeholder discovery. Returns 0 candidates (skeleton)."""
    out = workflow.output
    out.info("FTP discovery stage (skeleton — no Shodan query yet)")
    for i in range(1, _SKELETON_STEPS + 1):
        pct = (i / _SKELETON_STEPS) * 100
        out.raw(f"📊 Progress: {i}/{_SKELETON_STEPS} ({pct:.1f}%)")
        time.sleep(0.05)
    out.info("Discovery complete: 0 FTP candidates (skeleton)")
    return 0


def run_access_stage(workflow: "FtpWorkflow", candidate_count: int) -> int:
    """Placeholder access/auth stage. Returns 0 accessible (skeleton)."""
    out = workflow.output
    total = max(candidate_count, _SKELETON_STEPS)
    out.info("FTP access verification stage (skeleton — no real auth yet)")
    for i in range(1, total + 1):
        pct = (i / total) * 100
        out.raw(f"📊 Progress: {i}/{total} ({pct:.1f}%)")
        time.sleep(0.05)
    out.info("Access verification complete: 0 accessible FTP hosts (skeleton)")
    return 0
```

---

### 5. `shared/ftp_workflow.py`

Key detail: the rollup lines match `parse_final_results()` field regexes so the
dashboard shows `0/0` rather than garbage. The final success line is picked up by
the new FTP pattern added to `progress.py` (see modification §6 below).

```python
"""
FTP scan workflow orchestrator (Card 2 skeleton).

Completely separate from shared/workflow.py — no changes to SMB workflow.
"""
from __future__ import annotations

import argparse
import sys


class _FtpOutput:
    """Minimal stdout wrapper with flush=True for subprocess pipe streaming."""

    def __init__(self, verbose: bool = False, no_colors: bool = False) -> None:
        self.verbose = verbose
        self.no_colors = no_colors

    def info(self, msg: str) -> None:
        print(f"ℹ  {msg}", flush=True)

    def success(self, msg: str) -> None:
        print(f"✓  {msg}", flush=True)

    def error(self, msg: str) -> None:
        print(f"✗  {msg}", file=sys.stderr, flush=True)

    def raw(self, msg: str) -> None:
        """Emit verbatim — used for 📊 Progress lines."""
        print(msg, flush=True)

    def workflow_step(self, name: str, num: int, total: int) -> None:
        """[n/m] header — matched by progress.py workflow_step_pattern."""
        print(f"[{num}/{total}] {name}", flush=True)


class FtpWorkflow:
    """FTP scan workflow skeleton."""

    STEP_COUNT = 2

    def __init__(self, output: _FtpOutput) -> None:
        self.output = output

    def run(self, args: argparse.Namespace) -> None:
        country = getattr(args, "country", None) or "ALL"
        out = self.output

        out.info(f"FTP scan starting — country filter: {country}")

        out.workflow_step("FTP Discovery", 1, self.STEP_COUNT)
        from commands.ftp.operation import run_discover_stage
        candidates = run_discover_stage(self)

        out.workflow_step("FTP Access Verification", 2, self.STEP_COUNT)
        from commands.ftp.operation import run_access_stage
        accessible = run_access_stage(self, candidates)

        # Rollup lines — field regexes in parse_final_results() will parse these.
        out.raw(f"📊 Hosts Scanned: {candidates}")
        out.raw(f"🔓 Hosts Accessible: {accessible}")
        out.raw(f"📁 Accessible Shares: 0")

        # Success line — must match the pattern added to parse_final_results() below.
        out.raw("🎉 FTP scan completed successfully")


def create_ftp_workflow(args: argparse.Namespace) -> FtpWorkflow:
    """Factory mirroring create_unified_workflow() in shared/workflow.py."""
    output = _FtpOutput(
        verbose=getattr(args, "verbose", False),
        no_colors=getattr(args, "no_colors", False),
    )
    return FtpWorkflow(output)
```

---

## Files to Modify

### 6. `gui/utils/backend_interface/progress.py`

**Why:** `parse_final_results()` currently only recognises SMB-specific success
strings. Without this change, the FTP skeleton always returns `success=False`,
causing the dashboard to display "Scan failed" even on a clean exit.

**Change:** The file uses a direct `if` condition at line 566, not a list.
Add one `or` clause to the existing condition:

Current (lines 566–569):
```python
if ("🎉 SMBSeek security assessment completed successfully!" in cleaned_output or
    ("✓ Found" in cleaned_output and "accessible SMB servers" in cleaned_output) or
    "✓ Discovery completed:" in cleaned_output):
    results["success"] = True
```

Replace with (one new `or` clause appended before the closing paren):
```python
if ("🎉 SMBSeek security assessment completed successfully!" in cleaned_output or
    ("✓ Found" in cleaned_output and "accessible SMB servers" in cleaned_output) or
    "✓ Discovery completed:" in cleaned_output or
    "🎉 FTP scan completed successfully" in cleaned_output):
    results["success"] = True
```

The three existing clauses are untouched.

---

### 7. `gui/utils/backend_interface/interface.py`

**Changes are additive only.** Three additions:

**A. In `__init__`, immediately after the line setting `self.cli_script`:**
```python
self.ftp_cli_script = self.backend_path / "ftpseek"
```

**B. New method `_build_ftp_cli_command()` — add after `_build_tool_command()` (~line 165):**
```python
def _build_ftp_cli_command(self, *args) -> List[str]:
    """Build CLI command for ftpseek using same interpreter as GUI."""
    interpreter = sys.executable or "python3"
    command_list = [interpreter, str(self.ftp_cli_script), *args]
    if os.getenv("XSMBSEEK_DEBUG_SUBPROCESS"):
        _logger.debug(
            "FTP CLI command: %s with %d args", str(self.ftp_cli_script), len(args)
        )
    return command_list
```

**C. New method `run_ftp_scan()` — add after `run_scan()` (~line 322):**
```python
def run_ftp_scan(
    self,
    countries: List[str],
    progress_callback: Optional[Callable] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    verbose: bool = True,
) -> Dict:
    """
    Execute FTP scan workflow via ftpseek CLI subprocess.

    Args:
        countries: ISO country codes (empty list = global scan).
        progress_callback: Called with (percentage, message) during scan.
        log_callback: Called with raw stdout lines for log streaming.
        verbose: Pass --verbose for parseable progress output.

    Returns:
        Result dict with 'success' key and parsed summary stats.
    """
    if self.mock_mode:
        return mock_operations.mock_ftp_scan_operation(countries, progress_callback)

    cmd = self._build_ftp_cli_command()
    if verbose:
        cmd.append("--verbose")
    if countries:
        cmd.extend(["--country", ",".join(countries)])

    return process_runner.execute_with_progress(
        self,
        cmd,
        progress_callback,
        log_callback=log_callback,
    )
```

**Note:** No config override needed — the FTP skeleton reads no config.
Config integration is deferred to Card 3.

---

### 8. `gui/utils/backend_interface/mock_operations.py`

Add `mock_ftp_scan_operation()` so `--mock` mode works when FTP button is clicked.
Add it after the last existing `mock_*` function:

```python
def mock_ftp_scan_operation(
    countries: List[str],
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """Mock FTP scan for --mock mode and tests."""
    steps = [
        (5.0,   "Starting FTP scan (mock)"),
        (15.0,  "[1/2] FTP Discovery"),
        (30.0,  "📊 Progress: 3/10 (30.0%)"),
        (50.0,  "📊 Progress: 5/10 (50.0%)"),
        (70.0,  "📊 Progress: 7/10 (70.0%)"),
        (80.0,  "[2/2] FTP Access Verification"),
        (90.0,  "📊 Progress: 9/10 (90.0%)"),
        (100.0, "🎉 FTP scan completed successfully"),
    ]
    for pct, msg in steps:
        if progress_callback:
            progress_callback(pct, msg)
        time.sleep(0.3)

    return {
        "success": True,
        "hosts_scanned": 0,
        "hosts_accessible": 0,
        "accessible_shares": 0,
    }
```

---

### 9. `gui/utils/scan_manager.py`

Two changes: (A) a targeted guard in `_process_scan_results()` and (B) two new
methods. **No other existing methods are touched.**

**A. Guard DB fallback against FTP scans — `_process_scan_results()` line 636.**

The existing condition triggers a DB stats fallback whenever `success=True` and
all counts are 0. For FTP scans this would pull SMB dashboard stats and
contaminate the FTP result.

Current (line 636):
```python
if results.get("success", False) and not results.get("error") and hosts_scanned == 0 and accessible_hosts == 0 and shares_found == 0:
```

Replace with (add protocol guard at the end):
```python
if (results.get("success", False) and not results.get("error")
        and hosts_scanned == 0 and accessible_hosts == 0 and shares_found == 0
        and self.scan_results.get("protocol") != "ftp"):
```

`self.scan_results["protocol"]` is set to `"ftp"` by `start_ftp_scan()` before
the worker runs, so the guard is always set when it matters.

**B. New methods — add after the last existing method.**

Add `start_ftp_scan()` and `_ftp_scan_worker()`. **Do not modify `start_scan()`,
`_scan_worker()`, or any other existing method.**

The worker reuses `_process_scan_results()` and `_cleanup_scan()` exactly like
`_scan_worker()` does. Progress updates go through `_update_progress()` (which
handles thread-safe dispatch via `ui_dispatcher`).

```python
def start_ftp_scan(
    self,
    scan_options: dict,
    backend_path: str,
    progress_callback: Callable,
    log_callback: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Start an FTP scan in a background thread.

    Shares the same lock/state mechanism as start_scan() so only one
    protocol scan can run at a time. SMB behaviour is unchanged.

    Args:
        scan_options: Dict with optional 'country' key.
        backend_path: Path to SMBSeek installation directory.
        progress_callback: Called with (percentage, status, phase).
        log_callback: Called with raw stdout lines for log streaming.

    Returns:
        True if scan started, False if already scanning or lock failed.
    """
    if self.is_scan_active():
        return False

    country = scan_options.get("country")
    if not self.create_lock_file(country, "ftp"):
        return False

    try:
        self.backend_interface = BackendInterface(backend_path)
        self.is_scanning = True
        self.scan_start_time = datetime.now()
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.scan_results = {
            "start_time": self.scan_start_time.isoformat(),
            "country": country,
            "scan_options": scan_options,
            "status": "running",
            "protocol": "ftp",
        }

        self.scan_thread = threading.Thread(
            target=self._ftp_scan_worker,
            args=(scan_options,),
            daemon=True,
        )
        self.scan_thread.start()
        return True

    except Exception as exc:
        self.is_scanning = False
        self.remove_lock_file()
        self._update_progress(0, f"Failed to start FTP scan: {exc}", "error")
        return False


def _ftp_scan_worker(self, scan_options: dict) -> None:
    """
    Worker thread for FTP scan execution.

    Mirrors _scan_worker() structure exactly:
    - try: execute + process_scan_results
    - except: _handle_scan_error
    - finally: _cleanup_scan (always)

    Progress goes through _update_progress() for thread-safe UI dispatch.
    """
    try:
        country_raw = scan_options.get("country") or ""
        countries = [c.strip() for c in country_raw.split(",") if c.strip()]

        self._update_progress(5, "Initializing FTP scan...", "initialization")

        result = self.backend_interface.run_ftp_scan(
            countries=countries,
            progress_callback=self._handle_backend_progress,
            log_callback=self._handle_backend_log_line,
            verbose=True,
        )

        self._process_scan_results(result)

    except Exception as exc:
        self._handle_scan_error(exc)

    finally:
        self._cleanup_scan()
```

---

### 10. `gui/components/dashboard.py`

**Replace `_start_ftp_scan_placeholder()` only.** Add `_build_ftp_scan_options()`
alongside it. Do not touch any other method.

The post-start sequence **must mirror `_start_new_scan()` lines 779–791 exactly**:
`_reset_log_output` → `_update_scan_button_state("scanning")` →
`_show_scan_progress` → `_monitor_scan_completion`.

Replace the existing placeholder (lines 1820–1827):

```python
def _start_ftp_scan_placeholder(self) -> None:
    """Launch FTP scan via scan manager (Card 2+)."""
    scan_options = self._build_ftp_scan_options()
    backend_path = str(self.backend_interface.backend_path)

    started = self.scan_manager.start_ftp_scan(
        scan_options=scan_options,
        backend_path=backend_path,
        progress_callback=self._handle_scan_progress,
        log_callback=self._handle_scan_log_line,
    )

    if started:
        # Mirror _start_new_scan() post-start sequence exactly.
        self.current_scan_options = scan_options
        self._reset_log_output(scan_options.get("country"))
        self._update_scan_button_state("scanning")
        self._show_scan_progress(scan_options.get("country"))
        self._monitor_scan_completion()
    else:
        messagebox.showerror(
            "FTP Scan Error",
            "Could not start FTP scan.\n"
            "A scan may already be running.",
            parent=self.parent,
        )


def _build_ftp_scan_options(self) -> dict:
    """
    Build FTP scan options from current dashboard state.

    Card 2: Global scan (country=None). A proper FTP scan dialog
    with country selection is a Card 5 follow-up.
    """
    return {"country": None}
```

---

## SMB Regression Safety

Files **not modified at all** in Card 2:
- `smbseek`
- `shared/workflow.py`
- `commands/discover/` (all files)
- `commands/access/` (all files)
- `gui/utils/backend_interface/process_runner.py`
- `gui/utils/backend_interface/config.py`
- `gui/utils/backend_interface/error_parser.py`

`progress.py` gets a one-line addition inside an existing list — the SMB patterns
are untouched; the parser is purely additive.

Changes to `interface.py`, `mock_operations.py`, and `dashboard.py` are
**additive only** — new methods/attributes appended.

`scan_manager.py` has one targeted line edit: the DB fallback condition in
`_process_scan_results()` gets a protocol guard added. All other method bodies
are unchanged. `progress.py` has one targeted `or` clause added to the existing
success `if` condition — three existing clauses untouched.

The scan lock is shared: if an SMB scan is running, `start_ftp_scan()` hits
`is_scan_active()` and returns `False` immediately. Vice versa holds.

---

## Verification Steps

### 1. Compile check
```bash
python3 -m py_compile ftpseek
python3 -m py_compile commands/ftp/__init__.py
python3 -m py_compile commands/ftp/models.py
python3 -m py_compile commands/ftp/operation.py
python3 -m py_compile shared/ftp_workflow.py
python3 -m py_compile gui/utils/backend_interface/progress.py
python3 -m py_compile gui/utils/backend_interface/interface.py
python3 -m py_compile gui/utils/backend_interface/mock_operations.py
python3 -m py_compile gui/utils/scan_manager.py
python3 -m py_compile gui/components/dashboard.py
```

### 2. CLI smoke test
```bash
source venv/bin/activate
python3 ftpseek --help
python3 ftpseek --verbose
python3 ftpseek --verbose --country US
```
Expected: prints `[1/2]`, `[2/2]` step headers, `📊 Progress:` lines, rollup
summary, `🎉 FTP scan completed successfully`, exits 0.

### 3. Progress format spot-check
```bash
python3 ftpseek --verbose 2>&1 | grep "📊 Progress:"
```
Expected: lines matching `📊 Progress: N/10 (P.P%)`.

### 4. Success line spot-check
```bash
python3 ftpseek --verbose 2>&1 | grep "🎉"
```
Expected: `🎉 FTP scan completed successfully`.

### 5. Mock mode GUI test
```bash
xvfb-run -a ./xsmbseek --mock
```
- Click "Start FTP Scan": progress should stream, dashboard returns to idle state,
  results dialog shows (0 hosts, success). Confirm `_monitor_scan_completion()`
  fires (button re-enables).
- Click "Start SMB Scan": must behave exactly as before.

### 6. Lock contention test (manual)
Start an SMB scan; while running, click "Start FTP Scan".
Expected: error dialog "A scan may already be running."

### 7. Existing test suite
```bash
xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v
```
No new failures expected.

---

## What Is NOT Tested Manually in Card 2

- Real FTP discovery (no Shodan query, no port 21 connect)
- FTP authentication (no anonymous login logic)
- FTP directory listing
- FTP database persistence (no ftp_* tables yet — Card 3)
- Country selection in FTP scan dialog (Card 5)

---

## Open Risks / Follow-ups for Card 3

1. **`_build_ftp_scan_options()` always passes `country=None`.**
   This means every FTP scan is a global scan in Card 2. A proper FTP scan
   dialog (with country, max_results, etc.) is deferred to Card 5.

2. **`_handle_scan_progress` and `_show_scan_results` are SMB-labelled.**
   The dashboard progress text and results dialog say "SMB" generically.
   Card 5 should make these protocol-aware when FTP results are shown.

3. **`process_runner.py` timeout:** Uses `config.get("operations_timeout")`,
   which is SMB-config-driven. FTP scans with real I/O (Card 4+) may need
   a separate FTP timeout key.

4. **`validate_backend()` in `config.py`** checks `os.X_OK` on `cli_script`.
   The FTP interface does not call `validate_backend()` yet, but the
   implementing agent must still `chmod +x ftpseek` at creation time so Card 3
   can add that validation without surprises.

5. **`_process_scan_results` DB fallback** is guarded in Card 2 (protocol guard
   at line 636). SMB fallback behaviour is unchanged — guard only fires when
   `self.scan_results["protocol"] == "ftp"`. No Card 3 follow-up needed here.

---

## Ordered Implementation Steps

1. Create `commands/ftp/__init__.py`
2. Create `commands/ftp/models.py`
3. Create `commands/ftp/operation.py`
4. Create `shared/ftp_workflow.py`
5. Create `ftpseek`; run `chmod +x ftpseek`
6. Edit `gui/utils/backend_interface/progress.py` — add FTP `or` clause to success `if` at line 566
7. Edit `gui/utils/backend_interface/interface.py` — add `ftp_cli_script`, `_build_ftp_cli_command()`, `run_ftp_scan()`
8. Edit `gui/utils/backend_interface/mock_operations.py` — add `mock_ftp_scan_operation()`
9. Edit `gui/utils/scan_manager.py` — (A) add protocol guard to `_process_scan_results()` line 636; (B) add `start_ftp_scan()`, `_ftp_scan_worker()`
10. Edit `gui/components/dashboard.py` — replace `_start_ftp_scan_placeholder()`, add `_build_ftp_scan_options()`
11. Run all `py_compile` checks (step 1 of Verification)
12. Run CLI smoke tests (steps 2–4)
13. Run existing test suite (step 7)
14. Write this guide to `docs/dev/ftp_module/claude_plan/02-card2.md`
