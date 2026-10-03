# Dorkbook Lessons Learned

## Query reliability investigation (2026-09-28)

- Distinguish authored phrase quotes from JSON/URL escaping. Decode the actual
  `q` parameter and compare it with the saved/form query before rewriting syntax.
- Never strip quotes from custom or saved queries to compensate for a failed
  upstream. Built-in wording changes are explicit catalog edits backed by live
  comparisons; previously applied defaults have no built-in provenance.
- Read DeGoog `engineTimings` in production, not just in the diagnostic script.
  HTTP 200 plus Wikipedia hits can mask a throttled general-purpose engine.
- Keep API shape, raw hit counts, title clues, and verified open directories
  separate. Title clues are hints, not target verification or a useful-yield PASS.
- Browser engine selections may differ from API defaults. Identify the engine
  producing a useful browser hit before comparing query variants.
- Choose representative catalog cases by stable keys, not substrings containing
  quotes. Otherwise changing authored syntax silently drops test coverage.
- Match rate limiting against error/status fields, never incidental timings or
  counts that happen to contain 429. Record unrun cases when limits stop a run.

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

## Unified defaults and backend checks (2026-09-27)

- A canonical config filename passed as an explicit override bypasses modular
  shards. Resolve canonical identity and use `load_config()` for that profile.
- GUI settings snapshots can overwrite newer module config. Preserve the
  config-owned `se_dork.default_query` from current storage during prefs saves.
- Fallback-to-default reads are useful at startup but unsafe before a save.
  Strict config loading validates JSON roots before materialization; reject
  malformed structures/query types rather than coercing them or replacing data.
- Refresh untouched fields against their last loaded baseline. Preserve manual
  run-local input; explicit Apply alone replaces the matching live field.
- Derive default marks from query text. A selected row, catalog edit, or delete
  must never silently become a configuration change.
- HTTP 200 does not mean search engines succeeded. SearXNG reports failures in
  `unresponsive_engines`; DeGoog also uses `engineTimings` with `rate_limited`
  and `errorReason`. Inspect both, stop on upstream limits, and keep yield
  claims separate from API/response-shape validation.
- Keep migration fixtures pinned to the original three built-ins; a growing
  catalog must not retroactively change what a legacy database contained.
- Render populated catalogs, not just empty widgets: the second provider can
  fall below the viewport, and a small query-row button can overflow layouts.
- Test headless imports in a fresh interpreter. A global `sys.modules` check
  falsely fails after GUI tests and can falsely pass through cached imports.
- Use ephemeral browser messages for cross-tab Apply. Do not persist query
  text in localStorage behind the user's preference-storage opt-in setting.

## Start Scan consolidation (2026-09-27)

- Consistent labels must lead to the same UI, not just similar-looking actions.
  Check and remove superseded launch callbacks and their fallback dialogs.
- Match widget type, width, padding, and right-edge anchoring across provider
  panels; equal labels alone do not ensure alignment. Check resized layouts.
- Validate consolidation by applying different provider queries in the same
  real scan form, then inspect both persisted destinations and the built request.
