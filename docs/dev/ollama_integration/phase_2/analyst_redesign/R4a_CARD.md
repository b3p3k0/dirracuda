# R4a — Sidecar schema v6: model discovery ledger + discovered-model list

- Date: 2026-09-18
- Type: code card. codex implements, Claude validates.
- Depends on: R3a (v5). Enables R4b (dropdown). Scope: sidecar `analyst.db` ONLY. Additive-only.
- Contract: Amendment A2.

## Deliverables (db_schema.py + tests)

1. Version constants: add V5_SCHEMA_VERSION=5; PREVIOUS_SCHEMA_VERSION=5; SCHEMA_VERSION=6;
   KNOWN_SCHEMA_VERSIONS=(1..6). Update __all__.
2. `_V6_ADDITIONAL_DDL` (all STRICT, additive):
   - `analyst_discovery_contact` — a pre-run charged /api/tags contact ledger, NOT tied to a run:
     `contact_id TEXT PRIMARY KEY CHECK(<lower 64-hex>)`, `contact_no INTEGER NOT NULL CHECK(>0)`
     with `UNIQUE(contact_no)` (a monotonic ordinal; there is no run to scope it), `endpoint TEXT
     NOT NULL` (content-free, e.g. "127.0.0.1:11434"), `request_sha256 TEXT NOT NULL CHECK(<hex>)`,
     `state TEXT NOT NULL CHECK(state IN (<reuse OLLAMA_CONTACT_STATES>))`, `models_found INTEGER
     CHECK(models_found IS NULL OR models_found >= 0)`, `charged_at_utc TEXT NOT NULL`,
     `finished_at_utc TEXT`, with the same dispatching/finished CHECK pattern as other ledgers.
   - `analyst_discovered_model` — the persisted non-cloud model list:
     `endpoint TEXT NOT NULL`, `model_tag TEXT NOT NULL CHECK(length>0)`, `model_digest TEXT NOT
     NULL CHECK(<lower 64-hex>)`, `first_seen_utc TEXT NOT NULL`, `last_seen_utc TEXT NOT NULL`,
     `PRIMARY KEY(endpoint, model_tag)`.
   - Indexes as useful (e.g. on discovered_model(endpoint)).
3. Add `validate_v5_migration_candidate` (exact v5, idle) mirroring the v4 candidate. Extend
   initialize_schema: fresh -> v6; v1..v5 -> v6 additive, one BEGIN IMMEDIATE, idempotent, refuse
   foreign/partial. Extend `_expected_snapshot`, the version chain, and `store.py::
   _audit_existing_database` with the v5 branch.
4. Tests `shared/tests/test_analyst_r4a.py`: fresh v6 (both tables + user_version=6); v1..v5 -> v6
   additive migration preserving rows; idempotent re-init; refuse foreign/partial; v6 snapshot
   verifies; discovery-contact dispatching/finished CHECK enforced; discovered_model PK dedupes on
   (endpoint, model_tag). Update the c8 fresh-identity + any snapshot/version-pinning tests for v6
   WITHOUT weakening any refusal test.

## Constraints
- Additive only; do NOT rebuild/alter any existing table or the analyst_ollama_contacts CHECK.
  Preserve every guardrail (E13 DELETE mode, E16 pristine-only v1, foreign/partial refuse).
- Only db_schema.py, store.py (audit branch), and the minimal existing schema-pinning tests +
  the new test. db_schema.py < 1700 lines (if the change would exceed it, stop and report).

## Acceptance (Claude validates, real venv)
1. `./venv/bin/python -m pytest shared/tests/test_analyst_r4a.py -q` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
3. `git diff` shows no core table rebuilt; fresh DB reports user_version=6 with both new tables.
