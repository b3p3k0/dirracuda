# Plan: Card C1 — ClamAV Backend Adapter

## Context

Dirracuda bulk extract writes files to quarantine but runs no antivirus checks. C1 adds `shared/clamav_scanner.py`, a pure subprocess adapter with no GUI coupling. It is the foundation all later cards (C2–C6) depend on. Nothing in the existing codebase changes in C1.

---

## Issue

No reusable scanner abstraction exists; backend/tool selection and result parsing would otherwise be duplicated across C3 (extract integration) and any future call site.

## Root cause

Feature is net-new. ClamAV is not referenced anywhere in the current codebase.

---

## Runtime Call Sites (Confirmed)

C1 itself adds no callers. These are the sites that will consume the adapter in later cards:

| Card | File | Line | Role |
|------|------|------|------|
| C3 | `gui/utils/extract_runner.py` | post-download loop | in-scope Phase 1 |
| C3 | `gui/components/dashboard.py` | ~2348, calls `run_extract()` | in-scope Phase 1 |
| C3 | `gui/components/server_list_window/actions/batch.py` | ~461, calls `run_extract()` | in-scope Phase 1 |
| out-of-scope | `gui/components/server_list_window/details.py` | ~1065 | Phase 1 exclusion |

The adapter must carry no knowledge of these callers.

---

## Adapter API

### Public surface

```python
# shared/clamav_scanner.py
from __future__ import annotations  # required for Python 3.8 union-type syntax in annotations

@dataclass
class ScanResult:
    verdict: str                # "clean" | "infected" | "error"
    backend_used: Optional[str] # "clamdscan" | "clamscan" | None (when no binary found)
    signature: Optional[str]    # virus name when infected; None otherwise
    exit_code: Optional[int]    # raw process exit code; None on launch failure
    raw_output: str             # combined stdout+stderr for logging
    error: Optional[str]        # human-readable error reason when verdict=="error"

class ClamAVScanner:
    def __init__(
        self,
        backend: str = "auto",           # "auto" | "clamdscan" | "clamscan"
        clamscan_path: str = "clamscan",
        clamdscan_path: str = "clamdscan",
        timeout_seconds: int = 60,
    ): ...

    def scan_file(self, path: Union[str, Path]) -> ScanResult: ...
```

`from __future__ import annotations` defers annotation evaluation and allows `Optional`/`Union` shorthands without runtime cost. `Optional[str]` and `Union[str, Path]` are used in the body for 3.8 compatibility; `str | Path` syntax in annotations is safe only at 3.10+.

### Factory helper (for callers that pass a config dict directly)

```python
def scanner_from_config(cfg: dict) -> ClamAVScanner:
    """Build ClamAVScanner from the 'clamav' config section dict."""
```

### Private methods (implementation detail, not API)

- `_resolve_binary() -> Optional[Tuple[str, str]]` — returns `(resolved_path, backend_name)` or `None` if not found. `backend_name` is always one of the string literals `"clamdscan"` or `"clamscan"`, never a filesystem path — this is what gets written to `ScanResult.backend_used`.
- `_invoke(binary_path: str, backend_name: str, path: str) -> ScanResult` — takes both the resolved filesystem path and the canonical label; populates `ScanResult.backend_used` from `backend_name` unconditionally, so the label is never derived from a path string.
- `_parse_output(combined_output: str, exit_code: int, backend_name: str) -> ScanResult` — maps exit code + combined stdout/stderr to ScanResult. Accepts the already-combined string (caller does `stdout + "\n" + stderr`) so signature regex runs over both streams without the parser needing to know where each came from.

---

## Command Invocation Strategy

| Backend | Command | Notes |
|---------|---------|-------|
| `clamdscan` | `clamdscan --no-summary <path>` | requires clamd daemon running |
| `clamscan` | `clamscan --no-summary <path>` | slower; self-contained |

- **Always use list form** — never `shell=True`.
- Use `subprocess.Popen` (not `subprocess.run`) so the process handle is available for `kill()` on timeout.
- Capture both streams: `stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True`.
- Drive with `proc.communicate(timeout=self.timeout_seconds)`.

### `auto` resolution order

```
shutil.which(clamdscan_path) → use clamdscan
  else shutil.which(clamscan_path) → use clamscan
  else → ScanResult(verdict="error", error="no scanner binary found")
```

