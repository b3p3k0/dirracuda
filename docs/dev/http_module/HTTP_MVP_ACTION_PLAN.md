# HTTP MVP Action Plan

This plan defines a low-risk path to add HTTP(S) index-discovery and browse/probe support while preserving SMB and FTP behavior.

## Goals

1. Add a reliable HTTP(S) discovery + verification + browse/probe MVP.
2. Keep SMB and FTP workflows stable, with additive changes only.
3. Reuse existing scan dialog, scan manager, workflow, persistence, and browser patterns where practical.
4. Keep migrations transparent to users (startup auto-migrate only).
5. Deliver unified browser integration for HTTP rows in MVP (same operator flow as SMB/FTP).

## Non-Goals (MVP)

1. No risky protocol-unification refactor of SMB/FTP code.
2. No destructive schema rewrites or manual migration scripts.
3. No deep web crawling/ranking engine in first pass.
4. No broad UI redesign beyond parity additions.

## Delivery Strategy

```text
Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6
 UI split   HTTP CLI   HTTP DB   HTTP verify   HTTP probe   QA/docs
```

## End-to-End Flow (Target)

```text
[Dashboard: Start HTTP Scan]
            |
            v
   [HTTP Scan Dialog + Scan Manager]
            |
            v
       [Backend HTTP CLI]
            |
            v
[Shodan candidates -> HTTP(S) verify -> index parse -> dir/file counts]
            |
            v
[Store http server + access summary rows]
            |
            v
[Write HTTP probe snapshot JSON]
            |
            v
[HTTP server browser + quarantine download path]
```

## Phase Plan

## Phase 1: Dashboard + HTTP Launch Dialog Wiring

Objective: Add HTTP scan launch UI that matches existing SMB/FTP ergonomics.

Deliverables:

1. Add `Start HTTP Scan` action to dashboard without changing existing scan lock semantics.
2. Add HTTP scan dialog (layout/behavior parity with FTP scan dialog).
3. Add TLS verification toggle in dialog (default allows insecure HTTPS verification).
4. Keep one-active-scan policy unchanged.
5. Persist HTTP dialog settings (in-session + restart parity).

Gate:

1. SMB and FTP launch paths still function unchanged.
2. HTTP button route works and shows expected dialog behavior.

## Phase 2: HTTP Workflow + CLI Skeleton

Objective: Create isolated HTTP execution path with GUI-compatible progress output.

Deliverables:

1. Add `httpseek` CLI entry point.
2. Add `shared/http_workflow.py` skeleton.
3. Add `BackendInterface.run_http_scan()` and `ScanManager.start_http_scan()` wiring.
4. Emit progress/output lines compatible with existing GUI parsing.

Gate:

1. HTTP scan can launch and complete a skeleton run without crashing.
2. SMB/FTP launch and progress parsing remain stable.

## Phase 3: HTTP Data Model + Persistence Layer

Objective: Add sidecar HTTP schema and read/write layer with startup migrations.

Deliverables:

1. Add `http_servers` table.
2. Add `http_access` summary table for verification and count outcomes.
3. Add `http_user_flags` / `http_probe_cache` tables for host-list parity.
4. Extend protocol presence view/query and unified list reads for HTTP coexistence (`host_type='H'`).
5. Ensure idempotent startup migration only (no user action).

Gate:

1. HTTP records persist independently.
2. Same IP can coexist across SMB/FTP/HTTP without collision in the unified server browser.
3. Existing DBs upgrade transparently at startup.

## Phase 4: HTTP Discovery Reliability + Count Semantics

Objective: Implement reliable HTTP candidate verification and index counting.

Deliverables:

1. Shodan candidate query baseline: `http.title:"Index of /"`.
2. HTTP(S) reachability/timeout verification with operator-controlled TLS verification mode.
3. Directory-index validation logic (not just title match).
4. File and directory count extraction for verified targets (HTTP and HTTPS).
5. One-level recursion for probe/count collection.
6. Failure reason taxonomy (`connect_fail`, `timeout`, `not_index`, `parse_fail`, etc.).

Gate:

1. Verified HTTP targets are persisted with actionable status and counts.
2. 0-accessible-dir/file outcomes are recognized, persisted, and made compatible with existing `Shares > 0` filtering behavior.

## Phase 5: HTTP Probe Snapshot + Browser/Download MVP

Objective: Provide HTTP browse/probe UX and quarantine-safe download parity.

Deliverables:

1. HTTP navigator helper for list/read/download behavior.
2. HTTP full built-in browser path inside the existing unified server browser flow.
3. HTTP probe snapshot cache path and save/load behavior.
4. Download-to-quarantine parity with existing safety model.

Gate:

1. Operator can inspect discovered HTTP index content in-app.
2. Probe output includes directory/file counts and sampled entries.
3. SMB/FTP browser behavior remains unchanged.

## Phase 6: QA, Hardening, and Documentation

Objective: Stabilize HTTP MVP and close regression + documentation loops.

Deliverables:

1. Add HTTP tests for workflow, counting, and UI/parsing edge cases.
2. Run SMB/FTP regression checklist after HTTP integration.
3. Update root and module docs.
4. Record known limits and follow-up work.

Gate:

1. No new SMB/FTP regressions.
2. HTTP manual runtime validation passes in active environment.
3. Handoff docs are complete and current.

## Dependency and Risk Notes

1. Existing scan state machine is shared; adding HTTP launch path must not drift lock behavior.
2. Progress/output parsing has parser-coupled assumptions; keep format-compatible lines.
3. Host-list filtering uses share-centric semantics; HTTP rows must map counts into `accessible_shares` so `Shares > 0` behaves consistently.
4. Allowing insecure HTTPS verification increases capture but should log TLS trust state for analyst context.
5. Legacy DB compatibility is a hard gate for any schema change.

## Future Refactor Hook (Post-MVP)

Keep HTTP snapshot and metadata shape aligned with SMB/FTP conventions so later normalization can ingest all protocols with minimal rewrite.
