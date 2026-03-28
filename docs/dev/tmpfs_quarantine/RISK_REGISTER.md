# Tmpfs Quarantine v1 - Risk Register

Date: 2026-03-28

## R1 - Mount requires elevated privileges

Risk:
- Runtime mount attempt may fail with `permission denied` for non-root users.

Impact:
- tmpfs mode unavailable despite being enabled.

Mitigation:
1. Fail open to disk quarantine.
2. One-time modal warning and log detail.
3. No privilege escalation attempts from app.

## R2 - Non-Linux platform mismatch

Risk:
- Operators may enable tmpfs on unsupported OS and assume memory-only behavior.

Impact:
- False trust in volatile storage.

Mitigation:
1. Disable tmpfs controls on non-Linux.
2. Force runtime fallback to disk.
3. Show explanatory note in config UI.

## R3 - Mountpoint occupied by non-tmpfs filesystem

Risk:
- Existing mount at `~/.dirracuda/quarantine_tmpfs` is not tmpfs.

Impact:
- Writes could route to unexpected filesystem.

Mitigation:
1. Detect filesystem type before use.
2. Treat non-tmpfs mount as fallback condition.
3. Warn operator once and use disk path.

## R4 - Data loss on close without explicit user consent

Risk:
- tmpfs content is volatile; accidental close deletes quarantine data.

Impact:
- Loss of downloaded evidence before triage.

Mitigation:
1. On close, if tmpfs active and non-empty, show destructive warning.
2. Cancel blocks close.
3. Confirm triggers cleanup and optional unmount.

## R5 - Cleanup/unmount failure during shutdown

Risk:
- File locks or system state can prevent purge/unmount.

Impact:
- Residual tmpfs artifacts and inconsistent operator expectations.

Mitigation:
1. Cleanup errors are logged and surfaced as warnings.
2. App shutdown still proceeds to avoid hang.
3. Rollback runbook includes manual recovery commands.

## R6 - Behavioral regression in existing quarantine flows

Risk:
- Integration could unintentionally change disk-path behavior when tmpfs is disabled.

Impact:
- Browser/extract operations writing to wrong destination.

Mitigation:
1. Root resolution centralized in shared quarantine helper.
2. Explicit tests for disabled-path behavior.
3. Regression checks against existing browser/extract tests.

## R7 - Kernel `noswap` capability drift

Risk:
- Runtime may mis-detect `noswap` support on unusual kernels.

Impact:
- Mount failure for an otherwise valid tmpfs setup.

Mitigation:
1. Gate `noswap` on Linux kernel version check.
2. On failure, fallback to disk + warning rather than hard fail.

## Residual Risk Notes

1. v1 does not expose mountpoint customization.
2. Runtime config hot-reload for tmpfs toggles is best-effort and restart-safe, not hard real-time.
