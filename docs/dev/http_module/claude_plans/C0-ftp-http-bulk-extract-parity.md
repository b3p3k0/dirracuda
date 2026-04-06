# C0: Plan + Contract Inventory — FTP/HTTP Bulk Extract Parity (Revision 7)

Date: 2026-04-01
Status: Planning only — no code edits

---

## Issue

We need a grounded implementation plan for FTP/HTTP bulk extract parity — one that correctly maps real call paths, data contracts, and runtime blockers before any code is written.

## Root Cause

1. `extract_runner.run_extract()` is SMB-only (impacket transport, SMB share enumeration).
2. `batch.py:_execute_extract_target()` hardcodes a skip for `host_type in ("F", "H")` (lines 419–426).
3. `dashboard.py:_extract_single_server()` has no `host_type` guard — FTP/HTTP rows reach SMB transport and fail uncleanly.
4. Dashboard's `accessible_shares` parsing (lines 2405–2406) produces bogus SMB share names for FTP/HTTP rows.
5. Dashboard extracted-flag write (`upsert_extracted_flag(ip, True)`, line 2465) is an SMB-only shim — ignores `host_type`.
6. No FTP/HTTP recursive traversal or download logic exists in the extract layer.
7. `details.py` fallback extract path (lines 1032, 1065) is SMB-only with no host_type guard.

---

## Locked decisions honored

| Decision | Value |
|----------|-------|
| Q1 Probe prerequisite | Required — no snapshot → `skipped` |
| Q2 Candidate scope | **Recursive traversal** (bounded by configured limits); probe snapshot is a gate only |
| Q3 HTTP endpoint identity | Resolve `port/scheme` from DB (`get_http_server_detail`) when missing from target |
| Q4 Missing snapshot | `skipped` with `"Probe required before FTP/HTTP extract"` |
| Q5 Detail-popup fallback | Explicitly block FTP/HTTP in fallback path; normal callback path supports all protocols |
| Q6 Extension filters | Same `included/excluded_extensions` + `extension_mode` semantics as SMB |

---

## 1) Runtime call paths — confirmed

**Dashboard post-scan bulk extract** (`dashboard.py`):
```
_extract_single_server(server, ...)                          # line 2389
  BUG A: no host_type guard — FTP/HTTP fall through to SMB path
  BUG B: lines 2405-2406: accessible_shares parsed as SMB share names
  BUG C: line 2465: upsert_extracted_flag(ip, True) — SMB shim, ignores host_type
  BUG D: line 2472: returns "success" unconditionally; never checks summary status
         before writing extracted flag
```

**Server-list batch extract** (`batch.py`):
```
_execute_extract_target(...)                                 # line 414
  - lines 419-426: if host_type in ("F", "H") → skipped return (hardcoded)
  - line 437: base_path = Path(options.get("download_path", ...)).expanduser()
  - lines 439-447: quarantine_dir = create_quarantine_dir(ip, purpose="extract",
                                                           base_path=base_path)
  - SMB path (lines 449-514):
      run_extract() inside try block
      line 482: log_path = write_extract_log(summary)
      line 492: reads summary["totals"]
      line 500: note_parts.append(f"log: {log_path}")   ← requires log_path
      line 503: _handle_extracted_update(...) unconditionally
      line 511: returns "status": "success" hardcoded
  BUG E: if FTP/HTTP runner returns skipped/failed/cancelled, caller marks extracted +
         reports success
  BUG F: old skip block (419-426) ran before base_path was set at line 437
```

**Detail-popup fallback** (`details.py`):
```
  line 1032: _parse_accessible_shares(...) — SMB only
  line 1065: extract_runner.run_extract(...) — SMB runner only
  BUG G: no host_type check; FTP/HTTP rows can reach SMB extract from popup
```

---

## 2) Concrete blockers

