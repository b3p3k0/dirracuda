# QA/QC Gates - HTTP DB Import Workstream

Use after each card and before merge decisions.

## Gate A: Migration Safety

1. Startup migration runs automatically with no prompts.
2. Migration re-run is idempotent.
3. Existing SMB/FTP data remains intact.

## Gate B: Multi-Protocol Row Correctness

1. Duplicate IP across protocols renders as distinct rows.
2. Row identity is protocol-aware, not IP-only.

## Gate C: Protocol State Isolation

1. HTTP state changes do not alter SMB/FTP rows for same IP.

## Gate D: Action Routing

1. HTTP row routes to HTTP actions only.

## Gate E: Deletion Semantics

1. Deleting HTTP row does not delete SMB/FTP sibling rows.

## Gate F: Regression Baseline

1. SMB, FTP, and HTTP critical paths still work.

