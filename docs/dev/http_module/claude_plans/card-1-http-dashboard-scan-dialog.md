# Card 1 Implementation Plan: Dashboard + HTTP Scan Dialog Entry

**Status:** approved, ready for implementation
**Date:** 2026-03-19
**Depends on:** none (first card)
**Destination file:** `docs/dev/http_module/claude_plan/01-card1.md`

---

## 1. Context and Scope

### Why this card exists

The dashboard currently has two scan launch buttons:
- `🔍 Start SMB Scan` → opens `ScanDialog`, locks scan state machine, calls `scan_manager.start_scan()`
- `📡 Start FTP Scan` → opens `FtpScanDialog`, same lock, calls `scan_manager.start_ftp_scan()`

Card 1 adds a third button for HTTP following the exact same structural pattern. No HTTP backend is implemented yet; the dialog collects and validates scan options for downstream use in Card 2. The stub launch path shows a clear informational message and does **not** corrupt scan state.

### What must remain untouched

- SMB scan start/stop/retry/error/stop-timeout lifecycle
- FTP scan start/stop/retry/error lifecycle
- External lock detection (`_check_external_scans()`)
- `_update_scan_button_state()` effect on SMB and FTP buttons

### Locked decisions honored

| Decision | Implementation |
|---|---|
| HTTP + HTTPS both verified | `verify_http: True`, `verify_https: True` are **fixed flags** in scan_options — not UI controls; both protocols are always checked |
| Allow insecure TLS by default | `allow_insecure_tls` BooleanVar default `True` in dialog |
| Dialog toggle for TLS | Single checkbox: "Allow insecure HTTPS (self-signed / untrusted certs)" — this is the **only** TLS-related toggle in Card 1 |
| 0-count HTTP hosts persist | No filter gate at Card 1; persistence is Card 3 concern |
| One-level probe recursion compatibility | `bulk_probe_enabled` key present in scan_options (default False) |

---

## 2. Pre-Revision Reality Check

Run these before touching any file. Record actual output.

```bash
# 1. Confirm repo and working directory
cd /home/kevin/DEV/smbseek-smb && pwd

# 2. Check git status — must be clean or known-dirty
git status --short

# 3. Recent commits for context
git log -n 5 --oneline

# 4. Baseline test run — record pass/fail counts before editing
# set -o pipefail ensures pipe failure propagates; tail is display-only
set -o pipefail && xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short | tail -40

# 5. Confirm FTP button lines in dashboard (reference anchors for HTTP additions)
grep -n "ftp_scan_button\|_handle_ftp_scan\|_start_ftp_scan" gui/components/dashboard.py

# 6. Confirm no HTTP hook already exists
grep -rn "http_scan_button\|http_scan_dialog\|start_http_scan" gui/ 2>/dev/null

# 7. Confirm scan_manager has start_ftp_scan but not start_http_scan
grep -n "def start_" gui/utils/scan_manager.py
```

**What to capture:** test pass count, line numbers for FTP button creation, any unexpected HTTP references.

---

## 3. Exact File/Function Touch List

### 3.1 `gui/components/dashboard.py` — modify

| Location | Change | Why |
|---|---|---|
| Import block (~line 31) | Add `from gui.components.http_scan_dialog import show_http_scan_dialog` | Wire dialog factory |
| `__init__` state block (~line 155) | Add `self.http_scan_button = None` | Consistent with `ftp_scan_button` nil-init |
| Button creation in actions frame (~line 340) | Add `self.http_scan_button` widget after FTP button | Visible HTTP launch control |
| `_update_scan_button_state()` (~line 1980) | Add `self.http_scan_button.config(state=...)` in all six branches | Maintain disable/enable parity |
| New method: `_handle_http_scan_button_click()` | Mirror `_handle_ftp_scan_button_click()` exactly | Route click → dialog |
| New method: `_start_http_scan(scan_options)` | Run idle guard + `_check_external_scans()`, then show info messagebox; **do not call scan_manager** | Card 1 dashboard-only placeholder |

**`_start_http_scan()` contract (Card 1):** Call `_check_external_scans()` and confirm `scan_button_state == "idle"`. Then show `messagebox.showinfo(...)`: "HTTP scanning backend is not yet implemented (Card 2). Your scan options have been captured." Return without calling scan_manager, without acquiring a lock, and without changing `scan_button_state`.

### 3.2 `gui/components/http_scan_dialog.py` — new file

Model: `ftp_scan_dialog.py` (1308 lines). Structural parity required.

Key differences from FTP dialog:

| FTP field | HTTP equivalent | Notes |
|---|---|---|
| `auth_timeout` | *(removed)* | HTTP has no auth step |
| `listing_timeout` | `request_timeout` | HTTP page request timeout |
| *(absent)* | `allow_insecure_tls` BooleanVar (default `True`) | Locked decision — new checkbox |
| Settings key prefix `ftp_scan_dialog.*` | `http_scan_dialog.*` | No collision with FTP settings |
| Template save/load (TemplateStore) | **Omitted in Card 1** — see note below | Risk of template namespace collision |

> **Template omission — HI sign-off required before implementation begins.**
> FTP dialog wires `TemplateStore` for scan template save/load/delete (ftp_scan_dialog.py ~line 430).
> Card 1 HTTP dialog will **not** include template wiring. This is an intentional parity gap.
> Reason: `TemplateStore` (template_store.py ~line 39) does not currently have a per-protocol namespace;
> adding HTTP templates without namespace isolation risks silently cross-contaminating FTP template lists.
> Template support for HTTP will be added in a later card once namespace isolation is confirmed or added.
> **HI must acknowledge this gap before implementation proceeds.**

**Public API to expose:**
```python
class HttpScanDialog:
    def __init__(self, parent, config_path, scan_start_callback,
                 settings_manager=None, config_editor_callback=None): ...
    def show(self) -> Optional[str]: ...  # returns "start" | "cancel" | None

def show_http_scan_dialog(parent, config_path, scan_start_callback,
                          settings_manager=None, config_editor_callback=None) -> None: ...
```

Reuse: `from gui.components.scan_dialog import ScanDialog; REGIONS = ScanDialog.REGIONS`

### 3.3 `gui/utils/scan_manager.py` — optional minimal extension

> **Risk note (LOW):** Adding a stub method to `scan_manager.py` in Card 1 carries small but non-zero regression risk
> since scan_manager is shared by SMB and FTP paths. A safer alternative is to keep the placeholder behavior
> entirely in `dashboard._start_http_scan()`: check `hasattr(self.scan_manager, 'start_http_scan')` and
> if absent, show the info messagebox without calling scan_manager at all.
>
> **Preferred approach (lower risk):** Dashboard-only placeholder — no scan_manager touch in Card 1.
> Card 2 adds `start_http_scan()` to scan_manager alongside the real worker.
>
> **If HI prefers the stub in scan_manager now** (for cleaner interface parity), add only:

```python
def start_http_scan(
    self,
    scan_options: dict,
    backend_path: str,
    progress_callback: Callable,
    log_callback: Optional[Callable[[str], None]] = None,
    config_path: Optional[str] = None,
) -> bool:
    """
    HTTP scan backend stub — Card 1 placeholder, replaced in Card 2.
    Does not acquire lock, change is_scanning, or emit progress events.
    """
    _logger.info(
        "HTTP scan backend not yet implemented (Card 2+). "
        "Scan options captured: country=%s, allow_insecure_tls=%s",
        scan_options.get("country"),
        scan_options.get("allow_insecure_tls"),
    )
    return False
```

**Default for implementation:** use dashboard-only placeholder (no scan_manager edit) unless HI explicitly approves the stub approach.

---

## 4. UI/State Behavior Table

All three scan buttons follow unified state machine. `http_scan_button` tracks `ftp_scan_button` in lockstep.

| `scan_button_state` | SMB button | FTP button | HTTP button | User action on HTTP button |
|---|---|---|---|---|
| `idle` | NORMAL ("Start SMB Scan") | NORMAL | NORMAL | Opens `HttpScanDialog` |
| `scanning` | NORMAL ("⬛ Stop Scan") | DISABLED | DISABLED | No-op (disabled) |
| `stopping` | DISABLED ("⏳ Stopping...") | DISABLED | DISABLED | No-op (disabled) |
| `retry` | NORMAL ("⏹ Stop (retry)") | DISABLED | DISABLED | No-op (disabled) |
| `error` | NORMAL ("⬛ Stop Failed") | DISABLED | DISABLED | No-op (disabled) |
| `disabled_external` | DISABLED | DISABLED | DISABLED | No-op (disabled) |

**Note:** Any active scan — SMB or FTP or (future) HTTP — disables both other protocol buttons. This is already implemented for SMB/FTP; HTTP button simply joins the pattern.

---

## 5. Dialog Contract

`HttpScanDialog._build_scan_options()` return value (passed to `scan_start_callback`):

