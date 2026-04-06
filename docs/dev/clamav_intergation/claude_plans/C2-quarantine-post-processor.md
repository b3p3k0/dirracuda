# C2 Plan: Reusable Quarantine Post-Processor Seam

## Context

C1 delivered `shared/clamav_scanner.py` with `ClamAVScanner.scan_file() -> ScanResult`. Without a
post-processing seam, C3 would have to embed scan dispatch logic directly inside `run_extract()`'s
inner loop, and future browser-download integration would duplicate it. C2 introduces the reusable
contract now so C3 only has to supply a concrete processor callable — not restructure the inner loop.

---

## Issue
No hook exists in `run_extract()` for post-download file routing. Scanning and file-movement
logic would otherwise be embedded directly in `gui/utils/extract_runner.py` with no reuse path.

## Root cause
`run_extract()` was written for download-only. The inner file loop has no extension point after
`conn.getFile()` completes successfully.

---

## Plan

### 1. New file: `shared/quarantine_postprocess.py`

Define two dataclasses and one passthrough function. No scanning or file movement in C2.

**Important:** `scan_result` is removed. The generic `metadata: Optional[Any]` field carries
caller-defined detail without coupling the seam to C1's `ScanResult` type. AV-specific typing
stays in C3+ when a real processor is constructed.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

@dataclass
class PostProcessInput:
    file_path: Path       # absolute path of the downloaded file
    ip_address: str
    share: str
    rel_display: str      # e.g. "subdir/file.txt"
    file_size: int

@dataclass
class PostProcessResult:
    final_path: Path          # where file ended up (same as file_path when not moved)
    verdict: str              # "skipped" | "clean" | "infected" | "error"
    moved: bool               # True if file was relocated
    destination: str          # "quarantine" | "extracted" | "known_bad"
    metadata: Optional[Any]   # caller-defined detail (e.g. ScanResult in C3+); None in C2
    error: Optional[str]      # only when verdict == "error"

# Callable contract type alias
PostProcessorFn = Callable[[PostProcessInput], PostProcessResult]

def passthrough_processor(inp: PostProcessInput) -> PostProcessResult:
    """No-op. Used when ClamAV is disabled. File stays where it is."""
    return PostProcessResult(
        final_path=inp.file_path,
        verdict="skipped",
        moved=False,
        destination="quarantine",
        metadata=None,
        error=None,
    )

__all__ = [
    "PostProcessInput",
    "PostProcessResult",
    "PostProcessorFn",
    "passthrough_processor",
]
```

No import of `ScanResult` or any GUI module. The seam is ClamAV-agnostic.

---

### 2. Modify `gui/utils/extract_runner.py`

**Change A — imports** (top of file, after existing `from shared.quarantine import ...`):
```python
from shared.quarantine_postprocess import PostProcessInput, PostProcessorFn
```

**Change B — `run_extract()` signature** (add one optional kwarg at the end, after `cancel_event`):
```python
def run_extract(
    ...
    cancel_event: Optional[Event] = None,
    post_processor: Optional[PostProcessorFn] = None,   # NEW
) -> Dict[str, Any]:
```

**Change C — injection point** (after successful `conn.getFile()`, currently lines 233–244):

Current code:
```python
total_files += 1
total_bytes += file_size
summary["files"].append({
    "share": share,
    "path": rel_display,
    "size": file_size,
    "saved_to": str(dest_path)
})
try:
    host_dir = download_dir.parent
    log_quarantine_event(host_dir, f"extracted {share}/{rel_display} -> {dest_path}")
except Exception:
    pass
```

Replace with:
```python
total_files += 1
total_bytes += file_size

# Post-processing seam — fail-open; original dest_path used if processor raises
final_path = dest_path
if post_processor is not None:
    try:
        _pp_inp = PostProcessInput(
            file_path=dest_path,
            ip_address=ip_address,
            share=share,
            rel_display=rel_display,
            file_size=file_size,
        )
        final_path = post_processor(_pp_inp).final_path
    except Exception as _pp_exc:
        summary["errors"].append({
            "share": share,
            "path": rel_display,
            "message": f"post_processor error (file kept in quarantine): {_pp_exc}",
        })
        # final_path stays as dest_path

summary["files"].append({
    "share": share,
    "path": rel_display,
    "size": file_size,
    "saved_to": str(final_path)   # unchanged when post_processor=None
})
try:
    host_dir = download_dir.parent
    log_quarantine_event(host_dir, f"extracted {share}/{rel_display} -> {final_path}")
except Exception:
    pass
