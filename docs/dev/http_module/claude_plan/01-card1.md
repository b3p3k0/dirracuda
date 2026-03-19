# Card 1 Implementation Plan: Dashboard + HTTP Scan Dialog Entry

Status: approved, ready for implementation
Date: 2026-03-19

---

## Context

Dashboard already supports:

- `Start SMB Scan`
- `Start FTP Scan`
- one-active-scan lock behavior
- unified `Servers` browser entry

Card 1 adds an HTTP launch entry with dialog parity while preserving existing scan-state behavior.

---

## Scope

1. Add `Start HTTP Scan` button + handler in dashboard header.
2. Add `http_scan_dialog.py` based on `ftp_scan_dialog.py` patterns.
3. Persist HTTP dialog settings (in-session + restart) via settings manager.
4. Keep SMB/FTP behavior unchanged.
5. Wire launch callback shape for future `scan_manager.start_http_scan()` integration (Card 2).

Out of scope:

1. HTTP CLI/backend execution.
2. HTTP DB schema/persistence writes.

---

## Proposed Design

1. `dashboard.py`
   - add `self.http_scan_button` in header, next to SMB/FTP scan buttons
   - add `_handle_http_scan_button_click()`
   - add `_start_http_scan(scan_options)` stub that currently warns "HTTP scan backend not yet implemented" if Card 2 path is absent
   - keep shared state machine and external lock checks unchanged
2. `http_scan_dialog.py`
   - copy FTP dialog structure and keep layout consistency
   - include country/regions, max results, API key override, concurrency, timeouts, verbose, and template persistence
   - support both HTTP and HTTPS verification toggles in options payload (locked decision)
   - include TLS verification toggle for HTTPS (`allow_insecure_tls` default True)
3. button state updates
   - enable HTTP button only when `scan_button_state == "idle"`
   - disable during scanning/stopping/retry/error/external lock

---

## Touch Targets

1. `gui/components/dashboard.py`
2. `gui/components/http_scan_dialog.py` (new)
3. optional tiny import/export adjustments in `gui/components/__init__.py` if needed

---

## Manual Regression Checklist

1. SMB scan start/stop unchanged.
2. FTP scan start/stop unchanged.
3. HTTP button visible and follows same enable/disable rules as FTP button.
4. HTTP dialog opens, validates inputs, and persists values after close/reopen + app restart.
5. TLS toggle defaults to insecure-allowed and persists correctly across reopen/restart.
6. External lock disables all scan launch buttons.

---

## Claude Prompt (Copy/Paste)

```text
Implement Card 1 from docs/dev/http_module/HTTP_PHASE_TASK_CARDS.md.

Context:
- Dashboard already has SMB + FTP scan buttons and unified server browser.
- We are adding HTTP scan entry with dialog parity first; backend execution comes in Card 2.

Requirements:
1) Add "Start HTTP Scan" button in gui/components/dashboard.py next to SMB/FTP buttons.
2) Add new gui/components/http_scan_dialog.py modeled after ftp_scan_dialog.py:
   - same general look/feel and settings persistence behavior
   - includes options for country/region, max results, API key override, concurrency/timeouts, verbose
   - include HTTP+HTTPS verification toggles in returned scan options
   - include HTTPS TLS verification toggle in returned scan options:
     allow_insecure_tls: bool (default True)
3) Add dashboard handlers:
   - _handle_http_scan_button_click()
   - _start_http_scan(scan_options)
4) Preserve shared scan lock/state behavior:
   - HTTP button enabled only in idle
   - disabled for scanning/stopping/retry/error/disabled_external
5) Do not implement backend HTTP execution yet; if no HTTP scan-manager route exists, show clear placeholder message.

Constraints:
- Preserve SMB and FTP behavior exactly.
- Minimal focused edits; no broad refactors.

Deliver:
- changed files
- concise diff summary
- automated checks run
- manual regression checklist results
- explicit notes on assumptions or follow-up needed for Card 2
```
