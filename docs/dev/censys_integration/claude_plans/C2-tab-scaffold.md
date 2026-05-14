# C2 - Experimental Tab Scaffold

Read first:

- docs/dev/censys_integration/SPEC.md
- docs/dev/censys_integration/TASK_CARDS.md
- gui/components/experimental_features/registry.py
- gui/components/experimental_features/se_dork_tab.py

Task:

Implement C2 only.

Scope:

1. Add `Censys Discovery` experimental tab shell.
2. Register tab in experimental registry.
3. Add tests for registry/wiring.

Constraints:

1. No live API run logic yet.
2. Preserve behavior of existing experimental tabs.
3. No commits.

Return required response format from TASK_CARDS.md.