| # | Blocker | Location |
|---|---------|----------|
| B1 | `run_extract()` is SMB-only | `gui/utils/extract_runner.py` |
| B2 | Hardcoded skip for F/H | `batch.py:419–426` |
| B3 | No `host_type` branch in dashboard | `dashboard.py:2389` |
| B4 | `accessible_shares` parsing for FTP/HTTP | `dashboard.py:2405–2406` |
| B5 | `upsert_extracted_flag` is SMB-only shim | `dashboard.py:2465` / `database_access.py:1018` |
| B6 | No FTP/HTTP recursive traversal or download logic | — |
| B7 | Callers unconditionally mark extracted and report success after any summary return | `batch.py:503,511` / `dashboard.py:2465,2472` |
| B8 | `details.py` fallback has no host_type guard | `details.py:1032,1065` |
| B9 | `log_path` undefined for FTP/HTTP success path (set at line 482 in SMB try block, referenced at line 500) | `batch.py:482,500` |

---

## 3) Implementation shape

### Principle

Keep `run_extract()` (SMB) entirely untouched. Add two new runners with a consistent `status`-keyed output contract. Callers gate extracted-flag writes and outer result status on this key.

---

### 3a. Runner output — status contract

The new FTP/HTTP runners always return a dict with a top-level `"status"` key. This applies to ALL exit paths — early exits AND mid-run termination:

| `"status"` value | When returned |
|-----------------|---------------|
| `"skipped"` | Precondition unmet (no probe snapshot, empty snapshot, unresolvable port) |
| `"failed"` | Transport/connect failure before any download |
| `"cancelled"` | `cancel_event` set **at any point** — before start OR detected mid-walk/download |
| `"success"` | Run completed, whether or not files were downloaded |

**Critical:** mid-run cancel returns `{"status": "cancelled"}` — NOT a success-shaped summary with `stop_reason`. This ensures the caller's `status`-key gate correctly blocks extracted-flag writes for cancelled runs. This differs from the SMB runner (which uses stop_reason + exception), but the SMB runner is not changed.

Caller gating pattern (both `batch.py` and dashboard helpers):
```python
runner_status = summary.get("status", "success")  # "success" = SMB legacy fallback
if runner_status in ("skipped", "failed", "cancelled"):
    return {"ip_address": ip, "action": "extract",
            "status": runner_status, "notes": summary.get("notes", "")}
# Only here: mark extracted, write log, return "success"
```

---

### 3b. Batch path fix (`batch.py`) — full FTP/HTTP flow

The FTP/HTTP block replaces the skip block at lines 419–426. It runs **before** `base_path` is set at line 437. The block must compute `base_path` from `options` itself using the same expression as line 437.

```python
# Replace lines 419-426:
if host_type in ("F", "H"):
    # 1. Resolve endpoint
    if host_type == "H":
        resolved = _resolve_http_endpoint(target, self.db_reader)
        if resolved is None:
            return {"ip_address": ip_address, "action": "extract",
                    "status": "skipped",
                    "notes": "HTTP extract requires a known port; probe required"}
        ftp_http_port, ftp_http_scheme = resolved
    else:
        ftp_http_port = target.get("port")
        ftp_http_scheme = None

    # 2. Compute base_path from options (line 437 sets this for SMB path,
    #    but FTP/HTTP block runs before it — must compute independently)
    ftp_http_base = Path(options.get("download_path",
                         str(Path.home() / ".dirracuda" / "quarantine"))).expanduser()
    try:
        quarantine_dir = create_quarantine_dir(ip_address, purpose="extract",
                                               base_path=ftp_http_base)
    except Exception as e:
        return {"ip_address": ip_address, "action": "extract",
                "status": "failed", "notes": f"Quarantine error: {e}"}

    # 3. Call runner
    clamav_cfg: dict = options.get("clamav_config") or {}
    if host_type == "F":
        summary = extract_runner.run_ftp_extract(
            ip_address, ftp_http_port, quarantine_dir,
            clamav_config=clamav_cfg, **_extract_opts(options, cancel_event))
    else:
        summary = extract_runner.run_http_extract(
            ip_address, ftp_http_port, ftp_http_scheme, quarantine_dir,
            clamav_config=clamav_cfg, **_extract_opts(options, cancel_event))

    # 4. Gate on runner status
    runner_status = summary.get("status", "success")
    if runner_status in ("skipped", "failed", "cancelled"):
        return {"ip_address": ip_address, "action": "extract",
                "status": runner_status, "notes": summary.get("notes", "")}

    # 5. Write log (sets log_path — required by notes-build at line 500)
    log_path = extract_runner.write_extract_log(summary)

    # 6. Fall through to existing lines 492-514:
    #    log_path defined, summary["totals"] present, host_type correct for
    #    _handle_extracted_update at line 503
```

