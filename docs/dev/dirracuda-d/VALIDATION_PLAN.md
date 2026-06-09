# Dirracuda Daemon Validation Plan

## Automated Gates

1. Compile daemon, controller, server, systemd, GUI integration, and tests.
2. Run focused daemon/service/systemd/remote tests.
3. Run focused GUI daemon tests plus the existing experimental dialog suite.
4. Run the full `experimental/webui/tests` suite.
5. Run `bash -n` on modified installer scripts.
6. Render the unit and run `systemd-analyze --user verify`.
7. Run the quick agent-testing lane.
8. Run `git diff --check` and file-size guard checks.

## Manual Gates

- `./dirracuda-d --help`, `--version`, `config check`, `doctor`, and JSON status
  are readable and stable.
- Direct start/status/logs/restart/stop work without a display.
- PID and direct log modes are `0600`.
- Missing credentials block raw server and daemon startup.
- Systemd install creates a managed user unit, enables and starts it, and desktop
  status reports the systemd backend.
- Systemd uninstall stops/removes the unit and direct mode becomes available.
- Remote LAN and allowlist behavior remain unchanged.

## Safety

Automated tests mock systemctl, journalctl, and process signaling. Live user-unit
installation is a manual acceptance action and must not run during the test
suite.

## Validation Record

Daemon v1 baseline completed 2026-06-08:

- Focused daemon/Web UI tests: 121 passed.
- Focused GUI plus legacy experimental dialog tests: 108 passed.
- Full Web UI suite: 518 passed.
- Quick scenario/fuzz lane: 60 passed.
- Final focused daemon/controller/remote tests: 43 passed.
- Final focused GUI plus legacy dialog tests: 104 passed.
- Installer shell syntax, Python compilation, and `git diff --check`: passed.
- Generated unit `systemd-analyze --user verify`: passed.
- Isolated live direct lifecycle: start, status, logs, restart, stop passed;
  PID/log modes were `0600`, stopped status returned `3`.

Red-team remediation completed 2026-06-08:

- Focused hardening/backend matrix: 219 passed.
- Focused GUI/daemon/legacy dialog matrix: 111 passed.
- Full Web UI suite: 566 passed.
- Quick scenario/fuzz lane: 60 passed.
- Installer shell syntax, Python compilation, `git diff --check`, file-size
  guardrails, and generated-unit verification: passed.
- Isolated live direct lifecycle passed for config check, doctor, start, status,
  logs, restart, stop, and stopped exit code `3`.
- Raw HTTP acceptance returned `413` for oversized and chunked login bodies,
  `414` for an oversized request target, and `431` for header count and byte
  limits; rejected connections closed.
- Host acceptance passed for IP literals, localhost, and `scanbox.lan`;
  an untrusted DNS Host returned `400`, and forwarded Host headers were ignored.
- Existing/unknown-user timing medians were 103.45 ms and 104.27 ms in the
  isolated sample. Two hundred unique oversized usernames added no limiter rows
  and no attacker-provided username text to logs.
- The IP-wide spray threshold blocked a subsequent correct password from the
  same source. Stored account/IP subjects were hashed.
- A 67,200-request local load run completed with zero failures and forced a
  live 5 MiB log rollover. Active and retained logs remained mode `0600`.
- Daemon and raw-server startup both failed closed when the disposable
  credential store was hidden.
- The copied 17.6 MiB real-world database retained the same SHA-256 digest
  before and after acceptance testing.

The live tests used an isolated HOME and port with the copied database at
`/tmp/dirracuda-hardening-qa-20260608-QoeDrh`. They did not install a unit into
the operator's actual user manager or exercise an external LAN client.
