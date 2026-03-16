# Card 1 Implementation Plan: Dashboard Scan Split

Status: approved, ready for implementation.

---

## Context

SMBSeek is adding FTP as a second protocol path. Card 1 introduces the minimum UI change to satisfy the Phase 1 gate: a second scan entry button on the dashboard for FTP, with the existing SMB button relabeled. All SMB lifecycle behavior is unchanged. The FTP button routes to a placeholder only. No protocol ownership tracking, no FTP stop/retry logic, no backend wiring.

---

## 1. Proposed Design

The existing single-button state machine is preserved entirely. Changes:

- **Relabel SMB button** "🔍 Start SMB Scan" — in both `_build_header_section()` (initial render) AND `_set_button_to_start()` (state reset after any scan). Both locations must be updated or the label reverts after the first scan cycle.
- **Add `self.ftp_scan_button = None`** in `__init__` alongside existing scan state vars.
- **Add `self.ftp_scan_button`** widget in `_build_header_section()`, placed immediately right of the SMB button.
- **FTP button rule:** enabled only when `scan_button_state == "idle"`. Disabled for every other state.
- **FTP click action:** re-checks external scan lock for consistency, then shows placeholder messagebox. No state transition, no scan_manager interaction.
- **No `active_scan_protocol` variable.** No FTP-specific stop/retry/error ownership.
- **Stop/retry/error behavior:** bound entirely to the existing SMB button. Unchanged.

---

## 2. File/Function Change Plan

### File: `gui/components/dashboard.py` only

No changes to `scan_manager.py` or `backend_interface/interface.py`.

---

### `__init__` — add explicit None init (~line 148, after existing scan state vars)

Add:
```python
self.ftp_scan_button = None
```

Reason: Explicit None init is clearer than `hasattr` checks. Guards in `_update_scan_button_state` use `if self.ftp_scan_button is not None:`.

---

### `_build_header_section()` (~lines 295-358)

Changes:
1. Change `self.scan_button` initial text: `"🔍 Start Scan"` → `"🔍 Start SMB Scan"`
2. Add FTP button immediately after SMB button pack:

```python
self.ftp_scan_button = tk.Button(
    actions_frame,
    text="📡 Start FTP Scan",
    command=self._handle_ftp_scan_button_click
)
self.theme.apply_to_widget(self.ftp_scan_button, "button_primary")
self.ftp_scan_button.pack(side=tk.LEFT, padx=(0, 5))
```

SMB regression: label changes cosmetically. Handler (`_handle_scan_button_click`) and all logic unchanged.

---

### `_set_button_to_start()` (line 1823-1829) — BLOCKING FIX

Change hardcoded label at line 1826:
```python
# Before:
text="🔍 Start Scan"
# After:
text="🔍 Start SMB Scan"
```

Reason: Called by `_update_scan_button_state("idle")` after every scan cycle. Without this, the label reverts after the first scan.

---

### `_update_scan_button_state()` (~lines 1802-1821)

Add FTP button enable/disable to each branch, guarded by `if self.ftp_scan_button is not None:`:

- `idle`: `self.ftp_scan_button.config(state=tk.NORMAL)`
- `scanning`: `self.ftp_scan_button.config(state=tk.DISABLED)`
- `stopping`: `self.ftp_scan_button.config(state=tk.DISABLED)`
- `retry`: `self.ftp_scan_button.config(state=tk.DISABLED)`
- `error`: `self.ftp_scan_button.config(state=tk.DISABLED)`
- `disabled_external`: `self.ftp_scan_button.config(state=tk.DISABLED)`

SMB regression: additions only. Existing SMB helper calls in each branch are untouched.

---

### Add `_handle_ftp_scan_button_click()` (new method)

Placement: immediately after `_handle_scan_button_click`.

```python
def _handle_ftp_scan_button_click(self) -> None:
    """Handle FTP scan button click. Phase 2+ will wire real backend."""
    if self.scan_button_state == "idle":
        self._check_external_scans()        # mirror SMB handler: re-verify lock state
        if self.scan_button_state == "idle":    # still idle after check
            self._start_ftp_scan_placeholder()
    # Non-idle states: button is disabled; defensive no-op if somehow reached.
```

Reason: Mirrors SMB handler's pre-action lock check. If lock state changed between refresh ticks, FTP handler disables itself before acting.

---

### Add `_start_ftp_scan_placeholder()` (new method)

Placement: immediately after `_handle_ftp_scan_button_click`.

```python
def _start_ftp_scan_placeholder(self) -> None:
    """Placeholder for FTP scan launch. Implemented in Card 2."""
    messagebox.showinfo(
        "FTP Scan",
        "FTP scanning is not yet implemented.\n"
        "This feature will be available in a future update.",
        parent=self.parent
    )
```

`parent=self.parent` ensures correct focus and modality in VM environments.
No state transitions. No scan_manager calls. No lock acquisition.

---

### Not changed

- `_handle_scan_button_click()` — no rename, no logic change
- `_check_external_scans()` — already calls `_update_scan_button_state`, which now handles FTP button
- `_monitor_scan_completion()` — already calls `_update_scan_button_state("idle")` on completion

---

## 3. State Behavior Table

| State             | SMB Button Text / State                          | FTP Button Text / State                |
|-------------------|--------------------------------------------------|----------------------------------------|
| idle              | "🔍 Start SMB Scan" — NORMAL (primary)           | "📡 Start FTP Scan" — NORMAL (primary) |
| scanning          | "⬛ Stop Scan" — NORMAL (danger)                  | "📡 Start FTP Scan" — DISABLED         |
| stopping          | "⏳ Stopping…" — DISABLED (warning)              | "📡 Start FTP Scan" — DISABLED         |
| retry             | "⏹ Stop (retry)" — NORMAL (warning)             | "📡 Start FTP Scan" — DISABLED         |
| error             | "⬛ Stop Failed" — NORMAL (danger)                | "📡 Start FTP Scan" — DISABLED         |
| disabled_external | "🔍 Scan Running" — DISABLED (button_disabled)   | "📡 Start FTP Scan" — DISABLED         |

