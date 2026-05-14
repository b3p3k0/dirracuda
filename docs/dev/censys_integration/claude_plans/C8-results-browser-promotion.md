# C8 - Results Browser + Promotion

Read first:

- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/TASK_CARDS.md
- gui/utils/sidecar_promotion.py
- gui/components/dashboard_experimental.py

Task:

Implement C8 only.

Scope:

1. Add Censys sidecar browser window.
2. Add manual single/bulk promotion hooks.
3. Reuse shared sidecar promotion contract.
4. Add browser/promotion tests.

Constraints:

1. No direct main-DB write bypass.
2. Preserve behavior when Server List Browser is unopened.
3. No commits.

Return required response format from TASK_CARDS.md.
