# Dirracuda Daemon Workspace

Status: v1 implemented, red-team hardened, and validated.

This directory is the canonical planning and execution record for the
runtime-headless `dirracuda-d` Web UI service manager.

Artifacts:

- `SPEC.md` - locked product and engineering contract
- `TASK_CARDS.md` - implementation sequence and completion state
- `VALIDATION_PLAN.md` - automated and manual acceptance gates
- `OPEN_QUESTIONS.md` - resolved product decisions
- `LESSONS_LEARNED.md` - implementation observations

Working rules:

1. `./dirracuda-d` is the supported headless entrypoint.
2. The daemon reuses existing Web UI and scan functionality.
3. CLI and desktop lifecycle controls share one controller.
4. Direct and per-user systemd backends must not run concurrently.
5. No system-wide unit or lingering change is made by v1.
