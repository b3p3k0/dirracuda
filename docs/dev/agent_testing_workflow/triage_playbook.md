# Triage Playbook (Wave 1 + Wave 4)

## 1) Reproduce with lane isolation
```bash
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py -q
./venv/bin/python -m pytest gui/tests/test_server_ops_fuzz_sequences.py -q
```

## 2) Classify failure type
- `Scenario`: deterministic behavior contract failure.
- `Fuzz`: ordering/invariant bug under sequence variation.
- `GUI smoke`: startup/entrypoint regression.

## 2.5) Scenario ID -> likely code region
- `S1-S4`: `server_list_window/actions/batch_status.py`, `running_tasks.py`, dashboard/server-list task button wiring.
- `S5-S6`: `dirracuda` and `gui/main.py` close-flow logic; dashboard cancel/force hooks.
- `S7`: `gui/dashboard/widget.py` scan-task methods (`_set_scan_task_*`, `_clear_scan_task`).
- `S8-S9`: `gui/components/dashboard_batch_ops.py` probe/extract monitor dialogs + task callbacks.
- `S10-S11`: `gui/components/se_dork_browser_window.py` probe monitor + shared registry lifecycle.
- `S12`: `gui/dashboard/widget.py` scan-task registration/update/clear + `request_cancel_active_or_queued_work`.
- `S13`: `gui/main.py`/`dirracuda` `_on_closing` cancellation loop + force-terminate retry path.
- `S14-S17`: `gui/utils/scan_manager.py` lock handling (`_cleanup_stale_locks`, `is_scan_active`, `create_lock_file`) + start/interrupt/cleanup lifecycle.
- `S18`: `gui/main.py` and `dirracuda` `_handle_db_unification_result` cleanup prompt and failure/retry branches.
- `D1-D2`: `gui/utils/probe_cache_dispatch.py` DB-first read path and file-cache fallback.
- `D3-D5`: `gui/utils/db_unification.py` backfill/import startup orchestration + `gui/main.py` startup warning handler.
- `D6`: `gui/utils/scan_manager.py` lifecycle ordering invariants under seeded event sequences.

## 3) Check likely root causes
- Orphan task IDs not removed on terminal state.
- Reopen callback detached or stale dialog reference.
- Cancel path not setting cancellation event or not propagating to task state.
- Shared registry listener subscription drift between windows.
- Close flow no longer honoring confirm/cancel contract.
- DB-first read no longer short-circuiting legacy file cache.
- Startup migration failures silently swallowed without pending warning state.
- ScanManager lock file left behind after terminal cleanup path.
- Start failure path leaves manager in active state or loses error reporting.

## 4) Fix guardrails
- Keep task removal tied to terminal states (`success/cancel/fail/forced stop/window close`).
- Keep callbacks stable on task updates (`reopen_callback`, `cancel_callback`).
- Keep close flow deterministic: confirm -> cancel request -> retry/force fallback.

## 5) Validate after fix
```bash
./venv/bin/python -m py_compile \
  gui/tests/_server_ops_harness.py \
  gui/tests/test_server_ops_scenario_matrix.py \
  gui/tests/test_server_ops_fuzz_sequences.py \
  scripts/run_agent_testing_workflow.py

./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
./venv/bin/python scripts/run_agent_testing_workflow.py --lane deep
```