The existing SMB block (lines 428–514) is untouched. The FTP/HTTP block is self-contained; it only falls through to 492–514 on `"success"`.

---

### 3c. Dashboard fix (`dashboard.py:_extract_single_server`)

Add `host_type` branch before line 2405. New helpers `_extract_ftp_server` / `_extract_http_server`:
- Skip `accessible_shares` parsing
- Resolve HTTP port (return `skipped` if unresolvable)
- Call runner
- Check `summary.get("status")`: only call `upsert_extracted_flag_for_host` and return `"success"` when `runner_status == "success"`
- Use `db_reader.upsert_extracted_flag_for_host(ip, host_type, True, protocol_server_id=..., port=...)` (`database_access.py:1231`)

---

### 3d. Detail-popup fallback fix (`details.py`, Q5)

Add `host_type` guard before line 1032:

```python
host_type = server_data.get("host_type", "S")
if host_type in ("F", "H"):
    protocol = "FTP" if host_type == "F" else "HTTP"
    messagebox.showinfo("Extract",
        f"{protocol} extract is not available from this view.\n"
        "Use the Server List extract action.")
    return
```

`run_extract()` at line 1065 is not changed.

---

### 3e. Destination path handling — collision prevention + traversal safety

**Basename collision:** Both navigators write basename only (FTP `ftp_browser.py:375`, HTTP `http_browser.py:241`). Recursive extraction collides on same-named files across directories.

**Path traversal:** HTTP parser (`http_browser.py:90–92`) stores root-absolute hrefs as-is. `PurePosixPath` does not resolve `..`. Naive dir mirroring can escape quarantine.

**Fix — `_safe_dest_dir`:**

```python
def _safe_dest_dir(remote_path: str, download_dir: Path) -> Optional[Path]:
    rel_path = PurePosixPath(remote_path.lstrip("/"))
    rel_parent = rel_path.parent
    candidate = (download_dir / rel_parent).resolve()
    try:
        candidate.relative_to(download_dir.resolve())
    except ValueError:
        return None  # path escapes quarantine
    return candidate
```

`download_dir` must exist before `.resolve()` is called. In download loop:
```python
local_dest_dir = _safe_dest_dir(remote_path, download_dir)
if local_dest_dir is None:
    skipped.append({"path": remote_path, "reason": "unsafe_path"})
    continue
local_dest_dir.mkdir(parents=True, exist_ok=True)
nav.download_file(remote_path, local_dest_dir)
```

Root-level files: `rel_parent = PurePosixPath(".")` → resolves to `download_dir` → passes boundary check.

---

### 3f. HTTP port resolution — no `port=None` allowed

`HttpNavigator._make_url` (`http_browser.py:156`): `f"{scheme}://{ip}:{port}{path}"` — `None` produces a broken URL.

`_resolve_http_endpoint(target, db_reader)` resolution:
1. `target["port"]` if valid integer → return `(port, target.get("scheme", "http"))`
2. `db_reader.get_http_server_detail(ip, protocol_server_id=..., port=...)` (`database_access.py:2074`)
3. Both fail → return `None`; caller returns `skipped`

---

### 3g. Runner control flow

**Cancellation: consistent across all exit points.** If `cancel_event` is set at any point — before start, during walk, or during download — the runner returns `{"status": "cancelled", "notes": "Cancelled"}`. No success-shaped summary is returned for a cancelled run. The ClamAV seam is not invoked.

