# Risks, Edge Cases, and Assumptions

Date: 2026-03-17

## Assumptions

1. SMB and FTP registries remain physically separate in MVP.
2. Unified browser rows are built at query time (UNION ALL).
3. Protocol-specific state tables are acceptable additive schema changes.

## Key Risks

## 1) Duplicate-IP row collisions

Risk:
Legacy code frequently resolves rows by IP only.

Impact:
Actions and state updates may hit wrong protocol row.

Mitigation:
Use protocol-aware row identity (`host_type` + protocol table id) end-to-end.

## 2) Cross-protocol state contamination

Risk:
Existing helpers write only SMB state tables.

Impact:
FTP row toggles could silently alter SMB row flags/status.

Mitigation:
Introduce protocol-aware write helpers and audit all row-action callsites.

## 3) Shares-centric filters hiding FTP rows

Risk:
Current server list defaults to `Shares > 0`.

Impact:
Operators may not see FTP rows despite valid discovery.

Mitigation:
Adjust filter semantics for mixed-protocol view or provide protocol-aware defaults.

## 4) Delete by IP behavior

Risk:
Current delete path in SMB list uses IP-based delete from `smb_servers`.

Impact:
Protocol-specific delete semantics can fail or delete wrong scope.

Mitigation:
Implement explicit per-protocol delete APIs and UI routing.

## 5) Metric and summary ambiguity

Risk:
Dashboard/server counts may mix host and protocol counts.

Impact:
Confusing operator interpretation ("hosts" vs "entries").

Mitigation:
Label metrics clearly as host count, SMB row count, or FTP row count.

## Edge Cases to Test

1. IP exists only in SMB.
2. IP exists only in FTP.
3. IP exists in both SMB and FTP.
4. IP exists in both, but SMB has no accessible shares while FTP has rich directories.
5. Favorite toggled on one protocol while opposite protocol remains untouched.
6. Deleting one protocol row and confirming sibling row remains.

## Failure Modes (Do Not Ignore)

1. UNION query returns duplicate rows with same row key.
2. Selecting one row highlights another row with same IP.
3. Batch action silently drops FTP rows.
4. Probe/extracted status appears to "flip" across protocols.

## Out of Scope (Current MVP)

1. Collapsing SMB+FTP into one physical host table.
2. Introducing `B` host type.
3. Protocol-aware scoring/ranking model redesign.
4. Full refactor of all legacy tools outside core GUI + migrations + data access.
