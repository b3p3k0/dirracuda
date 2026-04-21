# Monitor Reopen Audit - 2026-04-21

## Goal
Confirm whether the "hidden monitor cannot be reopened from Running Tasks" pattern appears outside server-list batch jobs.

## Scope checked
- `gui/components/server_list_window/actions/batch_status.py`
- `gui/components/se_dork_browser_window.py`
- `gui/components/dashboard_batch_ops.py`
- `gui/components/running_tasks_window.py`
- `gui/components/pry_status_dialog.py`

## Findings
- **Server-list batch monitor** had a wrapper-liveness mismatch:
  - Reopen path checked `dialog.winfo_exists()` directly.
  - `BatchStatusDialog` is wrapper-backed (`dialog.window.winfo_exists()`), so callback was skipped.
  - Fixed by making `_widget_exists()` wrapper-aware.
- **SE dork probe monitor** uses `reopen_callback=status_dialog.show` directly and checks `status_dialog.window.winfo_exists()` for UI loop updates; no reopen bug found in this pattern.
- **Dashboard monitors** are raw Tk dialogs (`Toplevel`) and reopen callbacks target live dialogs correctly.

## UX alignment changes applied
- Removed server-list `Stop Batch` footer button.
- Added server-list `Running Tasks (N)` footer button wired to shared task manager.
- Server-list now subscribes to shared task registry for live count/state updates and tears down subscription/window on close.

## Guardrail
Any monitor dialog that uses a wrapper object must expose and/or be checked via its underlying Tk window state for reopen viability.
