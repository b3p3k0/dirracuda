# SMBClient Removal Workspace

This directory is the dedicated workspace for the pure-Python SMB migration.

## Active Artifacts

1. `TASK_CARDS.md` - End-to-end execution cards (one-card-at-a-time model)
2. `CONTRACT_MATRIX.md` - SMB callsites and replacement contract map
3. `RISK_REGISTER.md` - Migration risks, mitigations, rollback triggers
4. `S6_VALIDATION_REPORT.md` - Final automated/manual validation evidence
5. `S6_ROLLBACK_RUNBOOK.md` - Rollback procedure and recovery expectations

## Working Rules

1. Keep all migration planning and execution notes in this directory.
2. Execute one task card at a time with explicit PASS/FAIL gates.
3. Preserve locked decisions from HI:
   - SMB1 discovery support is mandatory.
   - Cautious mode remains strict (`signed SMB2+/SMB3 only` contract).
   - Legacy `smbclient` labels are cleaned during cutover.
