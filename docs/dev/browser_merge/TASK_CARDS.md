# Browser Merge Task Cards (Claude-Ready)

Use one card at a time unless HI explicitly approves combining cards.

---

## Card U1: Shared Browser Core for FTP+HTTP

Goal:
Create a unified browser window core used by FTP and HTTP without behavior changes.

Primary scope:
1. Add `gui/components/unified_browser_window.py` with shared UI/controller logic.
2. Add protocol adapter layer for FTP + HTTP only.
3. Keep existing `FtpBrowserWindow` and `HttpBrowserWindow` as thin wrappers or compatibility entrypoints.

Definition of done:
1. FTP and HTTP browse/view/download paths run through unified core.
2. Existing FTP/HTTP behavior unchanged for operators.
3. Existing route callers do not require broad edits.

Regression checks:
1. FTP browser tests pass.
2. HTTP browser tests pass.
3. Action routing tests involving FTP/HTTP browse still pass.

Out of scope:
1. SMB browser migration.
2. Probe/cache consolidation.

---

## Card U2: Route F/H Directly to Unified Browser

Goal:
Move runtime browse routing for host types `F` and `H` to unified entrypoint.

Primary scope:
1. Update browse launch branches in server-list action/details flows.
2. Ensure protocol-specific context (port/scheme/banner) still passed correctly.
3. Remove direct callsites to legacy FTP/HTTP window classes where safe.

Definition of done:
1. No active browse path instantiates protocol-specific FTP/HTTP windows directly.
2. UI behavior for F/H rows remains unchanged.

Regression checks:
1. Action routing tests updated/passing.
2. Manual browse open from both list and details pop-up for F and H rows.

Out of scope:
1. SMB browser migration.

---

## Card U3: SMB Adapter Integration

Goal:
Integrate SMB into unified browser while preserving share/credential behavior and adding SMB banner-panel parity.

Primary scope:
1. Add SMB adapter supporting share selection and SMB path semantics.
2. Surface SMB-only controls in unified window conditionally.
3. Route SMB browse launch to unified entrypoint.
4. Add SMB banner panel support aligned with FTP/HTTP UI pattern.
5. Populate SMB banner text from Shodan metadata (best-effort parse) with explicit fallback placeholder when unavailable.

Definition of done:
1. SMB browse/view/download parity maintained.
2. Share selector and per-share credentials still behave correctly.
3. Existing SMB extraction callback behavior remains intact.
4. SMB browser shows a banner panel consistent with FTP/HTTP presentation.
5. SMB banner content is sourced from stored Shodan metadata when present.

Regression checks:
1. Existing SMB browser tests updated/passing.
2. Manual SMB browse on host with multiple shares.
3. No regressions in FTP/HTTP browse flows.
4. Banner panel remains correct in FTP/HTTP modes.

Out of scope:
1. Probe/cache module merge (handled separately).

---

## Card U4: Probe/Cache Helper Consolidation

Goal:
Consolidate protocol-specific probe/cache utility duplication behind one protocol-aware helper layer.

Primary scope:
1. Create protocol-aware probe/cache helper API.
2. Keep serialized snapshot contract compatible with existing details renderer.
3. Preserve protocol-specific differences explicitly (e.g., HTTP error dict shape if still required).

Definition of done:
1. Unified browser uses one probe/cache interface.
2. Existing snapshot rendering remains correct for S/F/H rows.

Regression checks:
1. `test_ftp_probe.py`, `test_http_probe.py`, and related action tests pass.
2. Manual probe status update visible in server list/details.

Out of scope:
1. Changing snapshot schema unless HI explicitly approves.

---

## Card U5: Cleanup and Deletion of Legacy Browser Classes ✓ DONE (2026-03-24)

Goal:
Remove deprecated protocol-specific window modules after parity confidence.

Primary scope:
1. Remove/retire legacy window classes and dead imports.
2. Update docs/tests to reference unified browser entrypoint.
3. Ensure no stale routing branches remain.

Definition of done:
1. One primary browser window implementation remains.
2. No dead protocol-specific window callsites.
3. Regression suite and manual smoke checks pass.

Regression checks:
1. Targeted browser + action-routing tests.
2. One manual browse/view/download per protocol.

Delivery notes (U5.3 + U5.4):
- `SmbBrowserWindow(UnifiedBrowserCore)` added to `unified_browser_window.py`.
- `open_smb_browser()` updated to instantiate `SmbBrowserWindow` directly.
- `file_browser_window.py` deleted.
- `ftp_browser_window.py` and `http_browser_window.py` were already deleted (U1–U2).
- Dead `file_browser_window` stubs removed from `test_action_routing.py` and
  `test_server_list_card4.py`.
- All automated checks green; import-chain guard confirms impacket stays lazy.

---

## Card U6: SMB Virtual Root Shares (UI Parity with FTP/HTTP)

Goal:
Remove SMB share dropdown UX and present accessible shares as a root-level listing, matching FTP/HTTP browse flow.

Primary scope:
1. Replace SMB `Share:` combobox interaction with virtual SMB root listing (`\\host\\` equivalent).
2. Show accessible shares as top-level directory entries; entering one drills into that share.
3. Re-query accessible shares from DB at SMB browser launch (do not rely on stale caller-provided list alone).
4. Keep share-open failures non-fatal: return/stay at root and show clear status/error.
5. Keep sort order alphabetical and keep admin-share filtering behavior as-is.
6. Keep SMB path label in `share\\path` form (not full UNC).

Definition of done:
1. SMB browser opens directly to a share list root view with no share dropdown.
2. `Up` navigation from share root returns to share list root.
3. Existing SMB view/download/extract behavior still works after entering a share.
4. Credential handling remains correct per share (default auth + share overrides).
5. No regressions in FTP/HTTP browser behavior.

Regression checks:
1. `gui/tests/test_action_routing.py` passes.
2. SMB-focused server-list/browser tests updated and passing.
3. One manual SMB smoke:
   - open SMB browse
   - enter share
   - browse/view/download
   - return to root
   - verify share-open failure path is graceful

Out of scope:
1. SMB protocol engine rewrite.
2. Schema changes or migration changes.
3. Probe/cache payload contract changes.