For explicit `backend="clamdscan"` or `"clamscan"`: if `shutil.which()` returns None, return `ScanResult(verdict="error", error="scanner not found: <name>")` — never raise.

For any other `backend` value (e.g. `"foo"`): return `ScanResult(verdict="error", backend_used=None, error="invalid backend: {backend}")` immediately, before any binary resolution. Do not fall through to `auto` behavior.

---

## Exit-Code / Result Mapping

| Condition | verdict | notes |
|-----------|---------|-------|
| exit 0 | `"clean"` | no threat found |
| exit 1 | `"infected"` | parse signature from stdout line matching `<path>: <NAME> FOUND` |
| exit 2 | `"error"` | scanner internal error; capture stderr |
| `subprocess.TimeoutExpired` | `"error"` | `error="scanner timeout: {N}s"`; kill process |
| `FileNotFoundError` on launch | `"error"` | `error="scanner not found: {binary_path}"` |
| `OSError` (non-FileNotFoundError) on launch | `"error"` | `error="failed to launch scanner: {str(e)}"` — covers PermissionError, exec-format errors, etc. |
| any other `Exception` | `"error"` | `error="unexpected: {str(e)}"` |

Signature parsing (exit 1 only):
```
combined = stdout + "\n" + stderr   # search both; some ClamAV packagings write FOUND to stderr
regex: r"^.+:\s+(.+)\s+FOUND$"     (applied per line of combined)
```
Take the first matching line; set `signature=None` if no match (defensive). `raw_output` always stores the full combined string for operator diagnostics.

---

## Timeout + Missing-Binary Fail-Open Behavior

The adapter itself **never raises**. All failure modes return `ScanResult(verdict="error", ...)`.

Callers in C3+ are responsible for checking `result.verdict == "error"` and applying the `fail_open` policy (leave file in quarantine, log reason, continue). The adapter has no opinion on fail-open; it is the caller's concern.

Process cleanup on timeout — `Popen` pattern (correct):
```python
# _resolve_binary() returns (resolved_path, backend_name) e.g. ("/usr/bin/clamdscan", "clamdscan")
resolved = self._resolve_binary()
if resolved is None:
    return ScanResult(verdict="error", backend_used=None, error="no scanner binary found", ...)

binary_path, backend_name = resolved   # backend_name is the label written to ScanResult.backend_used

proc = subprocess.Popen(
    [binary_path, "--no-summary", str(path)],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, errors="replace",        # errors="replace" guards against unexpected byte output
)
try:
    stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.communicate()  # drain to prevent deadlock
    return ScanResult(verdict="error", backend_used=backend_name,
                      error=f"scanner timeout: {self.timeout_seconds}s", ...)
```

Why `Popen` and not `subprocess.run`: `subprocess.run()` raises `TimeoutExpired` before returning, so no process object is available in the `except` block. `Popen` + `communicate(timeout=N)` is the documented pattern for timeout-with-kill (Python docs §subprocess.Popen.communicate).

`errors="replace"` (item 5): some ClamAV packaging produces non-UTF-8 bytes in scanner output on certain locales; `replace` converts them to `\ufffd` rather than raising a `UnicodeDecodeError`.

---

## Files to Change (C1 Only)

| Action | File | Notes |
|--------|------|-------|
| **New** | `shared/clamav_scanner.py` | adapter implementation |
| **New** | `shared/tests/test_clamav_scanner.py` | unit tests, no real ClamAV needed |

No other files are touched in C1.

---

## Test Plan (C1 only)

All tests monkeypatch `subprocess.Popen` — no real ClamAV binary required.

### Test cases

