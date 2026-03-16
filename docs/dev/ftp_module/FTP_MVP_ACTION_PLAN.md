# FTP MVP Action Plan

This plan defines a low-risk path to add anonymous FTP support while preserving SMB behavior.

## Goals

1. Add a reliable FTP discovery and browse/download MVP.
2. Keep SMB workflow stable and untouched except for explicit dashboard wiring changes.
3. Maintain implementation patterns compatible with future SMB+FTP normalization work.

## Non-Goals (MVP)

1. No value/ranking or advanced content prioritization.
2. No full normalization of directory/file artifacts into SQL.
3. No protocol-unification refactor of existing SMB code during MVP.

## Delivery Strategy

```text
Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 -> Phase 5 -> Phase 6
 UI split   FTP CLI    FTP DB    FTP discover  FTP browse   QA/docs
```

## End-to-End Flow (Target)

```text
[Dashboard: Start FTP Scan]
            |
            v
    [FTP Scan Manager]
            |
            v
     [Backend FTP CLI]
            |
            v
[Shodan candidates -> anon login test -> root listing test]
            |
            v
 [Store ftp server + access summary rows]
            |
            v
 [Write FTP probe snapshot JSON]
            |
            v
 [FTP server browser + download in app]
```

## Phase Plan

## Phase 1: Dashboard and Scan Entry Split

Objective: Introduce protocol-specific scan entry points while preserving current scan lock behavior.

Deliverables:

1. Rename dashboard primary scan control to `Start SMB Scan`.
2. Add `Start FTP Scan` action.
3. Preserve single active scan policy (same lock semantics).
4. Keep SMB scan behavior unchanged.

Gate:

1. SMB scan starts/stops exactly as before.
2. FTP button route exists and is isolated.

## Phase 2: FTP Workflow Skeleton

Objective: Create a separate FTP workflow/CLI scaffold with matching operator ergonomics.

Deliverables:

1. FTP CLI entry point with core args mirroring SMB where sensible.
2. FTP workflow shell with discovery and access step placeholders.
3. Progress output shape compatible with GUI expectations (`📊 Progress: x/y (%)` style).

Gate:

1. FTP command runs and reports progress without touching SMB pipeline.
2. No SMB regressions in scan manager path.

## Phase 3: FTP Data Model and Persistence

Objective: Add sidecar FTP schema and protocol coexistence view/query layer.

Deliverables:

1. `ftp_servers` table.
2. `ftp_access` summary table.
3. Optional `ftp_probe_cache` table if needed for status parity.
4. Protocol presence view/query (`has_smb`, `has_ftp`, `both`) keyed by IP.
5. Idempotent migrations.

Gate:

1. FTP records persist independently of SMB records.
2. Same IP can exist in both protocol paths.
3. Presence flags resolve correctly.

## Phase 4: FTP Discovery Reliability

Objective: Implement reliable anonymous FTP detection and root enumeration.

Deliverables:

1. Shodan FTP query strategy and candidate handling.
2. TCP/timeout handling for port 21.
3. Anonymous auth verification and root listing verification.
4. Error taxonomy for auth/list/timeout failures.
5. Summary writes into `ftp_access`.

Gate:

1. False positives are reduced by runtime verification.
2. Failures are captured with actionable statuses.

## Phase 5: FTP Browser and Download MVP

Objective: In-app FTP browse and download, following SMB safety posture.

Deliverables:

1. FTP navigator helper (`list/read/download`).
2. Browser UI path for FTP hosts.
3. Download-to-quarantine parity.
4. Probe snapshot JSON cache path for FTP.

Gate:

1. Browse and download function from GUI for FTP hosts.
2. Remote operations stay read-only.

## Phase 6: QA, Docs, and Release Readiness

Objective: Stabilize MVP and produce operator-facing notes.

Deliverables:

1. Regression checks for SMB scan path.
2. FTP reliability and timeout tests.
3. Manual test matrix for mixed SMB+FTP IPs.
4. Doc updates (root README, dev docs, usage notes).

Gate:

1. MVP stable across repeated runs.
2. Clear known limitations documented.

## Dependency and Risk Notes

1. Dashboard currently uses a single scan state machine; introducing two scan entry points must avoid state drift.
2. GUI progress parsing is SMB-wording-heavy; FTP CLI output should stay format-compatible to reduce GUI churn.
3. Keep protocol coexistence in a query/view layer first to avoid intrusive SMB schema changes.

## Future Refactor Hook (Post-MVP)

Keep FTP snapshot schema and metadata shape close to SMB snapshot conventions so a later normalization pass can ingest both into unified artifact tables.
