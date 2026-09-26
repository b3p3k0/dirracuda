# Validation Plan - Keyboard Control Enhancement

Date: 2026-06-30

## Baseline Already Run

```bash
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py gui/tests/test_browser_viewer_keybindings.py -q
```

Result:

```text
16 passed in 0.15s
```

## Automated Checks

Helper contract:

```bash
python3 -m py_compile gui/utils/keybindings.py gui/tests/test_keybindings_contract.py
./venv/bin/python -m pytest gui/tests/test_keybindings_contract.py -q
```

Browser/viewer contract:

```bash
python3 -m py_compile gui/browsers/core.py gui/browsers/smb_browser.py gui/components/file_viewer_window.py gui/components/image_viewer_window.py gui/tests/test_browser_viewer_keybindings.py
./venv/bin/python -m pytest gui/tests/test_browser_viewer_keybindings.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py gui/tests/test_smb_browser_window.py gui/tests/test_smb_virtual_root.py -q
```

Dashboard/global contract:

```bash
python3 -m py_compile dirracuda gui/dashboard/widget.py gui/components/global_shortcuts.py
./venv/bin/python -m pytest gui/tests/test_dirracuda_dashboard_keybindings.py gui/tests/test_help_manual_dialog.py -q
```

Docs and line counts:

```bash
rg -n "Alt\\+2|Alt\\+6|bind_all|Ctrl/Cmd|BackSpace|F5" README.md docs dirracuda gui
wc -l README.md docs/KBD_QUICKREF.md docs/dev/kbd_ctrl_improve/*.md
```

## HI Manual Checks

Dashboard:
- `Alt+1` opens Start Scan.
- `Alt+2` opens Database.
- `Alt+3` opens Accessories.
- `Alt+4` opens Config.
- `Alt+5` opens About.
- `Alt+6..0` does nothing visible.
- `Ctrl/Cmd+Q`, `Ctrl/Cmd+H`, and `Ctrl/Cmd+T` work from dashboard and child windows.

Browsers:
- In SMB/FTP/HTTP browsers, tree focus + `Enter` opens selected row.
- `BackSpace` or `Alt+Up` navigates parent when focus is not in an editing widget.
- Focus a worker/size spinbox and press `BackSpace`; the browser must not navigate.
- `F5` and `Ctrl/Cmd+R` refresh.
- `Esc` and `Ctrl/Cmd+W` close.

Viewers:
- `Esc` and `Ctrl/Cmd+W` close file/image viewers.
- `Ctrl/Cmd+S` saves only when Save to Quarantine is available.

Dialog safety:
- Plain `Enter` in multiline notes/text inserts a newline.
- Destructive actions still require their existing confirmations.
