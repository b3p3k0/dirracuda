# Coverage Map (Wave 1 + Wave 4)

## New tests
- `gui/tests/test_server_ops_scenario_matrix.py`
  - S1: probe monitor hide/reopen/remove
  - S2: extract cancel + terminal cleanup
  - S3: pry mixed-selection guardrail
  - S4: shared count/state sync across dashboard + server-list
  - S5/S6: app close cancel/confirm paths
  - S7: dashboard scan-task queued/running/waiting/clear lifecycle
  - S8/S9: dashboard post-scan probe/extract monitor callback lifecycle
  - S10/S11: se dork probe task success/failure cleanup lifecycle
  - S12: dashboard scan-task duplicate prevention + cancel idempotency
  - S13: close-confirm race while active work clears mid-shutdown

- `gui/tests/test_server_ops_fuzz_sequences.py`
  - Deterministic seeded event sequences for monitorable batch lifecycle
  - Deterministic seeded dashboard scan-task lifecycle sequences
  - Deterministic seeded dashboard scan + close event-order sequences
  - Fast fuzz (`@pytest.mark.fuzz`)
  - Heavy fuzz (`@pytest.mark.fuzz_heavy`)

- `gui/tests/test_scan_manager_lifecycle.py`
  - S14: lock cleanup/preservation contracts on startup
  - S15: start admission contracts across SMB/FTP/HTTP
  - S16: start-failure cleanup contracts across SMB/FTP/HTTP
  - S17: interrupt + terminal cleanup contracts

- `gui/tests/test_scan_manager_fuzz.py`
  - D6: deterministic seeded ScanManager lifecycle event-order invariants
  - Fast fuzz (`@pytest.mark.fuzz`)
  - Heavy fuzz (`@pytest.mark.fuzz_heavy`)

- `gui/tests/test_scan_manager_config_path.py`
  - Scenario-marked config-path propagation checks for SMB/FTP starts

- `gui/tests/test_probe_cache_dispatch.py`
  - D1/D2: DB-first probe snapshot precedence and protocol cache fallback behavior

- `gui/tests/test_db_unification.py`
  - D3: probe backfill idempotent rerun behavior
  - D4: unresolved sidecar host skip/report behavior
  - D5(core): startup unification failure payload/state handling

- `gui/tests/test_db_unification_startup_ui.py`
  - D5/UI + S18(main): non-blocking startup warning/retry + cleanup prompt handling in `gui/main.py`

- `gui/tests/test_dirracuda_db_unification_startup_ui.py`
  - S18(dirracuda): canonical entrypoint parity for cleanup prompt + failure warning/retry + non-blocking prompt exceptions

## Existing tests leveraged by this workflow
- `gui/tests/test_server_list_running_tasks_integration.py`
- `gui/tests/test_running_tasks_registry.py`
- `gui/tests/test_action_routing.py`
- `gui/tests/test_main_close_behavior.py`
- `gui/tests/test_dirracuda_close_behavior.py`

## Shared helper
- `gui/tests/_server_ops_harness.py`
  - Deterministic test doubles for roots, dialogs, buttons, server-list mixin stubs
  - Dashboard batch-monitor and SE dork probe producer test doubles
