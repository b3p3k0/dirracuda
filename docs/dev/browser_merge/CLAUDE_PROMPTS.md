# Claude Prompts - Browser Merge

Use these prompts one card at a time.

## Prompt U1 (Recommended First)

```text
Implement Card U1 from docs/dev/browser_merge/TASK_CARDS.md.

Context:
- We currently have protocol-specific browser windows:
  - gui/components/ftp_browser_window.py
  - gui/components/http_browser_window.py
  - gui/components/file_browser_window.py (SMB, not in this card)
- Goal is to unify FTP+HTTP browser UI/controller logic first with minimal risk.

Requirements:
1. Create a unified browser window/controller for FTP+HTTP only.
2. Preserve existing runtime behavior and user-visible text/layout as closely as possible.
3. Keep SMB flow untouched in this card.
4. Keep protocol-specific navigator logic in shared/ as-is (no protocol rewrite).
5. Keep changes surgical and reversible.

Deliverables:
1. Changed files list with rationale.
2. Before/after call path for FTP and HTTP browse launch.
3. Targeted tests run + exact commands.
4. Explicit completion labels:
   AUTOMATED: PASS|FAIL
   MANUAL: PASS|FAIL|PENDING
   OVERALL: PASS|FAIL|PENDING

Validation:
- xvfb-run -a python -m pytest gui/tests/test_action_routing.py -v
- xvfb-run -a python -m pytest gui/tests/test_ftp_browser_window.py -v
- xvfb-run -a python -m pytest gui/tests/test_http_browser_window.py -v
```

## Prompt U2

```text
Implement Card U2 from docs/dev/browser_merge/TASK_CARDS.md.

Requirements:
1. Route host_type F/H browse launches to the unified browser entrypoint.
2. Preserve protocol-specific context passing (port, scheme, banner).
3. Keep SMB browse routing unchanged.
4. Update tests for routing behavior where needed.

Deliver:
- changed files
- route mapping summary
- targeted test results with commands
- completion labels (AUTOMATED/MANUAL/OVERALL)
```

## Prompt U3

```text
Implement Card U3 from docs/dev/browser_merge/TASK_CARDS.md.

Requirements:
1. Add SMB adapter into unified browser with share selector and credential behavior preserved.
2. Avoid regressions in FTP/HTTP paths.
3. Keep extraction callback and quarantine behavior unchanged.
4. Add SMB banner-panel parity with FTP/HTTP:
   - SMB browse mode must display a banner panel in the same UI pattern.
   - SMB banner content must be derived from stored Shodan metadata when available.
   - If banner cannot be derived, show an explicit fallback placeholder (do not fail browse flow).

Data contract guidance for SMB banner:
- Prefer deterministic best-effort parse order and document it in code comments + summary.
- Do not introduce schema-breaking changes for this card.

Validation minimum:
- action routing tests
- SMB browse-related tests
- FTP/HTTP browser tests
- manual check for banner panel parity across S/F/H

Deliver:
- changed files
- SMB parity notes
- SMB banner extraction notes (source fields + fallback behavior)
- commands + results
- completion labels
```

## Prompts U5.3 + U5.4 — DELIVERED (2026-03-24)

U5.3: SMB browser moved onto `UnifiedBrowserCore` (`SmbBrowserWindow` added to
`unified_browser_window.py`; `open_smb_browser()` updated).

U5.4: `file_browser_window.py` deleted; dead `file_browser_window` stubs removed
from `test_action_routing.py` and `test_server_list_card4.py`; docs updated.

---

## Prompt U6 (SMB Virtual Root Shares)

```text
Implement Card U6 from docs/dev/browser_merge/TASK_CARDS.md.

Objective:
Make SMB browsing start from a virtual host root (share list) instead of a Share dropdown, so SMB UX matches FTP/HTTP root-first drill-down behavior.

Locked requirements:
1. Remove SMB share dropdown UI entirely.
2. On SMB browser open, re-query accessible shares from DB (best source of truth at launch time).
3. Render accessible shares as top-level directory rows.
4. Entering a share drills into that share root and preserves existing SMB browse/view/download behaviors.
5. `Up` from share root returns to virtual root share list.
6. Keep path label as `share\\path` (no full UNC requirement).
7. Keep current admin/hidden share filtering behavior unchanged.
8. On share access failure, do not crash and do not strand navigation:
   - show non-fatal error/status
   - keep user at virtual root
9. Keep changes surgical; no schema/migration changes.
10. Keep FTP/HTTP behavior unchanged.

Implementation guidance:
- Prefer UI/controller state changes in `gui/components/unified_browser_window.py`.
- Keep `shared/smb_browser.py` protocol logic unchanged unless a minimal bug fix is strictly required.
- If needed, adjust SMB launch call path to refresh shares from DB at open.
- Preserve existing extraction callback and quarantine write behavior.

Deliverables:
1. Changed files list with rationale.
2. State-machine summary (virtual root -> share root -> nested path -> back up).
3. Error-handling summary for stale/inaccessible shares.
4. Targeted test commands + results.
5. Completion labels:
   AUTOMATED: PASS|FAIL
   MANUAL: PASS|FAIL|PENDING
   OVERALL: PASS|FAIL|PENDING

Validation minimum:
- xvfb-run -a python -m pytest gui/tests/test_action_routing.py -v
- xvfb-run -a python -m pytest gui/tests/test_server_list_card4.py -v
- xvfb-run -a python -m pytest gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py -v

Manual checks:
1. Open SMB browser for host with >=2 accessible shares; confirm no dropdown.
2. Confirm top-level shows share rows (sorted).
3. Enter share, browse directory, view file, download file.
4. Use Up until root and confirm share list reappears.
5. Validate graceful failure path when a listed share cannot be opened.
```

---

## Review Prompt (Use After Claude Returns)

```text
Review this plan/result for Card Ux with a bug-risk lens.

Check:
1. Any behavior regression risk on SMB/FTP/HTTP browse paths?
2. Any protocol-cross contamination risk?
3. Any path normalization bugs (SMB vs POSIX)?
4. Any missing tests for changed routing/UI flows?
5. Any shortcuts/assumptions that violate the card scope?

Return:
- Findings ordered by severity
- Exact file:line references
- Required fixes before merge
- Residual risk summary
```
