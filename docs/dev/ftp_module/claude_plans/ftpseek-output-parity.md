# Plan: ftpseek Output/Wording Parity Pass

## Context

ftpseek has stale "MVP skeleton" phrasing in its CLI entry point and uses a bespoke `_FtpOutput` class instead of the shared `SMBSeekOutput` that smbseek uses. This means `--quiet`, `--verbose`, and `--no-colors` flags are partially ignored (quiet does nothing in `_FtpOutput`). The goal is a minimal wording/style pass to bring ftpseek to smbseek baseline, while keeping all parser-sensitive line formats exactly intact so the GUI progress parser continues to work.

## Files to Modify

| File | Change type |
|---|---|
| `shared/output.py` | Add `raw()` method to `SMBSeekOutput` |
| `shared/ftp_workflow.py` | Remove `_FtpOutput`; use `create_output_manager()` |
| `ftpseek` | Wording + mutual-exclusion style parity |
| `gui/tests/test_backend_progress_ftp.py` | New file — regression test for FTP parse |

**Untouched:** `shared/workflow.py`, `smbseek`, `progress.py`, `commands/ftp/`.

---

## Step 1 — `shared/output.py`: Add `raw()` to `SMBSeekOutput`

Insert after `workflow_complete()`, before `print_rollup_summary()`:

```python
def raw(self, msg: str) -> None:
    """Emit msg verbatim with flush=True, respecting --quiet."""
    if not self.quiet:
        with self._print_lock:
            print(msg, flush=True)
```

No `force` parameter — no current caller needs it and it avoids premature API surface. `flush=True` is present because `raw()` is used for subprocess-streamed lines that need to reach the pipe reader promptly.

---

## Step 2 — `shared/ftp_workflow.py`: Replace `_FtpOutput` with `SMBSeekOutput`

**a.** Update module docstring — remove "Card 4" reference:
```python
"""
FTP scan workflow orchestrator.

Completely separate from shared/workflow.py — no changes to SMB workflow.
"""
```

**b.** Delete the entire `_FtpOutput` class (lines 12–34).

**c.** Update `FtpWorkflow.__init__` signature — change type hint from `_FtpOutput` to `"SMBSeekOutput"` (or just `object`/drop hint).

**d.** Update `create_ftp_workflow()` — import and use `create_output_manager`:

```python
from shared.output import create_output_manager
output = create_output_manager(
    config,
    quiet=getattr(args, "quiet", False),
    verbose=getattr(args, "verbose", False),
    no_colors=getattr(args, "no_colors", False),
)
```

**e.** No changes to `FtpWorkflow.run()` — all `out.*` calls already match `SMBSeekOutput` API:
- `out.info(...)` → works, now respects `--quiet` and adds color
- `out.workflow_step(name, num, total)` → works; `SMBSeekOutput` emits `\n\033[94m[1/2] FTP Discovery\033[0m` which the regex `(?:\033\[\d+m)?\[(\d+)/(\d+)\]\s*(.+?)(?:\033\[\d+m)?$` handles correctly
- `out.raw(...)` → uses new method added in Step 1

---

## Step 3 — `ftpseek`: Wording + mutual-exclusion style parity

1. **Module docstring** (line 2):
   - Before: `"""FTP scan CLI entry point (Card 2 skeleton — no real FTP I/O yet)."""`
   - After: `"""FTP server discovery and assessment — CLI entry point."""`

2. **Parser description** (line 19):
   - Before: `description="FTP server discovery and assessment (MVP skeleton)",`
   - After: `description="FTP server discovery and assessment",`

3. **Generic exception message** (line ~63):
   - Before: `print(f"Fatal error: {exc}", file=sys.stderr)`
   - After: `print(f"Error: {exc}", file=sys.stderr)`

4. **Mutual-exclusion guard** — align with smbseek pattern: `print + return 1` inside `main()`, and update the entrypoint call to `sys.exit(main())` for clean exit semantics:
   - In `main()`, replace `parser.error("--verbose and --quiet are mutually exclusive")` with:
     ```python
     print("Error: Cannot use both --quiet and --verbose options")
     return 1
     ```
   - At module bottom, replace `main()` with `sys.exit(main())`

---

