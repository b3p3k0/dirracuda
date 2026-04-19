# Dorkbook Lessons Learned

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
