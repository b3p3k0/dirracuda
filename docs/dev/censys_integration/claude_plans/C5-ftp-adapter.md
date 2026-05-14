# C5 - FTP Adapter + Sidecar Store

Read first:

- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/FIELD_QUERY_MATRIX.md
- docs/dev/censys_integration/TASK_CARDS.md

Task:

Implement C5 only.

Scope:

1. Add sidecar store schema and guards.
2. Add FTP run orchestration and persistence.
3. Add deterministic dedupe.
4. Add focused store/service tests.

Constraints:

1. Schema checks at open are required.
2. No writes on preflight/auth failures.
3. No commits.

Return required response format from TASK_CARDS.md.
