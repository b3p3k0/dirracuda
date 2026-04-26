# TASK_CARDS - Entrypoint Canonicalization Closure

---

## C1 - Runtime Entrypoint Hardening

Issue:
`gui/main.py` was still runnable and could diverge from canonical runtime behavior.

Scope:
1. Convert `gui/main.py` to strict runtime rejection shim.
2. Preserve import compatibility (`SMBSeekGUI` alias).
3. Add reusable loader utility for canonical `dirracuda` module.

Validation:
```bash
./venv/bin/python -m py_compile dirracuda gui/main.py gui/utils/dirracuda_loader.py
./venv/bin/python gui/main.py
```

---

## C2 - Canonical-Only Test Contract

Issue:
Legacy parity tests against `gui/main.py` encouraged dual-runtime assumptions.

Scope:
1. Remove legacy runtime parity tests for `gui/main.py`.
2. Keep and strengthen canonical `dirracuda` startup/close-flow tests.
3. Add shim smoke test for `gui/main.py` non-zero exit + deprecation guidance.
4. Update shared test harnesses/scenarios to patch canonical module bindings.

Validation:
```bash
./venv/bin/python -m pytest gui/tests/test_dirracuda_close_behavior.py -q
./venv/bin/python -m pytest gui/tests/test_dirracuda_db_unification_startup_ui.py -q
./venv/bin/python -m pytest gui/tests/test_dirracuda_tmpfs_warning_dialog_schedule.py -q
./venv/bin/python -m pytest gui/tests/test_legacy_gui_main_entrypoint.py -q
./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py gui/tests/test_server_ops_fuzz_sequences.py -q
```

---

## C3 - Documentation Guardrails

Issue:
Docs still implied runtime parity between canonical and legacy paths.

Scope:
1. Update technical reference to mark `gui/main.py` as compatibility-only.
2. Add guardrail language in `CLAUDE.md`.
3. Maintain this workspace as entrypoint source-of-truth for future agents.

Validation:
```bash
rg -n "gui/main.py|canonical|entrypoint|compatibility shim" docs/TECHNICAL_REFERENCE.md CLAUDE.md docs/dev/entrypoint_canonicalization/
```
