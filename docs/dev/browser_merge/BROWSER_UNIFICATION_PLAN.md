# Browser Unification Plan

Date: 2026-03-23
Status: U1-U5 complete (2026-03-24); U6 planned (2026-03-24)

## 1) Problem Statement

Current runtime uses protocol-specific browser implementations:
- `shared/smb_browser.py` -> `SMBNavigator`
- `shared/ftp_browser.py` -> `FtpNavigator`
- `shared/http_browser.py` -> `HttpNavigator`
- `gui/components/file_browser_window.py` (SMB)
- `gui/components/ftp_browser_window.py` (FTP)
- `gui/components/http_browser_window.py` (HTTP)

Routing branches per protocol in server-list actions/details, creating duplicated UI logic and repeated maintenance burden.

## 2) Goals

1. Provide one unified browser UI/controller for SMB, FTP, and HTTP.
2. Preserve existing behavior outside browser flow.
3. Keep protocol isolation and row identity semantics unchanged.
4. Minimize regression risk via incremental migration.
5. Achieve banner-panel parity across all three modes (SMB/FTP/HTTP), with SMB banner text sourced from Shodan metadata when available.

## 3) Non-Goals

1. No protocol storage/schema redesign.
2. No change to delete semantics or host-row identity.
3. No broad server-list rewrite.
4. No deep crawler/ranking features.

## 4) Hard Constraints

1. Surgical, reversible changes only.
2. Runtime behavior gate is required (not tests alone).
3. Legacy compatibility remains first-class.
4. Root-cause fixes over symptom patches.
5. Targeted validation first; expand only when risk warrants.
6. No commits unless HI says `commit`.

## 5) Current-State Analysis Summary

**Final state (2026-03-24 — all cards delivered):**

All three protocol browser windows (SMB, FTP, HTTP) now live in
`gui/components/unified_browser_window.py`:
- `SmbBrowserWindow(UnifiedBrowserCore)` — share selector, SMB path semantics,
  Shodan banner panel, quarantine download, extraction callback
- `FtpBrowserWindow(UnifiedBrowserCore)` — FTP anonymous browse/download
- `HttpBrowserWindow(UnifiedBrowserCore)` — HTTP directory listing/download

Protocol-aware probe/cache dispatch is in `gui/utils/probe_cache_dispatch.py`.

Legacy standalone windows deleted: `ftp_browser_window.py`, `http_browser_window.py`,
`file_browser_window.py`.

Browse launch routing (batch_operations.py, details.py) uses the unified
entrypoints `open_ftp_http_browser()` and `open_smb_browser()`.

## 6) Target Architecture

Use one UI window + protocol adapters.

### 6.1 Unified Browser Components

1. `gui/components/unified_browser_window.py`
   - Shared UI/state machine for list/view/download/cancel/probe snapshot rendering.
2. `gui/components/browser_protocols.py`
   - Adapter interfaces + protocol capability descriptors.
3. `gui/components/browser_factory.py`
   - Builds protocol adapter from host row context and config.

### 6.2 Adapter Contract (conceptual)

`BrowserAdapter` responsibilities:
1. `list_dir(path) -> ListResult`
2. `read_file(path, max_bytes) -> ReadResult`
3. `download_file(path, dest_dir, progress_cb)`
4. `cancel()`
5. Optional lifecycle hooks for stateful protocols:
   - `connect()` / `disconnect()`
6. Path semantics helper:
   - normalize display path vs wire path
7. Probe helpers:
   - load cached snapshot
   - run probe snapshot
   - cache-path lookup

### 6.3 Capability Flags

Per protocol:
1. has_share_selector
2. has_banner_panel
3. supports_scheme_display
4. supports_credentials
5. path_style (`windows` or `posix`)

Banner parity rule:
1. FTP and HTTP already expose banner panels.
2. SMB must expose the same banner panel pattern.
3. SMB banner value should be derived from stored Shodan metadata (best-effort parse) with a safe placeholder fallback when unavailable.

## 7) Execution Strategy (Low-Risk)

Phase U1: unify FTP + HTTP UI first, keep SMB unchanged.  
Phase U2: route FTP + HTTP through unified window; keep wrappers for compatibility.  
Phase U3: integrate SMB adapter into unified window with SMB-only controls and SMB banner parity sourced from Shodan metadata.  
Phase U4: collapse probe/cache duplication behind protocol-aware helper.  
Phase U5: remove legacy browser window classes after parity confidence.

Rationale:
- FTP+HTTP are already structurally similar, giving high confidence early wins.
- SMB enters later because its share/auth behavior is materially different.

## 8) Definition of Done

Unified browser migration is done when:
1. Browse/view/download works for SMB/FTP/HTTP via one primary window class.
2. Protocol-specific semantics remain correct (share routing, HTTP abs paths, FTP anon behavior).
3. Banner panel behavior is consistent across SMB/FTP/HTTP, with SMB showing Shodan-derived text or explicit fallback.
4. Existing action-routing tests pass after updates.
5. Manual Gate B confirms behavior parity for one host per protocol.
6. Legacy wrappers removed or marked deprecated with no active callsites.

## 9) Rollback Strategy

If a slice regresses runtime behavior:
1. Revert that slice only.
2. Keep prior protocol-specific windows in place.
3. Re-run targeted baseline commands and document failure cause.

## 10) Follow-On Scope: SMB Root-Level Share UX (U6)

Problem:
- SMB browsing still starts with a dedicated `Share:` dropdown while FTP/HTTP start at a root listing.
- This creates avoidable UI inconsistency even after unifying browser windows.

Goal:
- Make SMB browsing feel like FTP/HTTP by starting at a root-level listing and allowing drill-down from there.
- Represent accessible SMB shares as top-level directory rows (virtual host root), then browse inside shares.

Locked decisions (HI, 2026-03-24):
1. Remove SMB share dropdown entirely for UI uniformity.
2. Re-query accessible SMB shares from DB when SMB browser is launched.
3. If opening a share fails, keep user at SMB root and show non-fatal error/status.
4. Keep admin/hidden shares filtered by current existing behavior (no new exposure).
5. Keep alphabetical sorting behavior.
6. SMB path label should stay `share\\path` style (no full UNC requirement).

Execution notes:
1. Keep `shared/smb_browser.py` unchanged; implement this as UI/controller state handling.
2. Keep existing credential derivation and per-share credential override behavior.
3. Preserve extraction callback, quarantine path rules, and banner panel behavior.
4. Keep changes surgical and reversible (no schema changes).

Definition of done for U6:
1. Opening SMB browser shows share rows as top-level entries (no dropdown).
2. Double-clicking a share enters that share and lists its root directory.
3. `Up` from a share root returns to top-level share list.
4. File view/download/cancel continue to work inside shares.
5. Share entry failures do not crash or strand UI; user remains at root with clear error.
6. Targeted automation and manual Gate B pass with explicit PASS/FAIL labels.
