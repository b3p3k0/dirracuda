# Agent Testing Workflow (Wave 1 + Wave 4)

This workspace defines a repeatable, low-friction test workflow for server-ops behavior.

## Goal
- Validate server-ops edge cases faster than manual UI testing.
- Keep checks deterministic and headless by default.
- Preserve user-facing behavior while improving confidence and triage speed.

## Lanes
- `quick`: scenario + fast fuzz (`scenario or fuzz`).
- `ci`: same as `quick` (stable subset for pull requests).
- `deep`: `quick` + heavy fuzz (`fuzz_heavy`) and optional GUI smoke.

## Commands
```bash
# quick / ci lane
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
./venv/bin/python scripts/run_agent_testing_workflow.py --lane ci

# deep lane (without GUI smoke)
./venv/bin/python scripts/run_agent_testing_workflow.py --lane deep

# deep lane + optional GUI smoke launch check
./venv/bin/python scripts/run_agent_testing_workflow.py --lane deep --gui-smoke
```

## Failure interpretation
- Scenario failures indicate deterministic contract regressions in server-ops behavior.
- Fast fuzz failures indicate sequence/invariant violations under varied event ordering.
- Heavy fuzz failures indicate lower-probability ordering bugs and should be triaged before release.
- GUI smoke failures indicate startup/regression issues at the entrypoint level.

## Wave 1 scope
- Probe/extract/pry workflow monitor behavior.
- Running Tasks lifecycle and reopen/cancel invariants.
- App close confirm/cancel semantics while work is active/queued.

## Wave 2 additions (current)
- Dashboard scan-task producer lifecycle coverage.
- Dashboard post-scan probe/extract monitor callback lifecycle coverage.
- SE dork probe producer lifecycle coverage (success + failure cleanup).
- Producer-aware deterministic fuzz for dashboard scan tasks.

## Wave 3 additions (current)
- Scan lifecycle scenario coverage for duplicate-task prevention, cancel idempotency, and close-race handling.
- Scan/close deterministic fuzz sequences to exercise ordering around queue/run/wait/cancel/close.
- Main-DB portability coverage for DB-first snapshot reads, backfill idempotency, unresolved sidecar skips, and startup-failure signaling.
- Startup DB-unification UI handler checks for non-blocking warning + retry wiring.

## Wave 4 additions (current)
- ScanManager lock/state contract scenarios (stale/corrupt lock cleanup, valid lock preservation, active-state precedence).
- ScanManager start/interrupt/cleanup contract coverage across SMB/FTP/HTTP.
- Deterministic ScanManager lifecycle fuzz (fast + heavy seeds) with lock/active cleanup invariants.
- Startup DB-unification UI parity tests for both `gui/main.py` and canonical `dirracuda`.

## Flake guard
Run quick lane multiple times before merge to detect seed/order regressions:

```bash
for i in 1 2 3; do ./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick; done
```

## Promotion Boundary
- `pytest.ini` is development-testing infrastructure for custom markers (`scenario`, `fuzz`, `fuzz_heavy`, `gui_smoke`).
- It is not required for ordinary runtime users on `main`.
- Promotion parity guardrails now hard-fail development -> main PRs if `pytest.ini` is present in the promotion diff.

## How To Add A New Producer
Use this template when a module starts registering tasks through `get_running_task_registry()`:
1. Add at least one `scenario` test proving create/update/remove lifecycle.
2. Add a callback test proving reopen and cancel behavior are callable and safe.
3. Add a failure-path test proving terminal cleanup removes task entries.
4. Add at least one `fuzz` sequence action that touches the producer lifecycle.
5. Update `scenario_matrix.md`, `coverage_map.md`, and this README with new IDs/cases.

See companion docs:
- `scenario_matrix.md`
- `coverage_map.md`
- `triage_playbook.md`
