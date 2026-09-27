# U1 — provider-aware Dorkbook storage

Date: 2026-09-27
AUTOMATED: PASS
MANUAL: PENDING
OVERALL: PENDING (HI smoke check; later cards remain separate)

## Issue and root cause

The sidecar encoded destination solely as SMB/FTP/HTTP and rejected entries
without a protocol. That cannot represent a web-search dork independently of
Shodan. Its protocol/query unique index also lacked a provider boundary.

## Change

- Added catalog providers `shodan` and `self_hosted` and a topic (default General).
  This does not rename the existing `searxng` scan queue ID.
- Shodan requires SMB/FTP/HTTP; Self-hosted Search stores NULL protocol. Two
  partial unique indexes prevent duplicate queries even for the NULL case.
- Added schema version 2 and transactional legacy upgrade in `schema.py`.
  Existing API call signatures still default to Shodan. Store callers can list
  all providers or filter by provider, protocol, topic, and search text.
- Migration preserves existing fields, IDs, and the AUTOINCREMENT high-water
  mark. Reopening unchanged built-ins no longer refreshes their timestamps.
- A SQLite-consistent backup is created alongside a recognized legacy sidecar
  before rebuilding. Unsupported fields, generated columns, schema objects,
  versions, or constraints fail without silently discarding data.
- Existing UI and the three original built-ins remain in place. Provider UI,
  application defaults, and the larger catalog are U2–U5 work.
- Corrected README's stale unsaved-apply description and documented the schema.

## Validation run

```bash
./venv/bin/python -m pytest shared/tests/test_dorkbook_store.py shared/tests/test_dorkbook_providers.py shared/tests/test_dorkbook_migration.py gui/tests/test_dorkbook_window.py experimental/webui/tests/test_dorkbook_routes.py -q
```

PASS: **80 passed**, one existing Starlette/httpx deprecation warning.
All network dependencies were mocked; all migration databases were temporary.

```bash
./venv/bin/python -m py_compile experimental/dorkbook/models.py experimental/dorkbook/store.py experimental/dorkbook/schema.py shared/tests/test_dorkbook_providers.py shared/tests/test_dorkbook_migration.py
git diff --check
```

PASS. No new dependencies or general lint configuration.

Cases covered: old positional APIs, new provider CRUD/topic filters, exact
duplicate domains, database-level NULL uniqueness and destination constraints,
built-in protection/collisions, fresh/legacy startup, repeat/concurrent init,
WAL backup contents, backup failure, rebuild/seed failure rollback, row and
timestamp preservation, deleted high IDs, malformed/future schemas, custom
objects, generated fields, and incorrect partial-index predicates.

An independent DA review found the generated-column preservation issue; it was
fixed with `table_xinfo` and a regression test before closeout. The reviewer also
checked backup locking, compatibility, and sequence handling.

## HI smoke check

1. Launch `./dirracuda` and open Accessories → Dorkbook.
2. Confirm the original three tabs/defaults and existing custom dorks/notes
   still appear. The grouped provider UI is not implemented yet.
3. Add a temporary custom dork; close/reopen Dorkbook and confirm it persists.
4. Restart the app, confirm it is still present, then delete that temporary dork.

The actual user sidecar was not migrated during automated validation. Live
query tests belong to later UI/catalog cards. After HI supplied both URLs,
metadata-only requests using Python urllib (15-second timeout) confirmed:

- DeGoog `GET /api/search-tabs`: HTTP 200, JSON `tabs` list present.
- SearXNG `GET /config`: HTTP 200, JSON `engines` list present.

Both are reachable from this workspace. No search query or returned-target
request was made in this connectivity check; live query status remains PENDING.

## Backup and recovery

For the canonical sidecar, migration creates
`dorkbook.db.pre-providers-*.bak` in the same directory. Legacy/override paths
use their actual database filename and parent directory. The backup contains
committed WAL data and is created with owner-only filesystem permissions.
Repeat startup of the upgraded schema does not create another backup.

On migration failure, the transaction rolls back; retain the backup and error
details. An unsupported schema needs review, not deletion or automatic repair.
Do not run older binaries against the upgraded sidecar: their protocol-only
schema checks are incompatible. Before downgrade, stop desktop and Web UI,
preserve the upgraded database, and restore the pre-upgrade snapshot using a
SQLite-aware restore so stale WAL files cannot replay over it. Changes made
after that snapshot are not present in the old-format backup.

## File sizes

Counts below compare the complete current checkpoint against the preceding
commit, including the approved planning notes created earlier in this session.
All Python files are excellent (under 1,201 lines). Technical Reference remains
acceptable (1,664) and below the 1,700-line stop threshold.

| File | Before | After | Rubric |
|---|---:|---:|---|
| `README.md` | 790 | 790 | excellent |
| `docs/TECHNICAL_REFERENCE.md` | 1654 | 1664 | acceptable |
| `docs/dev/dorkbook/ASCII_SKETCHES.md` | 123 | 193 | excellent |
| `docs/dev/dorkbook/CANDIDATE_DORKS.md` | 0 | 96 | excellent |
| `docs/dev/dorkbook/IMPLEMENTATION_PLAN.md` | 0 | 201 | excellent |
| `docs/dev/dorkbook/LESSONS_LEARNED.md` | 14 | 31 | excellent |
| `docs/dev/dorkbook/OPEN_QUESTIONS.md` | 8 | 16 | excellent |
| `docs/dev/dorkbook/README.md` | 29 | 42 | excellent |
| `docs/dev/dorkbook/ROADMAP.md` | 79 | 82 | excellent |
| `docs/dev/dorkbook/SPEC.md` | 87 | 92 | excellent |
| `docs/dev/dorkbook/TASK_CARDS.md` | 226 | 235 | excellent |
| `docs/dev/dorkbook/UNIFIED_LIBRARY_PROPOSAL.md` | 0 | 117 | excellent |
| `docs/dev/dorkbook/VALIDATION_REPORT.md` | 91 | 94 | excellent |
| `experimental/dorkbook/models.py` | 79 | 87 | excellent |
| `experimental/dorkbook/schema.py` | 0 | 227 | excellent |
| `experimental/dorkbook/store.py` | 469 | 384 | excellent |
| `shared/tests/test_dorkbook_migration.py` | 0 | 268 | excellent |
| `shared/tests/test_dorkbook_providers.py` | 0 | 150 | excellent |

This new validation report is also below 1,200 lines.
