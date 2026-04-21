# Filesize Reduction Lessons Learned

Date: 2026-04-21
Scope: large-file modularization to enforce <=1500-line Python file ceiling.

## Guardrails to Carry Forward

1. Prefer compatibility-first extraction with method binding (`setattr`) when shrinking oversized classes; keep public import paths and call signatures unchanged.
2. Preserve monkeypatch-sensitive seams (`gui.components.dashboard.*`) by keeping runtime lookup helpers (`_mb()` / `_d()`) or equivalent patch-safe indirection.
3. For extracted method modules, use `from __future__ import annotations` to avoid import-time NameError from type annotations.
4. Avoid import-time evaluation of enum defaults in extracted modules; use `strategy=None` and resolve defaults inside the method body.
5. Keep one oversized target per issue/card and run only targeted tests tied to that subsystem before moving on.
6. Split oversized test modules into focused files with shared fixture import modules (not `conftest.py`) to preserve fixture scope conventions.
7. For schema/data paths, keep runtime guards fail-open where existing behavior expects no target drop due to parsing/schema ambiguity.
8. Always capture line-count evidence before/after and run a global `git ls-files '*.py' | xargs wc -l` check to verify no file remains above threshold.