**FTP runner steps:**
```
1. cancel_event.is_set() → {"status": "cancelled", "notes": "Cancelled before start"}
2. load_ftp_probe_result(ip) → None → {"status": "skipped", "notes": "Probe required..."}
   snapshot["shares"] empty → {"status": "skipped", "notes": "Probe snapshot empty"}
3. FtpNavigator(connect_timeout=..., max_file_bytes=...).connect(ip, port)
   failure → {"status": "failed", "notes": str(e)}
4. _ftp_walk: nav.list_dir(current_path)
   for entry in result.entries:
     if cancel_event.is_set(): nav.cancel() → return {"status": "cancelled", ...}
     if not entry.is_dir:
         abs_path = str(PurePosixPath(current_path) / entry.name)
         yield (abs_path, entry.size)
     elif depth_remaining > 0:
         yield from recurse
5. Per candidate:
   - cancel_event.is_set() → nav.cancel(); return {"status": "cancelled", ...}
   - limit checks (time/count/size) → update stop_reason, break loop
   - extension filter → skipped[] entry
   - _safe_dest_dir → None → skipped[unsafe_path]
   - mkdir; nav.download_file(remote_path, local_dest_dir)
     Success → files[]; FtpFileTooLargeError → skipped[]; FileExistsError → skipped[];
     other → errors[]
6. nav.disconnect() (finally)
7. post_processor (ClamAV seam) — only reached for non-cancelled runs
8. return {"status": "success", "totals": {...}, "files": [...], "skipped": [...],
           "errors": [...], "timed_out": bool, "stop_reason": str|None, "clamav": {...}}
```

**HTTP runner:** same structure; stateless (`HttpNavigator`, no `connect`/`disconnect`); probe gate uses `load_http_probe_result(ip, port)`; `entry.name` is absolute path — used directly.

**Navigator cancel:** `cancel_event` is NOT a constructor arg. Both navigators own internal `_cancel_event`. Propagation: `cancel_event.is_set()` → `navigator.cancel()`.

---

## 4) Data contracts

### Entry (confirmed: `smb_browser.py:24`)

```python
@dataclass
class Entry:
    name: str        # FTP: basename relative to listed dir; HTTP: full absolute path
    is_dir: bool
    size: int
    modified_time: Optional[float]
# File check: not entry.is_dir
```

### Path construction

| Protocol | `entry.name` | `abs_path` |
|----------|-------------|------------|
| FTP | basename relative to `current_path` | `str(PurePosixPath(current_path) / entry.name)` |
| HTTP | full absolute path | `entry.name` directly |

### HTTP snapshot (confirmed: `http_probe_runner.py:168–178`)

`errors` is top-level only — never inside `shares[]`. Non-empty `errors` is non-fatal telemetry.

### Cache functions

Both return `None` for missing file AND JSON parse failure. Single skip message covers both.

---

## 5) Error semantics

| Condition | `status` | Notes |
|-----------|----------|-------|
| `load_*_probe_result()` → `None` | `skipped` | `"Probe required before {FTP\|HTTP} extract"` |
| `snapshot["shares"]` empty | `skipped` | `"Probe snapshot empty"` |
| HTTP port unresolvable | `skipped` | `"HTTP extract requires a known port; probe required"` |
| `cancel_event` before start | `cancelled` | `"Cancelled before start"` |
| `cancel_event` during walk or download | `cancelled` | `"Cancelled"` |
| Connect failure | `failed` | Exception message |
| Individual file: unsafe path | file `skipped[]` | `reason="unsafe_path"` |
| Individual file: too large | file `skipped[]` | `reason="file_too_large"` |
| Individual file: `FileExistsError` | file `skipped[]` | `reason="already_exists"` |
| Individual file: other error | file `errors[]` | Job continues |
| Run completes (0+ files) | `success` | `timed_out`/`stop_reason` reflect limits hit |
| ClamAV error | fail-open | Matches SMB |