```python
{
    # Geographic scope
    "country": str | None,          # comma-separated ISO codes or None for global

    # Shodan query
    "max_shodan_results": int,       # 1–1000
    "api_key_override": str | None,
    "custom_filters": str | None,    # appended to Shodan query string

    # Concurrency
    "discovery_max_concurrent_hosts": int,  # 1–256

    # Timeouts (seconds, 1–300)
    "connect_timeout": int,          # TCP connection timeout
    "request_timeout": int,          # HTTP request read timeout

    # HTTP/HTTPS verification — FIXED FLAGS, not UI toggles.
    # Both protocols are always attempted; Card 4 consumer reads these for routing.
    "verify_http": True,
    "verify_https": True,

    # TLS behavior — ONLY user-visible toggle related to TLS (locked decision).
    "allow_insecure_tls": bool,      # default True; controls urllib3 verify= flag

    # Logging
    "verbose": bool,

    # Probe placeholder (Card 5 compatibility)
    "bulk_probe_enabled": bool,       # default False; no behavior in Card 1
}
```

**Settings persistence keys** (under `gui_settings.json`):
- `http_scan_dialog.max_shodan_results`
- `http_scan_dialog.api_key_override`
- `http_scan_dialog.custom_filters`
- `http_scan_dialog.country_code`
- `http_scan_dialog.discovery_max_concurrent_hosts`
- `http_scan_dialog.connect_timeout`
- `http_scan_dialog.request_timeout`
- `http_scan_dialog.allow_insecure_tls`
- `http_scan_dialog.verbose`
- `http_scan_dialog.bulk_probe_enabled`
- `http_scan_dialog.region_africa` … `region_south_america`

---

## 6. Regression Checklist

### Gate A — Automated

```bash
# Run full test suite after Card 1 changes.
# set -o pipefail ensures pytest exit code is not swallowed by tail.
set -o pipefail && xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short | tail -40
```

Expected: same pass count as baseline. No new failures. No import errors.

```bash
# Verify imports load cleanly
python -c "from gui.components.http_scan_dialog import show_http_scan_dialog; print('OK')"
python -c "from gui.components.dashboard import Dashboard; print('OK')"
# Expected output depends on which variant was implemented:
#   dashboard-only default  → False
#   stub-in-scan_manager    → True  (only if HI approved this variant)
python -c "from gui.utils.scan_manager import get_scan_manager; sm=get_scan_manager(); print(hasattr(sm, 'start_http_scan'))"
```

### Gate B — Manual (HI)

```text
1. [ ] Launch xsmbseek (or xvfb-run -a ./xsmbseek --mock)
       Confirm: three scan buttons visible in header — SMB, FTP, HTTP

2. [ ] Click "Start HTTP Scan"
       Confirm: HttpScanDialog opens
       Confirm: TLS toggle visible, defaults to checked (allow insecure)
       Confirm: country/region, max results, API key, concurrency, timeouts, verbose all present

3. [ ] Fill in country "US", set max results to 50, uncheck TLS toggle, click Start
       Confirm: messagebox shows "HTTP scanning backend is not yet implemented"
       Confirm: dialog closes
       Confirm: scan_button_state remains idle (all three buttons still enabled)
       Confirm: NO lock file created
         # Lock file path is managed by scan_manager; get the actual path before checking:
         python -c "from gui.utils.scan_manager import get_scan_manager; sm=get_scan_manager(); print(sm.lock_file)"
         # Then verify that path does not exist after the HTTP dialog Start click

4. [ ] Reopen HTTP dialog
       Confirm: country "US", max results 50, TLS unchecked all persisted

5. [ ] Restart app, reopen HTTP dialog
       Confirm: settings persisted across restart

6. [ ] Start an SMB scan
       Confirm: FTP and HTTP buttons both DISABLED during scan
       Stop SMB scan
       Confirm: FTP and HTTP buttons both re-enable after scan completes

7. [ ] Start an FTP scan
       Confirm: SMB and HTTP buttons both DISABLED during scan
       Stop FTP scan
       Confirm: SMB and HTTP buttons both re-enable after scan completes
```

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

---

## 7. Risks and Assumptions

| Item | Detail |
|---|---|
| **Risk: FTP button disable regression** | `_update_scan_button_state()` already uses `if self.ftp_scan_button is not None:` guard; HTTP button must use identical guard pattern to avoid AttributeError on init before button creation |
| **Risk: Template store collision** | FTP uses `TemplateStore` with implicit key namespacing. If HTTP templates use same TemplateStore instance without namespace, FTP templates will intermingle. **Mitigation:** Omit template save/load in Card 1 HTTP dialog (add in later card), OR add `protocol="http"` namespace param if TemplateStore supports it. Investigate before implementing. |
| **Risk: `gui_settings.json` key collision** | FTP keys prefixed `ftp_scan_dialog.*`; HTTP uses `http_scan_dialog.*`. No collision expected, but verify `settings_manager.get_setting()` default fallback doesn't cross-reference FTP keys. |
| **Assumption: `show_ftp_scan_dialog` signature is stable reference** | `HttpScanDialog.__init__` and `show_http_scan_dialog()` will use identical signature shape. If `show_ftp_scan_dialog` was changed since baseline, re-read before implementing. |
| **Assumption: `_build_actions_frame` or equivalent** | The exact function creating the SMB/FTP buttons in dashboard was inferred from exploration. Verify actual line range before inserting HTTP button. |
| **Assumption: scan lock protocol string** | `create_lock_file(country, "ftp")` uses `"ftp"` as protocol tag. Card 1 stub never calls `create_lock_file`, so no new protocol string needed until Card 2. |
| **Assumption: no `gui/components/__init__.py` re-export needed** | FTP dialog is imported directly in dashboard without going through `__init__.py`. HTTP dialog should follow same pattern. Verify. |

