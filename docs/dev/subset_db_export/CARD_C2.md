# Card C2 — Server List: Save Selected to Database

Status: authorized by HI 2026-10-04 (plan `PLAN.md`, decision D3; HI delegated card orchestration to RA). DA card.
Repo: dirracuda · Branch: development · Python: `./venv/bin/python`
Depends on: card C1 — `DBToolsEngine.export_subset(output_path, row_keys, *, include_credentials, progress_callback, cancel_event)` in `gui/utils/db_tools_engine_subset_methods.py`.

## Context (stands alone)

The Server List window (`gui/components/server_list_window/window.py`) shows
SMB/FTP/HTTP hosts. Each row dict has a `row_key` (`"S:<id>"` etc.).
`table.get_selected_server_data(tree, filtered_servers)` returns the selected
row dicts. Today an "Export Selected" menu (CSV/JSON/ZIP) exists in
`server_list_window/export.py::show_export_menu`, but only **Ctrl+E** reaches it.

## Scope

### 1. Entry points

- **Context menu:** add `💾 Save Selected to Database…` as a selection command
  (use `_add_selection_command` so it disables with no selection). Place it in
  its own separator group after the Copy IP / Copy URL group.
- **Button panel:** add an `📤 Export ▾` button in `_create_button_panel`, before
  the Delete button, styled `button_secondary`. Clicking it posts a menu under
  the button:
  - `Selected → Database…` · `Selected → CSV` · `Selected → JSON` · `Selected → ZIP`
  - separator
  - `All shown → Database…` · `All shown → CSV` · `All shown → JSON` · `All shown → ZIP`
  - The Selected items are disabled when nothing is selected. The All shown items are disabled when the list is empty.
  - The CSV/JSON/ZIP items call the existing `export.export_servers_to_format`. Do not change its behavior.
- Keep **Ctrl+E**, unchanged.

### 2. Flow (new code in `server_list_window/export.py`; `window.py` gets only wiring)

1. **Save As:** `filedialog.asksaveasfilename(parent=window, title="Save Hosts to Database",
   defaultextension=".db", filetypes=[("SQLite databases", "*.db"), ("All files", "*.*")],
   initialfile=f"dirracuda_subset_{YYYYMMDD_HHMMSS}.db")`. Cancel → stop.
   The OS dialog handles the overwrite confirm.
2. **Active DB guard:** if the chosen path resolves to the active DB, show an error
   (`safe_messagebox`) and stop. The engine also refuses, but the UI catches it first with
   a clear message. Get the active DB path from the window's existing reader/config.
   Find the right accessor; do not hardcode a path.
3. **Confirm dialog** (`Toplevel`, `grab_set`, `ensure_dialog_focus` last):
   - "Save N hosts (SMB a · FTP b · HTTP c) to <filename>?"
   - Checkbox `Include saved credentials`, **checked** by default (HI D2), with a
     one-line warning in secondary text: "Anyone with this file can read these credentials."
   - Note line: "Analyst results are not included."
   - Buttons: `Save` (primary) / `Cancel`. `WM_DELETE_WINDOW` = Cancel.
4. **Progress dialog:** runs `export_subset` in a `threading.Thread(daemon=True)`.
   The worker only writes progress and the result into a thread-safe holder (a queue or
   lock-guarded dict). The UI thread polls with `after(100, …)` and owns every widget
   update and the teardown. A `Cancel` button and `WM_DELETE_WINDOW` both set the
   `cancel_event`. **Never touch Tk from the worker.**
5. **Result:** on success, `safe_messagebox.showinfo` with the host counts, the row
   total, the size (MB) and the path, plus any `warnings` and `missing` count. On
   failure: `showerror` with the engine `error`. On cancel: a short info "Export cancelled."

### 3. Conventions (enforced by guardrail tests)

- `gui.utils.safe_messagebox` only. No direct `tkinter.messagebox`.
- Theme via `SMBSeekTheme.apply_to_widget(widget, <named style>)`, named styles only.
- `ensure_dialog_focus(dialog, parent)` as the last step of each grabbed dialog.
- `window.py` is 1236 lines: add wiring only (menu item, button, thin handlers).

### 4. Out of scope

No engine changes. No change to the CSV/JSON/ZIP export logic. No DB Tools dialog changes.

## Tests (new `gui/tests/test_server_list_subset_export.py`; run under xvfb)

Monkeypatch `filedialog.asksaveasfilename`, the engine, and `safe_messagebox`.
**A real messagebox blocks the suite under Xvfb**, so every messagebox call must be patched.

- The context menu has the new item, and it is disabled with no selection.
- `Export ▾` menu: item labels; enabled/disabled per the selection and empty list.
- Save As cancel → engine not called.
- Active DB path chosen → error shown, engine not called.
- Confirm Cancel / window close → engine not called.
- Confirm Save → engine called with exactly the selected `row_keys` and the checkbox value (both True and False).
- All shown → Database passes every `row_key` in `filtered_servers`.
- The worker never calls Tk. Use a fake engine that runs in the thread and asserts `threading.current_thread() is not threading.main_thread()`. Results are delivered via polling.
- Cancel during progress sets `cancel_event`, and the dialog closes on the UI thread.
- Success / failure / cancel each show the correct messagebox.
- CSV/JSON/ZIP items call `export_servers_to_format` with the "selected" / "all" data.

## Done when

```
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_server_list_subset_export.py -v
xvfb-run -a ./venv/bin/python -m pytest gui/tests -k "server_list or messagebox or theme or export" -q
```

All pass in the real venv. Report the commands, the output and the changed files. Do not commit.
