# R2 — Sidecar Schema v4: Read Tables

- Date: 2026-09-18
- Type: code card. Implemented by codex, validated by Claude.
- Depends on: R1. Implements `REPORT_JSON_SCHEMA.md` §2.
- Scope: **the experimental sidecar `analyst.db` only** (`experimental/analyst/db_schema.py`).
  Do NOT touch the primary `dirracuda.db` (`tools/db_schema.sql`, `shared/db_migrations.py`).
  The operator (HI) approved this sidecar schema change in the R0 decision.

## Goal

Add schema v4 to the analyst sidecar: the read projection tables and additive `fact_rank`
columns, with the same guardrailed migration the package already uses for v1->v2->v3.

## Deliverables (all in `experimental/analyst/db_schema.py` unless noted)

1. Version constants: add v4. Keep `V1_SCHEMA_VERSION=1`. Introduce explicit `V2_SCHEMA_VERSION=2`
   and `V3_SCHEMA_VERSION=3` if needed for migration branches. Set `PREVIOUS_SCHEMA_VERSION=3`,
   `SCHEMA_VERSION=4`, `KNOWN_SCHEMA_VERSIONS=(1,2,3,4)`. Update `__all__`.
2. `_V4_ADDITIONAL_DDL` mirroring the `_V3_ADDITIONAL_DDL` pattern, containing exactly the DDL in
   `REPORT_JSON_SCHEMA.md` §2:
   - `CREATE TABLE analyst_read (...) STRICT` (§2.1).
   - `CREATE TABLE analyst_read_exposures (...) STRICT` (§2.2).
   - `ALTER TABLE analyst_detector_hits ADD COLUMN fact_rank TEXT CHECK(...)` (§2.3).
   - `ALTER TABLE analyst_model_findings ADD COLUMN fact_rank TEXT CHECK(...)` (§2.3).
   - Any needed index (e.g. on `analyst_read(risk_level)`).
3. `initialize_schema`: extend the migration branches so a fresh DB is created at v4, and
   v1/v2/v3 DBs upgrade to v4 additively, all inside one `BEGIN IMMEDIATE`, idempotent, refusing
   foreign/partial DBs without mutation. Follow the exact existing pattern (concurrent-identity
   recheck, per-version DDL application, `PRAGMA user_version=SCHEMA_VERSION`).
4. `_expected_snapshot` / schema-verification: extend so v4 objects are the expected snapshot,
   and older versions still map to their own snapshots.
5. Row validation: add `_validate_v4_rows` (or extend the chain) enforcing the CHECK-backed
   domains where SQLite CHECK is not enough (e.g. `contacts_json` is a JSON array; `risk_level`
   in HIGH/MED/LOW; `read_mode` in quick/full).
6. Tests: `shared/tests/test_analyst_r2.py` — fresh-v4 create; v1->v4, v2->v4, v3->v4 additive
   migration (existing rows preserved, new columns default NULL); idempotent re-init; refuse a
   foreign application_id and a partial/corrupt DB without mutation; the v4 snapshot verifies;
   `fact_rank` accepts HIGH/MED/low and NULL and rejects other values.

## Constraints (codex must obey)

- Only `experimental/analyst/db_schema.py` and the new test file change. Do NOT touch the primary
  DB, other analyst modules, GUI, requirements, or CI.
- Additive only. No column drop/rename/retype. Existing v1/v2/v3 rows must survive migration
  untouched with new columns defaulting NULL/0.
- Preserve every existing guardrail: single `BEGIN IMMEDIATE`, idempotent, foreign/partial refuse,
  concurrent-identity recheck, `synchronous`/journal settings unchanged (E13 DELETE mode stays).
- STRICT tables. Match the file's existing DDL and code style. File stays < 1700 lines
  (if the change would exceed it, stop and report for modularization).

## Acceptance (Claude validates)

