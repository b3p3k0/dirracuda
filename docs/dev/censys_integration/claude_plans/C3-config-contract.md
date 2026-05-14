# C3 - Config + Secrets Contract

Read first:

- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/TASK_CARDS.md
- shared/config.py
- shared/tests/test_config_validation_paths.py

Task:

Implement C3 only.

Scope:

1. Add `censys.*` config accessors and coercion.
2. Enforce safe validation for PAT/org/defaults.
3. Add focused tests for failure paths.

Constraints:

1. No raw PAT in logs/errors.
2. Surgical edits only.
3. No commits.

Return required response format from TASK_CARDS.md.