## Step 4 — `gui/tests/test_backend_progress_ftp.py`: New regression test

Placed under `gui/tests/` because `parse_final_results` lives in the GUI backend interface.
Fixtures contain only parser-critical lines (no incidental messages).

```python
"""Regression tests for parse_final_results() against FTP-style CLI output."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.backend_interface.progress import parse_final_results

_BLUE  = "\033[94m"
_RESET = "\033[0m"

# Only parser-critical lines — workflow steps, rollup stats, success marker.
_FTP_OUTPUT_WITH_ANSI = (
    f"{_BLUE}[1/2] FTP Discovery{_RESET}\n"
    f"{_BLUE}[2/2] FTP Access Verification{_RESET}\n"
    "📊 Hosts Scanned: 42\n"
    "🔓 Hosts Accessible: 7\n"
    "📁 Accessible Shares: 0\n"
    "🎉 FTP scan completed successfully\n"
)

_FTP_OUTPUT_NO_ANSI = (
    "[1/2] FTP Discovery\n"
    "[2/2] FTP Access Verification\n"
    "📊 Hosts Scanned: 10\n"
    "🔓 Hosts Accessible: 3\n"
    "📁 Accessible Shares: 0\n"
    "🎉 FTP scan completed successfully\n"
)


class TestFtpProgressParsing:
    def test_parse_rollup_with_ansi(self):
        """ANSI-wrapped output is stripped and rollup stats parse correctly."""
        result = parse_final_results(_FTP_OUTPUT_WITH_ANSI)
        assert result["hosts_scanned"] == 42
        assert result["hosts_accessible"] == 7
        assert result["accessible_shares"] == 0

    def test_success_marker_detected(self):
        """🎉 FTP scan completed successfully sets success=True."""
        result = parse_final_results(_FTP_OUTPUT_WITH_ANSI)
        assert result["success"] is True

    def test_parse_rollup_no_ansi(self):
        """No-colors output also parses correctly and sets success=True."""
        result = parse_final_results(_FTP_OUTPUT_NO_ANSI)
        assert result["hosts_scanned"] == 10
        assert result["hosts_accessible"] == 3
        assert result["success"] is True
```

---

## Parser Compatibility Summary

| Parser-sensitive line | Emitted via | Regex / check in progress.py | Safe? |
|---|---|---|---|
| `[1/2] FTP Discovery` | `SMBSeekOutput.workflow_step()` | `(?:\033\[\d+m)?\[(\d+)/(\d+)\]\s*(.+?)(?:\033\[\d+m)?$` — optional leading ANSI | ✓ |
| `[2/2] FTP Access Verification` | same | same | ✓ |
| `📊 Hosts Scanned: N` | `SMBSeekOutput.raw()` | `r'📊\s*Hosts Scanned:\s*(\d[\d,]*)'` after ANSI strip | ✓ |
| `🔓 Hosts Accessible: N` | same | `r'🔓\s*Hosts Accessible:\s*(\d[\d,]*)'` | ✓ |
| `📁 Accessible Shares: N` | same | `r'📁\s*Accessible Shares:\s*(\d[\d,]*)'` | ✓ |
| `🎉 FTP scan completed successfully` | `SMBSeekOutput.raw()` | literal check at progress.py:569 | ✓ |

---

## Verification

```bash
# Run new targeted test
/home/kevin/venvs/smbseek/venv-desktop/bin/python -m pytest gui/tests/test_backend_progress_ftp.py -v

# Full baseline (expect same 2 known failures, no new failures)
/home/kevin/venvs/smbseek/venv-desktop/bin/python -m pytest gui/tests/ shared/tests/ -q
```

Expected: 3 new passes in `gui/tests/test_backend_progress_ftp.py`. Known failures remain:
- `gui/tests/test_rce_reporter.py::test_insufficient_data_sets_not_run_status`
- `gui/tests/test_rce_verdicts.py::test_not_run_statuses`

---

## Assumptions

- The GUI scan manager does **not** currently pass `--quiet` to `ftpseek` subprocesses. This means `raw()` respecting `--quiet` is safe — the rollup and success-marker lines will always reach `parse_final_results()` in practice. If this assumption ever changes, the `raw()` calls in `ftp_workflow.py` would need revisiting.