**Caller gating:** Only write extracted flag and return outer `"success"` when runner `status == "success"`.

---

## 6) Required test updates

### Existing test to update

| File | Test | Line | Change |
|------|------|------|--------|
| `gui/tests/test_action_routing.py` | `test_extract_ftp_row_returns_skipped` | 608 | Rename; mock `load_ftp_probe_result` → `None`; assert `status=="skipped"`, `"Probe required"` in notes |

### New tests

| Test file | Test | Covers |
|-----------|------|--------|
| `gui/tests/test_action_routing.py` | `test_extract_ftp_row_no_snapshot_skipped` | None → skipped, extracted NOT marked |
| `gui/tests/test_action_routing.py` | `test_extract_ftp_row_with_snapshot_routes_to_runner` | Snapshot → `run_ftp_extract` called; `write_extract_log` called; extracted marked |
| `gui/tests/test_action_routing.py` | `test_extract_http_row_no_snapshot_skipped` | None → skipped, extracted NOT marked |
| `gui/tests/test_action_routing.py` | `test_extract_http_row_no_port_skipped` | Unresolvable port → skipped, extracted NOT marked |
| `gui/tests/test_action_routing.py` | `test_extract_http_row_with_snapshot_routes_to_runner` | Snapshot + valid port → `run_http_extract` called; extracted marked |
| `gui/tests/test_action_routing.py` | `test_extract_ftp_skipped_result_does_not_mark_extracted` | Runner `status="skipped"` → `_handle_extracted_update` NOT called |
| `gui/tests/test_action_routing.py` | `test_extract_ftp_cancelled_mid_run_does_not_mark_extracted` | Runner `status="cancelled"` → `_handle_extracted_update` NOT called |
| `gui/tests/test_action_routing.py` | `test_popup_extract_blocked_for_ftp_row` | details.py guard: FTP row → showinfo called, `run_extract` NOT called (Q5) |
| `gui/tests/test_action_routing.py` | `test_popup_extract_blocked_for_http_row` | details.py guard: HTTP row → showinfo called, `run_extract` NOT called (Q5) |
| `gui/tests/test_extract_runner_clamav.py` | FTP/HTTP ClamAV seam tests | post_processor wiring for new runners |
| `gui/tests/test_extract_runner_clamav.py` | `test_safe_dest_dir_rejects_traversal_path` | `_safe_dest_dir` returns None for `..`-escape paths |
| `gui/tests/test_extract_runner_clamav.py` | `test_safe_dest_dir_accepts_valid_nested_path` | `_safe_dest_dir` returns correct mirrored dir for normal paths |
| `gui/tests/test_extract_runner_clamav.py` | `test_safe_dest_dir_accepts_root_level_file` | `_safe_dest_dir` returns `download_dir` for `/file.txt` |
| `gui/tests/test_dashboard_bulk_ops.py` | `test_dashboard_ftp_extract_branches_to_ftp_runner` | FTP row → `run_extract` (SMB) never called |
| `gui/tests/test_dashboard_bulk_ops.py` | `test_dashboard_http_extract_branches_to_http_runner` | HTTP row → `run_extract` (SMB) never called |
| `gui/tests/test_dashboard_bulk_ops.py` | `test_dashboard_ftp_extract_uses_host_aware_flag` | Extracted flag uses `upsert_extracted_flag_for_host(host_type="F")` |
| `gui/tests/test_dashboard_bulk_ops.py` | `test_dashboard_skipped_result_does_not_mark_extracted` | Runner `status="skipped"` → extracted flag NOT written |
| `gui/tests/test_dashboard_bulk_ops.py` | `test_dashboard_cancelled_result_does_not_mark_extracted` | Runner `status="cancelled"` → extracted flag NOT written |
| `gui/tests/test_database_access_protocol_writes.py` | `test_upsert_extracted_flag_for_host_http` | DB write routes to `http_probe_cache` |

---

## 7) Files to change

