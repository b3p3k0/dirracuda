# C5 — ClamAV Results Dialog + Session Mute

## Context

C1–C4 are complete. `run_extract()` in `gui/utils/extract_runner.py` already returns a `clamav` block
in its summary dict (line 491). The problem: both calling sites (`dashboard._extract_single_server` at
line 2384 and `batch._execute_extract_target` at line 508) build slim return dicts that drop the full
`summary` — the `clamav` block never reaches the UI layer. C5 must surface that block, add a results
dialog, and attach in-memory session mute.

---

## Issue
Operators have no way to see AV scan outcomes after a bulk extract. The clamav summary exists in
`run_extract`'s return value but is discarded before it reaches the GUI.

## Root cause
Both extract entry points (`_extract_single_server` in dashboard.py and `_execute_extract_target` in
batch.py) return a minimal dict `{ip, action, status, notes}` that omits `summary["clamav"]`. No
dialog exists to present it, and there is no session-mute mechanism.

## Fix

### 1. `gui/utils/session_flags.py` (new)
Module-level in-memory flag store. No persistence, no GUI coupling.

```python
_flags: dict = {}
CLAMAV_MUTE_KEY = "clamav_results_dialog_muted"

def set_flag(key: str, value: bool = True) -> None: ...
def get_flag(key: str, default: bool = False) -> bool: ...
def clear_flag(key: str) -> None: ...
```

### 2. `gui/components/clamav_results_dialog.py` (new)
Two public callables:

**`should_show_clamav_dialog(job_type, results, clamav_cfg) -> bool`**
Returns True when:
- `job_type == "extract"`
- session mute not active
- `show_results` is truthy — coerce via a local `_coerce_bool_like(v, default=True) -> bool` helper
  defined in `clamav_results_dialog.py` (bool passthrough, else `str(v).strip().lower() in {"true","yes","1"}`;
  default `True` when key absent). Do not import private symbols from `extract_runner`.
- at least one result has `result["clamav"]["enabled"] == True`

**`show_clamav_results_dialog(*, parent, theme, results, on_mute, wait, modal) -> Optional[tk.Toplevel]`**
Input: list of per-host result dicts (each has optional `"clamav"` key).
Shows:
- Aggregate totals header: scanned / clean / infected / errors across all enabled hosts
- Treeview with infected items (`path`, `signature`, `moved_to`) and error items
- "Mute until restart" button → calls `on_mute` callback and closes
- "Close" button
- Fail-safe: entire function wrapped in try/except; returns None on render failure

### 3. `gui/components/dashboard.py` (modify, 2 hunks)

**Hunk A — `_extract_single_server` success return (line ~2384)**
Add `"clamav"` key to the success return dict:
```python
return {
    ...
    "clamav": summary.get("clamav", {"enabled": False}),
}
```
Failure/skip/cancel paths unchanged (they never reach `run_extract`).

**Hunk B — add `_load_clamav_config(self)` helper method on Dashboard**
New method used only where config is not already in memory (i.e., `_run_post_scan_batch_ops`).
`_execute_batch_extract` already has `config_data` in memory at line 2237 — leave that line unchanged
(`clamav_cfg = config_data.get("clamav", {})`). No double-read.

```python
def _load_clamav_config(self) -> Dict[str, Any]:
    """Read the clamav section from conf/config.json. Returns {} on any error."""
    config_path = self.settings_manager.get_setting('backend.config_path', None) if self.settings_manager else None
    if not config_path:
        return {}
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return data.get("clamav", {})
    except Exception:
        return {}
```

**Hunk C — `_run_post_scan_batch_operations` dialog call (line ~1637–1659)**
(Real method name is `_run_post_scan_batch_operations` — verify exact name in dashboard.py before editing.)
Initialize `extract_results` to `[]` before the conditional block so it is always bound:
```python
extract_results: List[Dict[str, Any]] = []   # <-- add before if bulk_extract_enabled:

if bulk_extract_enabled:
    if not extract_targets:
        summary_stack.append(...)
    else:
        extract_results = self._execute_batch_extract(extract_targets)  # assigns here
        summary_stack.append(("extract", extract_results))
```

After the `summary_stack` loop, the dialog call is now safe:
```python
if show_dialogs and extract_results:
    _clamav_cfg = self._load_clamav_config()
    self._maybe_show_clamav_dialog(extract_results, _clamav_cfg, wait=True, modal=True)
```

`_maybe_show_clamav_dialog` is an instance method (directly testable):
```python
def _maybe_show_clamav_dialog(self, results, clamav_cfg, *, wait=False, modal=False):
    try:
        from gui.components.clamav_results_dialog import (
            should_show_clamav_dialog, show_clamav_results_dialog
        )
        from gui.utils import session_flags
        if should_show_clamav_dialog("extract", results, clamav_cfg):
            def _mute():
                session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)
            show_clamav_results_dialog(
                parent=self.parent, theme=self.theme, results=results,
                on_mute=_mute, wait=wait, modal=modal,
            )
    except Exception:
        pass  # fail-safe
```

### 4. `gui/components/server_list_window/actions/batch.py` (modify, 1 hunk)

**`_execute_extract_target` success return (line ~508)**
Add `"clamav"` key the same way as dashboard hunk A.

### 5. `gui/components/server_list_window/actions/batch_status.py` (modify, 1 hunk)

