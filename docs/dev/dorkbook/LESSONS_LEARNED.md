# Dorkbook Lessons Learned

## Provider migration (2026-09-27)

- `PRAGMA table_info` omits generated columns. Inspect `table_xinfo` before a
  rebuild and reject unknown/generated fields rather than silently losing them.
- Preserve `sqlite_sequence`, including deleted high IDs, and skip unchanged
  built-in updates so opening the library does not rewrite timestamps.
- NULL is the absence of a web-search protocol. Give it a provider-specific
  unique index; an ordinary composite UNIQUE with NULL permits duplicates.
- Hold the migration writer lock, then back up using a separate read connection.
  Backing up from the connection already in a write transaction can hang.
- Inspect partial-index predicates, not just column names. A built-in-only
  unique index is not a substitute for uniqueness across custom dorks too.
- Use `GLOB 'sqlite_*'` for the actual internal-object prefix; LIKE treats the
  underscore as a wildcard and can hide user-created objects from a guard.

## Original v1 lessons

Date: 2026-04-19

## Guardrails To Carry Forward

1. Treat built-in seed/upsert as availability-critical startup code.
2. Never let built-in refresh crash app startup due to live user-data uniqueness collisions.
3. On built-in/custom protocol+query collision, preserve custom rows and skip the conflicting built-in change.
4. Keep Dorkbook launch callbacks exception-safe; failures should surface a user-facing error and not bubble traceback noise.
5. Validate sidecar schema at open time against real runtime state (columns + unique indexes), not assumptions.
6. Keep duplicate matching explicit and deterministic (trimmed exact equality per protocol).
7. Add regression tests for each discovered production-risk edge case before/with the fix.
8. Prefer small, surgical fixes and targeted validation suites unless risk profile requires broader runs.
