# Claude Task Cards - HTTP DB Import Workstream

Use one card at a time unless HI explicitly approves parallel execution.

---

## Card 1: HTTP State Tables + Migration

Goal:
Add HTTP protocol-specific state tables with additive startup migration.

Primary files:
- `shared/db_migrations.py`
- `tools/db_schema.sql`
- `shared/tests/` migration tests

Prompt seed:

```text
Implement Card 1 from docs/dev/http_module/db_import/03-CLAUDE_TASK_CARDS.md.
Constraints:
- Additive and idempotent migrations only.
- Preserve SMB/FTP behavior and data.
Deliver:
- changed files
- migration notes
- test summary
```

---

## Card 2: Unified List Query API for HTTP Rows

Goal:
Expose protocol-aware host list rows that include HTTP entries (`host_type='H'`) with share-compatible count fields.

Primary files:
- `gui/utils/database_access.py`

Prompt seed:

```text
Implement Card 2 from docs/dev/http_module/db_import/03-CLAUDE_TASK_CARDS.md.
Requirements:
- Add protocol-aware read path for HTTP rows.
- Ensure `Shares > 0` filter remains correct by mapping HTTP counts into `accessible_shares`.
- Keep existing SMB/FTP readers backward compatible.
Deliver:
- changed files
- row schema notes
- validation summary
```

---

## Card 3: Protocol-Aware Write Helpers (HTTP)

Goal:
Ensure HTTP row actions write only to HTTP state tables.

Primary files:
- `gui/utils/database_access.py`
- HTTP row action callsites

---

## Card 4: UI Integration for HTTP Row Type

Goal:
Render and act on HTTP rows without duplicate-IP collisions.

Primary files:
- `gui/components/server_list_window/*`

---

## Card 5: Action Routing + Deletion Semantics

Goal:
Route browse/probe/extract/delete by protocol with no cross-delete.

Primary files:
- `gui/components/server_list_window/actions/*`

---

## Card 6: QA, Docs, and Handoff

Goal:
Close loop with tests and docs for protocol-isolated HTTP row behavior.

Primary files:
- tests + docs under `docs/dev/http_module/db_import/`
