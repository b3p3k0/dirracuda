# Running Tasks Contract (Mini Dash v2)

Date: 2026-04-21

## Goal
Provide one reusable task-tracking interface for long-running GUI work so users can always reopen task monitors and never lose visibility.

## Core Components
- Registry: `gui/utils/running_tasks.py`
- Task manager window: `gui/components/running_tasks_window.py`
- Dashboard integration point: `gui/dashboard/widget.py`

## Task Lifecycle API (Dashboard-facing)
- Register:
  - `_register_running_task(task_type, name, state, progress, reopen_callback, cancel_callback) -> task_id`
- Update:
  - `_update_running_task(task_id, name/state/progress/reopen_callback/cancel_callback)`
- Remove:
  - `_remove_running_task(task_id)`

## Required Semantics
- Active + queued only: remove tasks when done/cancelled/failed.
- Reopenable monitors: `reopen_callback` must deiconify/lift/focus task UI.
- Cancelable work: `cancel_callback` should request cancellation without crashing if already stopped.
- Close safety: app close checks active/queued work, confirms with user, cancels gracefully, retries once, then force-terminates.

## Integrations Implemented in This Card
- `scan` tasks:
  - Live output moved to `dashboard_scan_output_dialog` (non-modal, hide/reopen).
  - Queue-aware status updates (`queued` -> `running` -> removed).
- `probe` tasks:
  - Post-scan probe monitor is non-modal and hide/reopen capable.
- `extract` tasks:
  - Post-scan extract monitor is non-modal and hide/reopen capable.

## Wave 1 Legacy Cutover (2026-04-21)
- `server_list_window` batch monitors (`probe`, `extract`, `pry`) now register in the shared running-task service.
- `se_dork_browser_window` probe monitor now registers in the shared running-task service.
- Legacy server-list `Show Task Status` button is removed; reopen path is Running Tasks manager.
- Hide/close of monitor windows never removes active tasks; only terminal states remove tasks.

## How New Modules Plug In
1. Register task when work starts.
2. Update progress/state from UI-safe context.
3. Provide reopen callback for the module’s monitor dialog.
4. Provide cancel callback wired to real runtime cancellation.
5. Remove task on terminal state.