---

## 8. Out-of-Scope Confirmation

The following are explicitly out of scope for Card 1:

- HTTP CLI entry point (`httpseek`) — Card 2
- `scan_manager._http_scan_worker()` — Card 2
- `backend_interface` HTTP routing — Card 2
- `http_servers`, `http_access` DB tables — Card 3
- HTTP verification / Shodan query logic — Card 4
- HTTP probe snapshot / browser — Card 5
- Any modification to `shared/workflow.py`, `commands/discover/`, `commands/access/`, `shared/ftp_workflow.py` — not this card

---

## 9. Copy/Paste Implementation Prompt

```text
Implement Card 1 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.

Reference plan: docs/dev/http_module/claude_plan/01-card1.md

Context:
- Dashboard (gui/components/dashboard.py) already has SMB + FTP scan buttons with a
  shared scan-state machine and external lock detection.
- FTP scan dialog (gui/components/ftp_scan_dialog.py) is the structural model.
- No HTTP backend exists yet; the launch stub must not corrupt scan state.

Requirements:

1. dashboard.py changes:
   a. Import show_http_scan_dialog from gui.components.http_scan_dialog
   b. Add self.http_scan_button = None in __init__ state block (same location as ftp_scan_button)
   c. Create HTTP button widget after FTP button in actions frame
   d. Extend _update_scan_button_state() to disable/enable self.http_scan_button in all
      six states: idle, scanning, stopping, retry, error, disabled_external
      (Use identical guard: if self.http_scan_button is not None:)
   e. Add _handle_http_scan_button_click() mirroring _handle_ftp_scan_button_click() exactly,
      calling show_http_scan_dialog with scan_start_callback=self._start_http_scan
   f. Add _start_http_scan(scan_options) — dashboard-only placeholder (default path):
      - Do NOT call scan_manager at all in Card 1
      - Show messagebox.showinfo with:
        "HTTP scanning backend is not yet implemented.\n
         Your scan options have been captured for use when the backend is ready (Card 2)."
      - Do NOT change scan_button_state; scan remains idle
      - Pattern: check _check_external_scans() + idle guard first (same as _start_ftp_scan),
        then show info message and return — no further action

2. New gui/components/http_scan_dialog.py:
   - Model after ftp_scan_dialog.py structure exactly
   - Reuse REGIONS = ScanDialog.REGIONS (no forked copy)
   - Include all FTP dialog fields EXCEPT: remove auth_timeout; rename listing_timeout → request_timeout
   - Add allow_insecure_tls BooleanVar (default True)
     Label: "Allow insecure HTTPS (self-signed / untrusted certs)"
   - _build_scan_options() returns dict with keys defined in plan section 5
     (include verify_http=True, verify_https=True, allow_insecure_tls=bool)
   - Settings persistence keys prefixed http_scan_dialog.* (not ftp_scan_dialog.*)
   - Omit template save/load in Card 1 (no TemplateStore wiring; add in future card)
   - Expose show_http_scan_dialog() factory function with identical signature to show_ftp_scan_dialog()

3. gui/utils/scan_manager.py:
   - DO NOT modify scan_manager in Card 1.
     _start_http_scan() in dashboard handles the stub entirely (see 1f above).
     scan_manager gains start_http_scan() in Card 2 alongside the real worker.

Constraints:
- Preserve SMB and FTP behavior exactly — no changes to existing code paths
- Minimal, focused edits; no broad refactors
- Guard all new button references with 'if self.http_scan_button is not None:'
- No new files other than http_scan_dialog.py

Deliver:
- changed files with line-level diff summary
- output of: python -c "from gui.components.http_scan_dialog import show_http_scan_dialog; print('OK')"
- output of: set -o pipefail && xvfb-run -a python -m pytest gui/tests/ shared/tests/ -v --tb=short | tail -20
- manual regression checklist from plan section 6 Gate B (mark each PASS/FAIL/PENDING)
- explicit notes on any assumptions made or follow-up items for Card 2
```
