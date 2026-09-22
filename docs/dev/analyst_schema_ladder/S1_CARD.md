# S1 — One Schema Ladder

- Date: 2026-09-22
- Branch: `feature/ollama-analyst`
- Type: **code.** Behaviour-preserving refactor.
- Status: **implemented**, awaiting HI review.
- Ceremony: card + HI review. No contract freeze — this changes no contract.

## Why

Analyst's SQLite sidecar decided *"what version is this database, and may I upgrade it?"*
in seven places. Each restated the same fact by hand.

During N1 the schema went v6 → v7 and six of the seven were updated.
`store.py::_audit_existing_database` was missed. It knew versions 1–5 and "current", so a
v6 database — **every existing install** — matched no branch and hit:

> refusing to open a partial, foreign, or versioned Analyst database

The app would have refused to open the user's database instead of upgrading it. A
migration test caught it before commit; schema-only unit tests would not have.

The v6 commit (`3d3c47b`) *did* remember `store.py`. So the trap was survivable — but only
by author memory, which is precisely what failed.

## What changed

One ordered `_LADDER` in `db_schema.py`. Every version-aware site derives from it.

```python
@dataclass(frozen=True)
class _SchemaStep:
    version: int
    ddl: tuple[str, ...]          # what this version adds
    idle_query: str | None        # in-flight probe its new table needs
    row_validator: object | None  # row invariants it introduces
```

| Helper | Feeds |
| --- | --- |
| `_ddl_through(version)` | `_expected_snapshot` |
| `_ddl_after(version)` | both migration passes, fresh-create tail |
| `_idle_queries_through(version)` | `validate_migration_candidate` |
| `MIGRATABLE_VERSIONS` | every "is this an upgrade source?" test |

### Sites collapsed

| Was | Now |
| --- | --- |
| `initialize_schema` first-pass ladder | one `in MIGRATABLE_VERSIONS` test |
| `initialize_schema` second-pass ladder + 6 DDL chains | one test + `_ddl_after(source)` |
| fresh-create branch, five DDL loops | `_TABLE_DDL`/`_INDEX_DDL` + `_ddl_after(V2)` |
| `_expected_snapshot`, 7 branches | `_ddl_through(version)` |
| row-validator ladder (stopped at v4) | walks the ladder — gap closed |
| 6 × `validate_vN_migration_candidate` | `validate_migration_candidate(conn, version)` |
| `store.py::_audit_existing_database` ladder | **deleted** |

`store.py`'s `db_schema` import dropped from 20 names to 9.

### Kept deliberately

- **`validate_schema_v1..v6`** stay as one-line wrappers. Only v1 and v2 have external
  callers, but keeping all six avoids test churn. (HI decision.)
- **v1 is still a special case.** It is admitted only when *pristine* — zero domain rows and
  an unowned generation-zero lease — not merely idle. That logic moved to
  `_validate_v1_pristine`, unchanged.
- **`_TABLE_DDL` / `_INDEX_DDL` are still read at call time** in the fresh-create branch,
  because `test_analyst_c8.py:604` monkeypatches them to force a mid-DDL rollback.
- **Every `_VN_ADDITIONAL_DDL` tuple keeps its exact name, contents and statement count**,
  because `test_analyst_c9_durable.py:761` derives a `parametrize` range from their `len()`.

## Adding a schema version now

Three edits, all in `db_schema.py`:

1. Define `_V8_ADDITIONAL_DDL`.
2. Bump `SCHEMA_VERSION`, add `V7_SCHEMA_VERSION`, extend `KNOWN_SCHEMA_VERSIONS`,
   move `PREVIOUS_SCHEMA_VERSION`.
3. Append one `_SchemaStep`.

Then add the new version's digest to `_GOLDEN_SNAPSHOT_SHA256` in the guardrail test —
deliberately, because a guardrail asserts every known version has one.

`store.py` needs no edit. That is the point of the card.

## Guardrail

`shared/tests/test_analyst_schema_ladder_guardrail.py`, 40 tests:

- SHA-256 of `_expected_snapshot()` for v1–v7, captured before the refactor. **No change
  to this module may ever move a historical schema.**
- `_ddl_through()` rebuilds every version to exactly that digest.
- Parametrized over `MIGRATABLE_VERSIONS`: build a database at that version, open it,
  assert it reaches `SCHEMA_VERSION` and validates — plus an idempotency pass.
- `store.py` names no individual schema version and imports no per-version validator.
- `db_schema` exports only the generic candidate validator.

### Proof it guards

Both failure modes were injected and observed:

| Injection | Result |
| --- | --- |
| Delete the v6 branch from `store._audit_existing_database` (the literal N1 bug) | `test_every_migratable_version_upgrades_to_current[6]` fails with "refusing to open a partial, foreign, or versioned Analyst database"; v1–v5 still pass |
| Delete the v6 step from `_LADDER` | 6 tests fail, including the **v6 and v7** digests — removing v6's DDL changes v7's cumulative schema too |

## Validation

| Check | Result |
| --- | --- |
| ladder guardrail | 40 passed |
| `shared/tests/` + `experimental/` | 3472 passed, 1 failed |
| that failure | `test_daemon_cli.py::test_daemon_modules_import_without_tkinter` — pre-existing, fails identically at pre-N1 `73f7896` |
| `gui/tests/` | 2082 passed |
| `scripts/tests/` | **not run** — nothing under `scripts/` imports `db_schema.py`; that suite costs 99 minutes |

End-to-end, against real data:

- The live v7 sidecar (1 profile, 4 runs) opens and validates.
- A hand-built v6 upgrades to v7, with `analyst_llm_profile` and
  `analyst_runs.profile_id` present afterwards. **This is the exact scenario the N1 bug
  would have broken.**
- The real Analyst tab lists 17 models against loopback Ollama.

## File sizes

| File | Pre-N1 | After N1 | Now |
| --- | --- | --- | --- |
| `db_schema.py` | 1272 | 1379 | **1263** |
| `store.py` | 942 | 946 | **926** |

`db_schema.py` is smaller than before N1, despite carrying an extra schema version.

## Review

Behaviour-preserving, but this is the upgrade path for every user database.
**Flagged for HI review before merge.** Two commits:

- `19638f2` — ladder + golden-snapshot guardrail, pure addition, nothing rewired
- `4207d58` — the rewire