| Test | Scenario |
|------|----------|
| `test_auto_prefers_clamdscan_when_available` | both binaries present → verdict=="clean", `backend_used=="clamdscan"` |
| `test_auto_falls_back_to_clamscan` | clamdscan absent, clamscan present → verdict=="clean", `backend_used=="clamscan"` |
| `test_auto_no_binary_returns_error` | both absent → verdict=="error", `backend_used is None`, error contains "no scanner" |
| `test_explicit_clamdscan_present` | backend="clamdscan", binary present → `backend_used=="clamdscan"` |
| `test_explicit_clamdscan_missing_returns_error` | backend="clamdscan", binary absent → verdict=="error", `backend_used is None` |
| `test_explicit_clamscan_present` | backend="clamscan", binary present → `backend_used=="clamscan"` |
| `test_explicit_clamscan_missing_returns_error` | backend="clamscan", binary absent → verdict=="error", `backend_used is None` |
| `test_invalid_backend_returns_error` | backend="foo" → verdict=="error", `backend_used is None`, error contains "invalid backend: foo" |
| `test_exit_0_returns_clean` | subprocess returns rc=0 → clean, signature=None |
| `test_exit_1_returns_infected_with_signature` | rc=1, stdout has FOUND line → infected + correct signature |
| `test_exit_1_no_found_line_signature_is_none` | rc=1, malformed stdout → infected, signature=None |
| `test_exit_2_returns_error` | rc=2 → error, raw_output captured |
| `test_timeout_returns_error` | `communicate()` raises TimeoutExpired → proc.kill() called, verdict=="error", error contains "scanner timeout" |
| `test_file_not_found_returns_error` | `Popen()` raises FileNotFoundError → verdict=="error", error contains "scanner not found" |
| `test_oserror_non_fnf_returns_launch_error` | `Popen()` raises generic `OSError` (e.g. PermissionError) → verdict=="error", error contains "failed to launch scanner" |
| `test_scanner_from_config_passes_values` | config dict → correct ClamAVScanner attrs |
| `test_shell_false_always` | Popen called without shell=True |
| `test_clamdscan_could_not_connect_preserves_raw_output` | rc=2, stderr="Could not connect to clamd..." → verdict=="error", raw_output contains full diagnostic string |

Pattern mirrors `shared/tests/test_access_auth_retry_and_failhard.py`. Two module-level targets are patched per test:

- `monkeypatch.setattr("shared.clamav_scanner.shutil.which", ...)` — controls binary availability in all auto/explicit-backend tests; must be set before `Popen` to ensure selection logic is exercised, not assumed.
- `monkeypatch.setattr("shared.clamav_scanner.subprocess.Popen", ...)` — controls process execution.

The timeout test must verify `proc.kill()` was called (use a mock Popen whose `communicate()` raises TimeoutExpired and whose `kill()` is recorded).

### Validation commands

```bash
python3 -m py_compile shared/clamav_scanner.py
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py -q
```

---

## Risks + Mitigations

| Risk | Mitigation |
|------|-----------|
| Shell injection via file path | Always use list-form subprocess — never `shell=True` |
| Process leak on timeout | `Popen` + `communicate(timeout=N)`; `proc.kill()` + `proc.communicate()` in `except TimeoutExpired` |
| `clamdscan` socket permission error (exit 2 with misleading message) | Treat exit 2 uniformly as error; raw_output preserved for operator |
| Output format variation across ClamAV versions | Parse with regex, not positional split; test with synthetic stdout strings |
| `auto` resolution caches stale `shutil.which` result | `_resolve_binary()` called fresh on each `scan_file()` invocation; no instance caching |
| Misleading error when clamd daemon not running | clamdscan exits 2 with "Could not connect" in stderr; captured in raw_output |

---

## Assumptions

1. No new PyPI dependencies — subprocess only. The `clamd` PyPI package (2014) is excluded.
2. Config passed to `scanner_from_config()` uses the key names from SPEC_DRAFT.md: `backend`, `clamscan_path`, `clamdscan_path`, `timeout_seconds`.
3. `shutil.which()` is sufficient for binary detection; no absolute path resolution beyond what the OS PATH provides (user can override via `clamscan_path`/`clamdscan_path` config keys).
4. `--no-summary` flag is accepted by both `clamscan` and `clamdscan` and is safe to include unconditionally.
5. Tests will monkeypatch `subprocess.Popen` at the `shared.clamav_scanner` module namespace (not globally).
6. Python 3.8 minimum: `dataclasses` is available. `from __future__ import annotations` is added to the module. `Optional[X]` / `Union[X, Y]` used in code; bare `X | Y` syntax is avoided since that requires 3.10+.

---

## HI Test Needed?

**No.** Per TASK_CARDS.md C1 acceptance criteria: unit-level only. All behavior is exercised via monkeypatched subprocess; no real scanner binary required for C1 sign-off.
