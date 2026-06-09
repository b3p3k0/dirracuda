# Dirracuda Daemon Task Cards

## C0 - Contract Freeze

Status: Complete

- Lock runtime-headless scope, command set, JSON envelope, exit codes, and
  per-user systemd decisions.
- Record contracts in this workspace.

## C1 - Structured Controller

Status: Complete

- Add structured status/action models.
- Secure PID state, durable rotated logs, ownership checks, bounded stop, and
  direct-process state classification.
- Preserve compatibility predicates.

## C2 - Daemon CLI

Status: Complete

- Add root virtualenv bootstrap and argparse command groups.
- Add lifecycle, foreground, logs, doctor, config, credentials, JSON, help, and
  version behavior.
- Enforce credential preflight on all server startup paths.

## C3 - Per-user Systemd And Installer

Status: Complete

- Add managed unit rendering/install/uninstall/status.
- Automatically select systemd when its user unit exists.
- Extend installer permissions and optional Web UI user-service setup.

## C4 - Desktop Integration

Status: Complete

- Display lifecycle backend.
- Route status and lifecycle actions through the shared controller.
- Route save-and-restart through the controller restart operation.
- Add focused GUI tests outside the oversized legacy dialog test module.

## C5 - Documentation And Closeout

Status: Complete

- Update operator and technical documentation.
- Run focused, full Web UI, GUI, installer, quick-lane, and manual smoke gates.
- Record final validation outcome without committing unless explicitly asked.

## C6 - Red-Team Remediation

Status: Complete

- Bound request bodies, targets, headers, concurrency, backlog, limiter rows,
  and direct-process logs.
- Remove username timing and persistence disclosure paths.
- Enforce canonical trusted DNS hosts while accepting IP literals and localhost.
- Add remote plaintext transition confirmations and persistent security posture
  reporting across daemon, browser, and desktop surfaces.
- Re-run focused, full Web UI, GUI, and adversarial acceptance gates.