FTP button text does not change across states — only `state` attribute (NORMAL/DISABLED) changes.

---

## 4. Regression Plan

### SMB Baseline

1. **Idle state:** Both buttons visible and enabled. SMB reads "🔍 Start SMB Scan". FTP reads "📡 Start FTP Scan".
2. **Start SMB scan:** Dialog appears → confirm → SMB button → "⬛ Stop Scan". FTP button disabled.
3. **Stop SMB scan:** Confirmation → Stop Now → SMB → "⏳ Stopping…". FTP remains disabled.
4. **Stop timeout / retry:** 10s timeout → SMB → "⏹ Stop (retry)" (enabled). FTP still disabled.
5. **Scan completes normally:** Both return to idle. **SMB must read "🔍 Start SMB Scan" — not old "🔍 Start Scan".** Results dialog as normal. (Verifies `_set_button_to_start` fix.)
6. **Cancel dialog (no scan started):** Open dialog → Cancel → state stays idle. Both buttons remain enabled. No lock acquired.
7. **Start failure:** Simulate invalid backend path → error messagebox → SMB returns to idle as "🔍 Start SMB Scan". FTP enabled. No stale lock.

### FTP Placeholder

8. **FTP click in idle:** Click FTP → `_check_external_scans()` runs → no lock → placeholder messagebox with correct parent → dismiss → both idle, no state change.
9. **FTP click during external lock (race):** Lock appears between refresh and click → `_check_external_scans()` in FTP handler detects it → exits early. Both buttons disabled.
10. **FTP button during SMB scan:** FTP button is DISABLED — no response. Verify `state=DISABLED`.

### External Lock

11. **Active external lock:** Inject `.scan_lock` with live foreign PID → both buttons disabled. Status bar shows PID.
12. **Stale lock recovery:** Inject `.scan_lock` with dead PID → next `_check_external_scans` cycle → stale lock cleaned → both buttons idle with correct labels.

### Initialization Safety

13. **Widget guard:** Any `_update_scan_button_state` call before `_build_header_section` completes must not raise `AttributeError`. Guard: `self.ftp_scan_button is not None` (initialized to None in `__init__`).

---

## 5. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `_set_button_to_start()` reverts label after scan cycle | Confirmed | Update line 1826 first in patch sequence |
| `_update_scan_button_state` called before widget exists | Low-medium | `self.ftp_scan_button = None` in `__init__`; guard with `is not None` |
| Placeholder messagebox loses focus in VM | Low | `parent=self.parent` in `showinfo` call |
| FTP handler acts on stale lock state | Low | `_check_external_scans()` inside FTP handler before acting |
| SMB button label change breaks code reading `scan_button["text"]` | Very Low | Grep for string before committing |
| FTP click reaches handler in non-idle state | Very Low | Button DISABLED; handler has defensive no-op |

---

## 6. Out-of-Scope Confirmation

Card 1 will NOT implement:
- `active_scan_protocol` or any protocol-ownership tracking
- FTP-specific stop, retry, or error states
- FTP backend CLI or any subprocess invocation
- FTP workflow, discovery, auth, or scan_manager routing
- FTP database schema or persistence
- Changes to `scan_manager.py` or `backend_interface/interface.py`
- FTP probe snapshots, file browser, or progress parsing
- Changes to the SMB scan options dialog
- Renaming `_handle_scan_button_click`

---

## 7. Ready-to-Implement Patch Sequence

1. **`_set_button_to_start()` (line 1826):** Change `text="🔍 Start Scan"` → `text="🔍 Start SMB Scan"`. Fix blocking regression first.

2. **`__init__` (~line 148):** Add `self.ftp_scan_button = None` after existing scan state vars.

3. **`_build_header_section()`:** Change SMB button initial text to "🔍 Start SMB Scan". Add `self.ftp_scan_button` widget with label, command, theme, pack.

4. **`_update_scan_button_state()`:** Add `if self.ftp_scan_button is not None:` guard with NORMAL (idle) or DISABLED (all other branches).

5. **Add `_handle_ftp_scan_button_click()`** immediately after `_handle_scan_button_click`.

6. **Add `_start_ftp_scan_placeholder()`** immediately after `_handle_ftp_scan_button_click`.

7. **Grep check:** Search `dashboard.py` for `self.scan_button["text"]` or the string `"🔍 Start Scan"` — confirm no other references.

8. **Manual regression pass** per §4 (all 13 checks) before committing.

---

## Critical Files

| File | Change scope |
|------|-------------|
| `gui/components/dashboard.py` | All changes |
| `gui/utils/scan_manager.py` | No changes |
| `gui/utils/backend_interface/interface.py` | No changes |

## Function Summary

| Function | Type | Change |
|----------|------|--------|
| `__init__` | Modified | Add `self.ftp_scan_button = None` |
| `_build_header_section()` | Modified | Relabel SMB button; add FTP widget |
| `_set_button_to_start()` | Modified | Fix label text (blocking) |
| `_update_scan_button_state()` | Modified | Add guarded FTP enable/disable per branch |
| `_handle_ftp_scan_button_click()` | New | FTP click handler with lock re-check |
| `_start_ftp_scan_placeholder()` | New | Messagebox with parent; no state change |