1. `./venv/bin/python -m pytest shared/tests/test_analyst_r2.py -v` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` shows no regression (v1/v2/v3 tests
   still green).
3. `git diff --stat` touches only `db_schema.py` + the new test.
4. A quick manual check: creating a fresh sidecar reports `user_version=4` and the two new
   tables + two new columns exist; a v3 DB migrates in place with existing rows intact.

---

## R2 correction (round 2) — issues found in review

The first R2 pass introduced two regressions and left three stale tests. Fix all five.
Allowed files widen to include `experimental/analyst/store.py` and the three named tests.

### Regression 1 — E16 pristine-only v1 restriction was deleted (must restore)

E16 states: "V1 retains its earlier pristine-only migration restriction." The first pass
rewrote `validate_v1_migration_candidate` to allow a populated v1 DB (only refusing "active"
work) and deleted `_V1_DOMAIN_TABLES`. This is a contract violation.

Fix in `db_schema.py`:
- Restore `_V1_DOMAIN_TABLES` and the zero-domain-rows refusal: a v1 DB with ANY row in a
  domain table is refused ("populated Analyst v1 requires an explicit later migration").
- Keep the unowned generation-zero lease check.
- Remove the added active-run/active-file/active-attempt "idle" relaxation for v1 (v1 is
  pristine-only; those checks belong to v2/v3 candidates, not v1).
- Remove the `INSERT INTO analyst_ollama_schedule ... SELECT ... FROM analyst_runs` seeding in
  the v1->v4 path: a pristine v1 has zero runs, so no seeding is needed or allowed.
- Result: pristine (empty) v1 migrates additively to v4; populated v1 is refused unchanged.

### Regression 2 — store.py audit broke for v2 and v3 (must fix)

`store.py::_audit_existing_database` branches on `PREVIOUS_SCHEMA_VERSION`. Changing that
constant to 3 means a v2 DB is now refused and a v3 DB is validated as v2. Fix:
- Give `_audit_existing_database` explicit branches: `SCHEMA_VERSION` -> `validate_schema`;
  `V3_SCHEMA_VERSION` -> `validate_v3_migration_candidate`; `V2_SCHEMA_VERSION` ->
  `validate_v2_migration_candidate`; `V1_SCHEMA_VERSION` -> `validate_v1_migration_candidate`;
  else refuse.
- Grep the whole package + tests for `PREVIOUS_SCHEMA_VERSION` and any place that assumes it
  equals 2; make each use the explicit `V2_SCHEMA_VERSION` / `V3_SCHEMA_VERSION` it means.

### Stale tests to update to v4 (preserve what they verify)

- `shared/tests/test_analyst_c8.py::test_fresh_database_has_exact_identity_schema_and_owner_permissions`:
  add `analyst_read` and `analyst_read_exposures` to the expected table set and
  `idx_analyst_read_risk` to the expected index set. Keep `user_version == SCHEMA_VERSION`.
- `shared/tests/test_analyst_c9_durable.py::test_exact_empty_v1_migrates_additively_and_reopen_is_idempotent`:
  the additive comparison excludes changed tables. v4 ALTERs `analyst_detector_hits` and
  `analyst_model_findings` (adds `fact_rank`) and adds `analyst_read`,
  `analyst_read_exposures`, `idx_analyst_read_risk`. Extend the exclusion set to drop these
  (mirror the existing `analyst_files` exclusion) via new test constants `_V4_TABLES` /
  `_V4_INDEXES`. Do NOT weaken the idempotent-reopen or empty-contacts/schedule assertions.
- `shared/tests/test_analyst_c16.py::test_populated_exact_v2_migrates_in_place_with_zero_high_bits`:
  import and use `V2_SCHEMA_VERSION` instead of `PREVIOUS_SCHEMA_VERSION` (it means v2). Keep
  the zero-high-bits and row-preservation assertions.

### Hard guardrails for this correction

- Do NOT modify or weaken any test that verifies a refusal (populated v1, foreign id, partial
  DB, active/owned lease). The populated-v1 refusal test MUST stay and MUST pass by behavior,
  not by editing the test.
- Additive only; E13 journal settings unchanged; every existing guardrail preserved.
- Full check: `./venv/bin/python -m pytest shared/tests -k analyst -q` passes with ZERO
  failures (the environment has bubblewrap; there are no legitimate "sandbox" failures here).
