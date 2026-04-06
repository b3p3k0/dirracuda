# Reddit OD Module: Locked Decisions

Date: 2026-04-05
Status: Approved by HI

## Intent
Build an experimental, GUI-driven Reddit feed ingestion module for `r/opendirectories` that is fully isolated from Dirracuda's existing scan workflows.

## Locked Decisions
1. Data storage uses a separate sidecar SQLite database (not the main Dirracuda DB).
2. MVP entrypoint is GUI-only (no CLI path in this phase).
3. Ingestion supports both `new` and `top` sort sources.
4. `Replace cache` performs a full wipe of Reddit module data (posts, targets, ingest state).
5. Explorer action should infer protocol when possible, and prompt the user only when protocol cannot be inferred reliably.

## Guardrails
1. Do not integrate with SMB/FTP/HTTP scan pipelines.
2. Do not auto-probe or auto-extract anything from ingested targets.
3. Keep all Reddit data untrusted and analyst-triggered only.
4. Keep changes surgical and reversible.
5. No commits unless HI explicitly says `commit`.

## Root-Cause Operating Rules
1. Fix root causes, not symptom suppression.
2. Prevent known failures from accumulating.
3. Treat migration and data contract compatibility as first-class.
4. Guard schema/data operations by runtime state checks.
5. Avoid performance regressions on UI/runtime hot paths.
6. Be explicit and safe with parsing/coercion/validation behavior.
