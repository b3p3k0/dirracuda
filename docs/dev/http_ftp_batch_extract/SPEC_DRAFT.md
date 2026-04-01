# Spec Draft: Bulk Extract Parity for FTP + HTTP

Date: 2026-04-01
Status: Draft (for HI review)

## 1) Objective

Add bulk extraction support for FTP and HTTP server rows, preserving existing SMB behavior and existing UI workflows.

## 2) In-Scope

1. Server List batch extract path (`_execute_extract_target`) for host types `F` and `H`.
2. Dashboard post-scan bulk extract path (`_extract_single_server`) for `F` and `H` rows.
3. Reuse existing extraction limits/settings (size/time/file-count/extensions/quarantine/ClamAV) for all protocols.
4. Deterministic extraction source for FTP/HTTP based on existing probe snapshots.

## 3) Out-of-Scope

1. New DB schema migrations.
2. Deep crawler behavior beyond existing probe snapshot depth.
3. Protocol-wide refactor of scan/probe architecture.
4. Changing SMB extract contracts unless required for shared helper reuse.

## 4) Current Root Cause

1. `extract_runner.run_extract(...)` is SMB-only by transport and file enumeration.
2. Server-list batch extract intentionally skips FTP/HTTP with a hardcoded message.
3. Dashboard bulk extract does not branch by protocol, so FTP/HTTP rows can enter SMB code path.

## 5) Proposed Design

## 5.1 Core principle

Keep `run_extract(...)` (SMB) stable; add protocol-aware extraction entrypoints for FTP/HTTP that share guardrails and summary contract.

## 5.2 FTP/HTTP extraction source

Use cached probe snapshots as the canonical candidate source:

1. FTP snapshot: `gui/utils/ftp_probe_cache.py` (`ftp_root`, root files + directory file samples).
2. HTTP snapshot: `gui/utils/http_probe_cache.py` (endpoint-aware `ip_port.json` path when port is known).

If no snapshot exists, return `skipped` with actionable note (`Probe required before extract`).

## 5.3 Candidate file model

Normalize snapshot into remote file candidates:

1. Root files -> `/filename`
2. Directory files -> `/<dir>/<filename>`
3. Deduplicate by normalized remote path.
4. Apply existing limit checks and extension mode before download.

## 5.4 Download engines

1. FTP: `shared.ftp_browser.FtpNavigator.download_file(...)`
2. HTTP: `shared.http_browser.HttpNavigator.download_file(...)`

Each downloaded file still lands in quarantine first; ClamAV post-processing pipeline remains fail-open and shared.

## 5.5 Output contract

Preserve summary shape used by existing dialogs/logging:

1. `totals.files_downloaded`, `totals.bytes_downloaded`, `totals.files_skipped`
2. `files[]`, `skipped[]`, `errors[]`, `timed_out`, `stop_reason`
3. `clamav` block always present (`{"enabled": false}` when disabled)

## 5.6 Host status updates

On successful run (including zero downloaded due to filters/limits), update extracted flag via existing per-host updater APIs, keyed by host type and endpoint metadata.

## 6) Safety + Compatibility Constraints

1. No assumptions about schema shape; continue graceful fallback behavior where protocol tables may be missing.
2. No UI-thread blocking work added.
3. No behavior regressions for SMB extract path.
4. Explicitly handle HTTP endpoint identity (`ip + port`) where available.
5. Fail-open on ClamAV or snapshot parsing issues; do not crash batch jobs.

## 7) Performance Constraints

1. Maintain existing server-level thread pool behavior.
2. Avoid expensive fresh recursive remote walks in extract path.
3. Keep FTP/HTTP extraction bounded to probe-sampled candidates and configured limits.

## 8) Known Risks

1. Snapshot path mismatch for HTTP endpoint-specific cache files could cause false "no snapshot" skips.
2. Directory/file name normalization may produce invalid remote paths on unusual servers.
3. Existing tests currently assert FTP extract is skipped; these will need intentional updates.
4. Detail-popup fallback extract path may still be SMB-biased if callback wiring is bypassed.

## 9) Initial Acceptance Criteria

1. Server-list batch extract on selected FTP rows downloads sampled files instead of "not yet supported."
2. Server-list batch extract on selected HTTP rows downloads sampled files instead of "not yet supported."
3. Dashboard post-scan bulk extract works for scan protocol `ftp` and `http` without entering SMB transport path.
4. SMB behavior remains unchanged.
5. ClamAV summary + routing works consistently for all three protocols.
