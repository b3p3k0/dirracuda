# C7 - SMB Adapter

Read first:

- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/FIELD_QUERY_MATRIX.md
- docs/dev/censys_integration/TASK_CARDS.md

Task:

Implement C7 only.

Scope:

1. Extend query builder + service for SMB.
2. Persist SMB rows with stable normalization.
3. Add SMB-specific tests and keep FTP/HTTP green.

Constraints:

1. Preserve cross-protocol dedupe rules.
2. No commits.

Return required response format from TASK_CARDS.md.
