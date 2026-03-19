# Locked Decisions (HI Approved)

Date locked: 2026-03-19

These decisions are authoritative for this workstream unless HI explicitly changes them.

## Protocol Representation

1. Host type uses `S`, `F`, and `H`.
2. If an IP exposes multiple protocols, it appears as one row per protocol.
3. Row identity is protocol-aware (host type + protocol server id), not IP-only.

## Per-Protocol State

1. Favorite/avoid/notes are protocol-specific for HTTP rows.
2. Probe/extracted/rce fields are protocol-specific for HTTP rows.
3. Same-IP cross-protocol state contamination is not allowed.

## Deletion Semantics

1. Deleting an `H` row removes only HTTP records for that row.
2. Deleting `S` or `F` sibling rows must not remove `H` rows (and vice versa).

## Migration and UX Requirements

1. Startup auto-migration only.
2. No user-facing migration scripts.
3. Hosts with 0 accessible dirs/files persist in DB.
4. `Shares > 0` advanced filter must hide 0-count HTTP rows when enabled.
5. HTTP browser uses full built-in server-list UX parity (not a separate picker-only flow).
