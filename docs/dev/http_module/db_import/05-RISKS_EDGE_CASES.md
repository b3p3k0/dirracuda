# Risks, Edge Cases, and Assumptions

Date: 2026-03-19

## Assumptions

1. HTTP storage remains sidecar/additive in MVP.
2. Startup migration remains transparent to users.
3. Protocol routing uses explicit host type, not IP-only logic.

## Key Risks

## 1) Duplicate-IP row collisions

Risk:
Legacy logic may key rows by IP only.

Mitigation:
Use protocol-aware row identity end-to-end.

## 2) Cross-protocol state contamination

Risk:
Shared helpers may default to SMB/FTP tables.

Mitigation:
Protocol-aware write helpers and callsite audits.

## 3) Zero-count visibility ambiguity

Risk:
Hosts with 0 dirs/files may be hidden unexpectedly.

Mitigation:
Define and test explicit filter semantics for 0-count rows.

## Edge Cases to Test

1. HTTP-only host.
2. Same IP present in SMB + FTP + HTTP.
3. HTTP verified host with 0 dirs and 0 files.
4. Protocol-specific favorite/probe/delete for same IP.

