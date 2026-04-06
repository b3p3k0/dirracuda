# Reddit OD Module: Risk Register

Date: 2026-04-05

## R1: Endpoint behavior drift (unofficial JSON)
Impact: ingestion can break without code changes.
Mitigation:
1. Centralize endpoint handling in `experimental/redseek/client.py`.
2. Classify response-shape errors clearly.
3. Surface clear operator error text in dialog.
Validation:
1. Unit test malformed/partial JSON payload handling.

## R2: Soft rate limiting (HTTP 429)
Impact: partial ingestion and user confusion.
Mitigation:
1. Hard-abort current run on 429.
2. Show explicit reason and partial counts.
3. Keep default page cap <= 3 and delay 1-2s.
Validation:
1. Mock 429 response test path.

## R3: Data duplication and cursor drift
Impact: bloated DB and noisy browser results.
Mitigation:
1. Deterministic dedupe keys for targets.
2. PK on `post_id` for posts.
3. `new` mode tuple cursor compare.
4. `top` mode bounded refresh + dedupe-only semantics.
Validation:
1. Repeat-ingest tests for idempotency.

## R4: Legacy/main DB interference
Impact: regressions in established workflows.
Mitigation:
1. Separate sidecar DB file.
2. Zero writes to main schema.
3. Keep integration surface at GUI buttons/windows only.
Validation:
1. Startup + scan launch sanity checks across SMB/FTP/HTTP.

## R5: False positives from weak parsing
Impact: low-value targets and user trust erosion.
Mitigation:
1. Conservative validation and cleanup rules.
2. Confidence levels on each target.
3. Keep raw and normalized value for auditability.
Validation:
1. Parser test corpus with expected confidence classes.

## R6: UI freeze during ingestion
Impact: poor UX and perceived instability.
Mitigation:
1. Run network and parse work off main thread.
2. Marshal UI updates safely.
3. Keep bounded page and post limits.
Validation:
1. Manual UI responsiveness check during active run.

## R7: Ambiguous protocol on open action
Impact: failed open actions or wrong protocol guess.
Mitigation:
1. Explicit inference rules for known schemes/ports.
2. Prompt only when unresolved.
3. Do not auto-probe to guess.
Validation:
1. Manual test with scheme, port, and bare-host rows.
