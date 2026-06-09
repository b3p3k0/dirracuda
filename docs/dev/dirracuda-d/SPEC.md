# Dirracuda Daemon v1 Specification

## Goal

Provide a runtime-headless, approachable CLI for operating the existing Web UI
without launching or importing Tkinter.

## CLI Contract

Top-level commands:

- `start`, `stop`, `restart`, `status`
- `run`
- `logs [-n N] [--follow]`
- `doctor`
- `config path`, `config check`
- `credentials set USERNAME [--force]`
- `systemd install`, `systemd uninstall`, `systemd status`
- `--help`, `--version`, global `--json`

Exit codes: success `0`, operational/ownership failure `1`, usage `2`, cleanly
inactive status `3`, interrupted foreground/follow `130`.

JSON output keys are exactly `ok`, `command`, `state`, `backend`, `message`, and
`details`. Security posture is additive inside `details` as `security_mode` and
`warnings`.

## Lifecycle Contract

- The root launcher always re-executes `./venv/bin/python`.
- An installed per-user unit selects systemd; otherwise direct mode is used.
- Direct state distinguishes stopped, stale, unhealthy, unmanaged, ambiguous,
  and healthy running processes.
- PID and log files are mode `0600`; PID writes are atomic. Direct logs rotate
  continuously at 5 MiB with three retained files.
- Direct stop verifies ownership, signals the process group, waits 15 seconds,
  and escalates only when required.
- Startup requires valid Web UI security configuration and at least one usable
  credential.
- Explicit lifecycle commands are operator intent; `config.enabled` is
  informational and does not veto them.
- Credential setup validates the username before prompting and refuses any
  password prompt that cannot disable terminal echo.

## Systemd Contract

- Unit location follows `$XDG_CONFIG_HOME/systemd/user`, falling back to
  `~/.config/systemd/user`.
- Installation is user-scoped, enabled, and started immediately.
- V1 does not invoke `loginctl`, enable lingering, or install system-wide files.
- Only units containing the Dirracuda managed marker may be replaced or removed.
- The foreground `run` command is the unit `ExecStart`.

## Compatibility

- Desktop controls use the same facade and show the selected backend.
- `is_running()` and the `StartResult` name remain available for existing code.
- The insecure remote override remains supported with persistent warnings.
- IP literals and localhost are trusted Host values; custom DNS names use the
  canonical `trusted_hosts` config list exposed by both UIs.
- Direct deployment ignores forwarded headers; reverse proxies are outside the
  supported v1 trust model.
