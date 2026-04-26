# Entrypoint Canonicalization Workspace

This workspace tracks closure of the GUI entrypoint transition.

Canonical runtime path:
- `./dirracuda`

Legacy compatibility path:
- `gui/main.py` (import compatibility only; runtime invocation is intentionally rejected)

Use this workspace when planning or validating:
1. Entrypoint behavior changes.
2. Tests that touch startup/close flow.
3. Agent guardrails and technical reference updates related to runtime launch paths.
