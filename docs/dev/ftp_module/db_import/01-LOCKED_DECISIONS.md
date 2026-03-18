# Locked Decisions (HI Approved)

Date locked: 2026-03-17

These decisions are authoritative for this workstream unless explicitly changed by HI.

## Protocol Representation

1. Host type uses `S` and `F` only.
2. If an IP exposes both protocols, it appears as two rows:
   - SMB row: `S <ip>`
   - FTP row: `F <ip>`
3. No merged single-row `B` representation in MVP.

## Per-Protocol State

1. `favorite` / `avoid` / `notes` are protocol-specific (not shared across S/F rows).
2. `probe status`, `indicator matches`, `extracted`, `rce status` are protocol-specific.
3. SMB and FTP state must not cross-contaminate for same IP.

## Deletion Semantics

1. Deleting one selected row removes only that protocol entry.
2. Deleting `S 1.2.3.4` must not delete `F 1.2.3.4`.
3. Deleting `F 1.2.3.4` must not delete `S 1.2.3.4`.

## Migration and UX Requirements

1. DB migrations are invisible and automatic on startup.
2. No side tools and no user migration steps.
3. Server-list/browser text should be protocol-generic where practical.