**`_finalize_batch_job` (line ~129–130)**
The existing block is `if results and show_summary: self._show_batch_summary(...)`. The ClamAV dialog
call goes inside the same guard — not after it — so non-interactive shutdown paths (`show_summary=False`)
never trigger UI:
```python
if results and show_summary:
    self._show_batch_summary(job_type, results)
    if job_type == "extract":
        _clamav_cfg = job.get("options", {}).get("clamav_config", {})
        self._maybe_show_clamav_dialog(results, _clamav_cfg, wait=False, modal=False)
```

`_maybe_show_clamav_dialog` is shared via the mixin — same implementation as dashboard hunk C but
with `self.window` as parent. It lives on `ServerListWindowBatchStatusMixin`:
```python
def _maybe_show_clamav_dialog(self, results, clamav_cfg, *, wait=False, modal=False):
    try:
        from gui.components.clamav_results_dialog import (
            should_show_clamav_dialog, show_clamav_results_dialog
        )
        from gui.utils import session_flags
        if should_show_clamav_dialog("extract", results, clamav_cfg):
            def _mute():
                session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY)
            show_clamav_results_dialog(
                parent=self.window, theme=self.theme, results=results,
                on_mute=_mute, wait=wait, modal=modal,
            )
    except Exception:
        pass  # fail-safe
```

`clamav_config` is already stored in `job["options"]["clamav_config"]` (set in
`_launch_extract_workflow` at batch_operations.py line 565).

---

## Dialog ordering relative to batch summary

| Context | Batch summary | ClamAV dialog |
|---------|--------------|---------------|
| Dashboard post-scan | `wait=True, modal=True` — shown first, blocks | ClamAV shown after batch summary closes, `wait=True, modal=True` |
| Server-list on-demand | `wait=False, modal=False` | ClamAV also `wait=False, modal=False`, shown immediately after |

---

## Files changed

| File | Change |
|------|--------|
| `gui/utils/session_flags.py` | **New** |
| `gui/components/clamav_results_dialog.py` | **New** |
| `gui/components/dashboard.py` | Modify: 3 hunks (A: return dict, B: `_load_clamav_config`, C: dialog call) |
| `gui/components/server_list_window/actions/batch.py` | Modify: 1 hunk (return dict) |
| `gui/components/server_list_window/actions/batch_status.py` | Modify: 2 hunks (`_finalize_batch_job` call + `_maybe_show_clamav_dialog` method) |
| `gui/tests/test_clamav_results_dialog.py` | **New** |

---

## Test plan (new file: `gui/tests/test_clamav_results_dialog.py`)

**Unit / contract tests:**

1. **session_flags** — set/get/clear round-trip; get with default; reset `_flags` dict between every
   test (monkeypatch or explicit `clear_flag` teardown) to prevent mute-state leakage across cases
2. **`should_show_clamav_dialog` gating** — False when job_type != "extract"; False when muted; False
   when all `clamav.enabled == False`; False when `show_results == False` (string); False when
   `show_results == "false"` (string coercion); True on happy path with `show_results` absent (default on)
3. **Dialog construction** — `show_clamav_results_dialog` returns a `tk.Toplevel`; aggregate totals
   reflect input; infected/error treeview rows are populated
4. **Mute button** — invoking the mute command calls `on_mute` callback; `session_flags.get_flag`
   returns True afterward
5. **Fail-safe** — monkeypatch `tk.Toplevel` to raise; function returns None, does not propagate
6. **Return dict regression** — mock `run_extract`; call `_extract_single_server` (dashboard) and
   `_execute_extract_target` (batch.py) on a success path; assert `"clamav"` key present in return dict

**Wiring / invocation tests (verify actual call path, not just internals):**

7. **Dashboard post-scan — dialog shown** — build minimal dashboard stub; patch
   `_execute_batch_extract` to return one result with `"clamav": {"enabled": True, ...}`; patch
   `show_clamav_results_dialog`; call `_run_post_scan_batch_operations` with `show_dialogs=True`; assert
   `show_clamav_results_dialog` called once with `wait=True, modal=True`
8. **Dashboard post-scan — dialog suppressed when muted** — same setup but pre-set
   `session_flags.set_flag(CLAMAV_MUTE_KEY)`; assert `show_clamav_results_dialog` NOT called;
   clear flag in teardown
9. **Server-list finalize — dialog shown** — build minimal `ServerListWindowBatchStatusMixin` stub;
   set up `active_jobs` with an extract job whose `options["clamav_config"]` has `enabled=True`;
   inject results with `"clamav": {"enabled": True, ...}`; patch `show_clamav_results_dialog`; call
   `_finalize_batch_job`; assert dialog called once with `wait=False, modal=False`
10. **Server-list finalize — dialog suppressed when muted** — same but pre-set mute flag; assert
    `show_clamav_results_dialog` NOT called; clear flag in teardown

**Note:** `wait=True/modal=True` behaviour cannot be fully exercised headlessly; the invocation
tests (7–10) are the primary wiring gate. The fail-safe `try/except` in `_maybe_show_clamav_dialog`
ensures UI errors never break the surrounding extract flow.

---

## Validation run

```bash
python3 -m py_compile \
  gui/components/clamav_results_dialog.py \
  gui/utils/session_flags.py \
  gui/components/dashboard.py \
  gui/components/server_list_window/actions/batch.py \
  gui/components/server_list_window/actions/batch_status.py

xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_clamav_results_dialog.py -q

# Regression
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -q
```

## HI test needed?
Yes.
1. Enable ClamAV in `conf/config.json`.
2. Run post-scan bulk extract (dashboard) on a target with files — confirm ClamAV dialog appears after batch summary.
3. Click "Mute until restart" — run another extract in same session — confirm dialog suppressed.
4. Restart app — run extract again — confirm dialog reappears.
5. Repeat steps 2–3 from server-list batch extract path.