```

When `post_processor=None`: `final_path = dest_path` — identical behavior to today.
When `post_processor=passthrough_processor`: same outcome (passthrough returns `inp.file_path`).
When processor raises: `final_path = dest_path`, error recorded, extraction continues.

---

### 3. New file: `shared/tests/test_quarantine_postprocess.py`

**Contract tests (pure Python, no network, no impacket):**

1. `test_passthrough_returns_skipped_verdict` — `verdict == "skipped"`, `moved is False`, `final_path == inp.file_path`, `metadata is None`, `error is None`
2. `test_passthrough_destination_is_quarantine` — `destination == "quarantine"`
3. `test_passthrough_does_not_move_file` (tmp_path) — file still exists at original path after call
4. `test_postprocess_result_fields_accessible` — construct `PostProcessResult` directly; assert all fields
5. `test_postprocess_input_fields_accessible` — construct `PostProcessInput` directly; assert all fields

**Seam injection tests (monkeypatched SMBConnection, no real network):**

Setup helper — a fake SMBConnection that serves one file (`a.txt`, 3 bytes):
```python
def _fake_conn():
    entry = MagicMock()
    entry.get_longname.return_value = "a.txt"
    entry.is_directory.return_value = False
    entry.get_filesize.return_value = 3
    entry.get_mtime_epoch.return_value = None
    conn = MagicMock()
    conn.listPath.return_value = [entry]
    conn.getFile.side_effect = lambda share, smb_path, writer: writer(b"abc")
    return conn
```

Test 6 — `test_run_extract_saved_to_uses_postprocessor_final_path`:
```
- Patch gui.utils.extract_runner.SMBConnection → returns _fake_conn()
- Define redirecting_processor(inp) → PostProcessResult(final_path=tmp_path/"redirected.txt", ...)
- Call run_extract(..., post_processor=redirecting_processor)
- Assert summary["files"][0]["saved_to"] == str(tmp_path / "redirected.txt")
```

Test 7 — `test_run_extract_saved_to_unchanged_without_postprocessor`:
```
- Patch gui.utils.extract_runner.SMBConnection → returns _fake_conn()
- Call run_extract(...) with no post_processor argument
- Assert summary["files"][0]["saved_to"] == str(download_dir / "share" / "a.txt")
```

Test 8 — `test_run_extract_postprocessor_exception_is_failopen`:
```
- Patch gui.utils.extract_runner.SMBConnection → returns _fake_conn()
- Define raising_processor(inp) → raise RuntimeError("boom")
- Call run_extract(..., post_processor=raising_processor)
- Assert summary["files"][0]["saved_to"] == str(download_dir / "share" / "a.txt")  (original path)
- Assert any(entry with "post_processor error" in summary["errors"])
```

---

## Files to change (C2 only)

| Action | File |
|--------|------|
| Create | `shared/quarantine_postprocess.py` |
| Modify | `gui/utils/extract_runner.py` (3 surgical changes: import, signature, injection point) |
| Create | `shared/tests/test_quarantine_postprocess.py` (8 tests: 5 contract + 3 seam integration) |

**Not touched in C2:**
- `shared/config.py`, `conf/config.json.example` (C6)
- `gui/components/app_config_dialog.py` (C6)
- `gui/components/dashboard.py`, `gui/components/server_list_window/actions/batch.py` (C3)
- `shared/quarantine.py` (C4)
- `shared/clamav_scanner.py` (C1, complete)

---

## Validation plan

```bash
# 1. Syntax check all modified/new files
python3 -m py_compile shared/quarantine_postprocess.py gui/utils/extract_runner.py

# 2. Contract + seam integration tests (covers all 3 paths: redirected, unchanged, fail-open)
./venv/bin/python -m pytest shared/tests/test_quarantine_postprocess.py -v

# 3. C1 regression — confirm clamav_scanner is unaffected
./venv/bin/python -m pytest shared/tests/test_clamav_scanner.py -q
```

Tests 6 and 7 together cover the "with processor" and "no processor supplied" runtime paths in
the modified loop. Test 8 covers the fail-open exception path.

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Circular import: `quarantine_postprocess` → `clamav_scanner` | **Resolved**: `ScanResult` removed from C2 contract; no import of `clamav_scanner` in C2 |
| Breaking callers of `run_extract` | New param is keyword-only with `None` default; all existing callers unaffected |
| `saved_to` field consumed by C3+ batch callers | Field name unchanged; value unchanged when passthrough — no breakage |
| Quarantine log shows wrong path | When post_processor=None: `final_path = dest_path`; log message identical to today |
| Processor raises, hard-crashes extraction | **Resolved**: try/except with fallback to `dest_path`; error appended to `summary["errors"]` |
| Thread safety | `post_processor` runs in the same worker thread as `run_extract`; no UI thread impact |

---

## Assumptions

1. `PostProcessInput` does not carry `clamav_cfg`; that stays in C3 when a real processor callable
   is constructed from config.
2. `run_extract` callers (`dashboard.py`, `batch.py`) pass no `post_processor` keyword yet —
   they continue working unchanged.
3. `PostProcessorFn = Callable[[PostProcessInput], PostProcessResult]` is sufficient as the type
   contract; no abstract base class needed for phase 1.
4. Appending to `summary["errors"]` for processor failures is the right signal: it's visible to
   C3+ callers without adding a new top-level summary key in C2.

---

## HI test needed?

**No.**
C2 introduces no observable runtime change. All paths are passthrough or fail-open. Manual
verification is not required until C3 integrates real scanner calls.
