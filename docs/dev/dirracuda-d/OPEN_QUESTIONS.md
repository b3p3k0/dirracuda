# Resolved Decisions

## D1 - Headless Scope

Runtime headless only. V1 uses the existing repository virtualenv and dependency
set but never imports or launches Tk/display code from daemon entrypoints.

## D2 - Command Scope

Operational v1 includes lifecycle, foreground run, logs, doctor, config
inspection, credential setup, systemd management, help, version, and JSON.

## D3 - Runtime Selection

The root launcher automatically uses `./venv/bin/python` and fails with setup
guidance when it is absent.

## D4 - Credentials

Startup fails closed without a usable credential. Credential setup requires an
explicit username and hidden double-entry password prompt.

## D5 - Systemd

Systemd support is per-user and opt-in. The installer may install, enable, and
start it immediately. V1 does not alter lingering.

## D6 - Backend Selection

An installed user unit selects systemd automatically; otherwise lifecycle
commands use direct-process control.

## D7 - Foreground Configuration

`run` uses only canonical saved Web UI configuration. It has no ad hoc host,
port, or config-path overrides.

## D8 - Red-Team Hardening

All findings ship in one pass. Remote plaintext HTTP remains available with
transition confirmation and persistent warnings. Host validation accepts IP
literals and localhost automatically and requires explicit trusted DNS names.
Legacy rate-limit state is reset and vacuumed during the schema-v2 migration.
