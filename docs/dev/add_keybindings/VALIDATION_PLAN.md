# Validation Plan - Keyboard Accessibility Phase 1

Date: 2026-05-07

## Automated Validation

## 1) Helper-Level Contracts

```bash
python3 -m py_compile gui/utils/keybindings.py gui/tests/test_keybindings_contract.py
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py -q
```

## 2) Dashboard Shortcut Wiring

```bash
python3 -m py_compile dirracuda gui/dashboard/widget.py gui/tests/test_dirracuda_dashboard_keybindings.py
./venv/bin/python -m pytest gui/tests/test_dirracuda_dashboard_keybindings.py -q
```

## 3) Scan Dialog Flow

```bash
python3 -m py_compile \
  gui/components/unified_scan_dialog.py \
  gui/components/scan_preflight.py \
  gui/components/scan_results_dialog.py \
  gui/components/scan_dork_editor_dialog.py
./venv/bin/python -m pytest \
  gui/tests/test_scan_dialog_nonblocking_singleton.py \
  gui/tests/test_scan_results_dialog.py \
  gui/tests/test_scan_preflight_probe_depth.py -q
```

## 4) Ops/Admin Surfaces

```bash
python3 -m py_compile \
  gui/components/app_config_dialog.py \
  gui/components/db_tools_dialog.py \
  gui/components/running_tasks_window.py \
  gui/components/server_list_window/window.py \
  gui/components/server_list_window/details.py \
  gui/components/batch_extract_dialog.py \
  gui/components/batch_summary_dialog.py \
  gui/components/clamav_results_dialog.py
./venv/bin/python -m pytest \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_db_tools_dialog.py \
  gui/tests/test_server_list_running_tasks_integration.py \
  gui/tests/test_clamav_results_dialog.py \
  gui/tests/test_server_list_details_probe_section.py -q
```

## 5) Phase 2 Browser/Viewer Surfaces

```bash
python3 -m py_compile \
  gui/utils/keybindings.py \
  gui/browsers/core.py \
  gui/browsers/smb_browser.py \
  gui/components/file_viewer_window.py \
  gui/components/image_viewer_window.py \
  gui/tests/test_browser_viewer_keybindings.py
./venv/bin/python -m pytest \
  gui/tests/test_keybindings_contract.py \
  gui/tests/test_browser_viewer_keybindings.py \
  gui/tests/test_ftp_browser_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_smb_browser_window.py \
  gui/tests/test_smb_virtual_root.py -q
```

## HI Manual Validation (Keyboard-Only)

1. Dashboard:
   - Verify `Alt+1..6` launches mapped actions.
   - Verify `Alt+7..0` no-op without side effects.
   - Verify `Ctrl/Cmd+T` toggles theme from dashboard and child dialogs.
   - Verify `Ctrl/Cmd+H` opens Help placeholder dialog from dashboard and child dialogs.
   - Verify `Ctrl/Cmd+Q` uses normal quit flow (running-task confirmation still appears when needed).
2. Unified Scan flow:
   - `Enter` starts from main scan dialog.
   - `Esc` cancels dialogs.
   - Preflight dialogs honor Enter/Esc/Ctrl+W.
3. App Config + DB Tools:
   - App Config: `Enter` and `Ctrl/Cmd+S` save, `Esc` cancel.
   - DB Tools: `Esc`/`Ctrl/Cmd+W` close.
4. Server List + Running Tasks:
   - Server List tree `Enter` opens selected details.
   - Running Tasks `Enter` reopens selected monitor.
   - Window close shortcuts function.
5. Shared op dialogs:
   - Batch Extract settings, Batch Summary, ClamAV results respond to advertised keys.
6. Multiline exception:
   - In Server Detail notes, plain `Enter` inserts newline (does not close).
   - `Ctrl/Cmd+Enter` can submit only where explicitly supported.
7. Browser/viewer contract:
   - In SMB/FTP/HTTP browsers, verify `Enter`, `BackSpace`/`Alt+Up`, `F5`/`Ctrl/Cmd+R`, and close shortcuts.
   - In file/image viewers, verify close shortcuts and `Ctrl/Cmd+S` behavior only when Save is available.