| File | Change | Scope |
|------|--------|-------|
| `gui/utils/extract_runner.py` | Add | `run_ftp_extract()`, `run_http_extract()`, `_ftp_walk()`, `_http_walk()`, `_safe_dest_dir()`, `_skipped_summary()`, `_failed_summary()`, `_cancelled_summary()` |
| `gui/components/server_list_window/actions/batch.py` | Edit | Replace lines 419–426 with self-contained FTP/HTTP block (computes `base_path` from `options`, calls `write_extract_log`, falls through to 492–514 on success only); add `_resolve_http_endpoint()` |
| `gui/components/dashboard.py` | Edit | Add `host_type` branch before line 2405; new `_extract_ftp_server()`, `_extract_http_server()` with runner-status gate; replace line 2465 shim with `upsert_extracted_flag_for_host` |
| `gui/components/server_list_window/details.py` | Edit | Add `host_type` guard before line 1032 (Q5 block) |
| `gui/tests/test_action_routing.py` | Edit/Add | Update FTP always-skip test; add routing + gating + cancelled + Q5 popup tests |
| `gui/tests/test_extract_runner_clamav.py` | Add | FTP/HTTP ClamAV seam + `_safe_dest_dir` unit tests |
| `gui/tests/test_dashboard_bulk_ops.py` | Add | Dashboard branch + extracted-flag + skipped-gate + cancelled-gate tests |

**Not touching:** `run_extract()` internals, `shared/ftp_browser.py`, `shared/http_browser.py`, `shared/smb_browser.py`, probe cache modules, `database_access.py`, DB schema.

---

## Validation plan

```bash
python3 -m py_compile gui/utils/extract_runner.py
python3 -m py_compile gui/components/server_list_window/actions/batch.py
python3 -m py_compile gui/components/dashboard.py
python3 -m py_compile gui/components/server_list_window/details.py

./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py -q
./venv/bin/python -m pytest gui/tests/test_database_access_protocol_writes.py -q

xvfb-run -a ./venv/bin/python -m pytest gui/tests/ -q --tb=short
```

---

## Risks

| Risk | Mitigation |
|------|------------|
| **R1** FTP `entry.name` is relative — join with `current_path` | `_safe_dest_dir` catches any escapes |
| **R2** HTTP root-absolute hrefs with `..` not normalized | `_safe_dest_dir` resolves + boundary-checks; skips to `skipped[unsafe_path]` |
| **R3** `_safe_dest_dir` uses `.resolve()` — `download_dir` must exist first | Create `download_dir` before walk loop; catch `OSError` from `mkdir` |
| **R4** Symlink loops in recursive traversal | Track seen absolute paths per run |
| **R5** `gui/tests/` may be gitignored | `git add -f gui/tests/` per PROJECT_GUIDELINES.md |
| **R6** Dashboard hardcodes `max_depth=3`, `delay=0`, `timeout=30` | Same values in FTP/HTTP helpers for parity |
| **R7** FtpNavigator `max_depth` field may interact with runner depth counter | Set navigator `max_depth` generously; rely on runner counter; verify at C2 start |

---

## Assumptions

| # | Assumption | Confidence | Verify before |
|---|-----------|------------|---------------|
| A1 | `upsert_extracted_flag_for_host(ip, "H", True, ...)` routes to `http_probe_cache` | High — `database_access.py:1254–1256` | DB write test in C3 |
| A2 | ClamAV post_processor is protocol-agnostic | High | C1 |
| A3 | FTP/HTTP runner summary contract (success path) matches `run_extract` shape plus `"status": "success"` | Required — callers use `summary["totals"]`, `summary.get("clamav")` | C1 |
| A4 | `HttpNavigator` stateless — no `connect()` needed | Confirmed `http_browser.py:115` | — |
| A5 | FtpNavigator `max_depth` is for probe/browse; runner controls extract depth separately | ~80% | Verify at C2 start |

---

## HI Test Needed?

**No** — this is a planning card with no code changes.

Manual Gate B deferred to C2, C3, and C5 per task card definitions.
