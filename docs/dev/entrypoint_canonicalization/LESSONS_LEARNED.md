# LESSONS_LEARNED - Entrypoint Canonicalization

1. A deprecated-but-runnable entrypoint eventually drifts from canonical behavior.
2. Import compatibility and runtime compatibility are separate concerns; preserve the first, constrain the second.
3. Duplicated test suites across canonical + legacy runtime paths amplify maintenance cost and hide source-of-truth.
4. Agent-facing guardrails must be explicit in local repo docs, not only in historical context.
