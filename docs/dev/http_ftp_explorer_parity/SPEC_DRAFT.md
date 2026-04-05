# HTTP/FTP Explorer Parity Spec (Draft)

Date: 2026-04-04  
Scope: Unified explorer parity for download tuning controls and behavior.

## Problem Statement

The unified explorer is not functionally parity-complete:

- SMB exposes download tuning (`worker count`, `large files limit`) and uses those settings at runtime.
- FTP/HTTP explorer flows do not expose these controls and do not apply equivalent runtime tuning behavior.

This causes UX inconsistency and makes bulk browser downloads less controllable outside SMB.

## Goals

1. Expose download tuning controls in FTP/HTTP explorer windows.
2. Persist tuning values via shared `file_browser.*` settings keys.
3. Bring runtime behavior closer to SMB:
   - FTP: worker count + large-file threshold behavior.
   - HTTP: worker count behavior only (large split deferred).
4. Keep SMB behavior unchanged.
5. Document the explicit HTTP limitation.

## Non-Goals

1. No schema/migration changes.
2. No deep refactor of protocol navigators.
3. No HTTP HEAD/content-length discovery expansion in this phase.
4. No redesign of non-download explorer UX.

## Current-State Evidence (Call Paths)

1. Shared FTP/HTTP window UI is built in `gui/components/unified_browser_window.py` (`UnifiedBrowserCore`).
2. SMB has separate window construction and tuning controls in same file (`SmbBrowserWindow`).
3. FTP download loop currently processes files sequentially in `FtpBrowserWindow._download_thread_fn`.
4. HTTP download loop currently processes files sequentially in `HttpBrowserWindow._download_thread_fn`.
5. Existing shared settings keys already exist for SMB tuning:
   - `file_browser.download_worker_count`
   - `file_browser.download_large_file_mb`

## Design Contract

1. Add tuning strip to `UnifiedBrowserCore` for FTP/HTTP windows:
   - `Worker count` (1..3)
   - `Large files limit (MB)`
2. HTTP rendering rule:
   - show large-file control but disable it and show explicit note.
3. Persistence:
   - load and save both values via shared settings keys above.
4. Runtime behavior:
   - FTP: apply worker-count concurrency and large/small queue routing.
   - HTTP: apply worker-count concurrency only.
5. Safety invariants:
   - preserve cancellation semantics.
   - preserve ClamAV post-processing and fail-open behavior.
   - preserve existing completion popup/dialog behavior.

## Acceptance Criteria (Global)

1. FTP UI shows editable worker and large-file controls.
2. HTTP UI shows worker control editable + large-file control disabled with clear explanation.
3. Shared settings survive window reopen/restart behavior (via settings manager path).
4. FTP worker count and large threshold affect runtime download behavior.
5. HTTP worker count affects runtime download behavior; large split remains inactive by design.
6. README + Technical Reference explicitly describe the limitation.
7. Targeted test suite passes with no SMB regressions introduced by touched code.

## Verification Standard

- Run targeted `py_compile` + focused `pytest` for touched browser tests.
- Report exact commands + PASS/FAIL.
- Any residual risk must be explicit in card report.
