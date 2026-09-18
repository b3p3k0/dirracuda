# R3a — Sidecar Schema v5: Host-Read Contact Ledger

- Date: 2026-09-18
- Type: code card. codex implements, Claude validates.
- Depends on: R2 (v4). Enables R3b (read reduce).
- Scope: experimental sidecar `analyst.db` only (`experimental/analyst/db_schema.py`). Do NOT
  touch the primary DB. Additive-only, same guarded pattern R2 used.

## Why

The read-first "host READ" step is one host-level Ollama generation per run. Every Ollama HTTP
contact must be precharged in a durable, content-free ledger (kept invariant, C9B/R91). The
existing `analyst_ollama_contacts` ledger ties a `chat` contact to a `chunk_id` (table CHECK),
so a host-level call does not fit it. Rather than rebuild that safety-critical table's CHECK, add
a small ADDITIVE host-read contact ledger. Keyed by run_id (D5: as the primary tables key a run).

## Deliverable (db_schema.py + tests)

1. Bump `SCHEMA_VERSION` 4 -> 5. Add `V4_SCHEMA_VERSION=4`; set `PREVIOUS_SCHEMA_VERSION=4`;
   `KNOWN_SCHEMA_VERSIONS=(1,2,3,4,5)`. Update `__all__`. (Mirror how R2 added v4.)
2. `_V5_ADDITIONAL_DDL` = exactly one new STRICT table (+ its index):

   ```sql
   CREATE TABLE analyst_read_contact (
     contact_id     TEXT PRIMARY KEY CHECK(<lower 64-hex, reuse _LOWER_SHA>),
     run_id         TEXT NOT NULL,
     attempt_no     INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 2),
     request_sha256 TEXT NOT NULL CHECK(<lower 64-hex>),
     lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
     state          TEXT NOT NULL CHECK(state IN (<reuse OLLAMA_CONTACT_STATES>)),
     charged_at_utc   TEXT NOT NULL CHECK(length BETWEEN 1 AND 40),
     finished_at_utc  TEXT CHECK(finished_at_utc IS NULL OR length BETWEEN 1 AND 40),
     resource_failures_before INTEGER NOT NULL CHECK(BETWEEN 0 AND 6),
     resource_failures_after  INTEGER CHECK(IS NULL OR BETWEEN 0 AND 6),
     FOREIGN KEY(run_id) REFERENCES analyst_runs(run_id) ON DELETE RESTRICT,
     UNIQUE(run_id, attempt_no),
     CHECK((state='dispatching' AND finished_at_utc IS NULL)
        OR (state!='dispatching' AND finished_at_utc IS NOT NULL))
   ) STRICT
   ```
   Plus `CREATE INDEX idx_analyst_read_contact_run ON analyst_read_contact(run_id)`.
   Match the exact style/idioms already used by `analyst_ollama_contacts` (the `_LOWER_SHA`
   helper, the `_values(OLLAMA_CONTACT_STATES)` interpolation).
3. Migration branches: fresh DB -> v5; v1/v2/v3/v4 -> v5 additively, one `BEGIN IMMEDIATE`,
   idempotent, refuse foreign/partial DBs without mutation. Extend `_expected_snapshot`,
   the version-validation chain, `validate_v4_migration_candidate` (new; require exact v4, idle),
   and `store.py::_audit_existing_database` (add the V4 branch) exactly as R2 did for v3.
4. Tests `shared/tests/test_analyst_r3a.py`: fresh v5 (table+index exist, user_version=5);
   v1..v4 -> v5 additive migration preserving existing rows; idempotent re-init; refuse
   foreign/partial; v5 snapshot verifies; the CHECK rejects attempt_no outside 1..2 and a
   dispatching row with a finished timestamp.

## Constraints

- ONLY `experimental/analyst/db_schema.py`, `experimental/analyst/store.py` (audit branch), and
  the new test. Additive only; do NOT rebuild or alter any existing table's CHECK. Do NOT touch
  `analyst_ollama_contacts`. Preserve every guardrail (E13 journal mode, foreign/partial refuse,
  E16 pristine-only v1). Keep db_schema.py < 1700 lines.
- Update the existing schema-pinning tests ONLY as needed for the version bump (the c8 fresh
  test adds `analyst_read_contact` + its index; any snapshot/version test that enumerates
  objects). Do NOT weaken any refusal test.

## Acceptance (Claude validates in the real venv)

1. `./venv/bin/python -m pytest shared/tests/test_analyst_r3a.py -v` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
3. `git diff --stat` touches only db_schema.py, store.py, the new test, and the minimal existing
   schema-pinning tests. No core table rebuilt.
