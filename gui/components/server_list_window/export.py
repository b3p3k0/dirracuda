"""
Server List Export Operations

Handles CSV/JSON/ZIP export functionality with progress dialogs.
All operations use explicit dependency injection to avoid tight coupling.
"""

import tkinter as tk
import threading
from pathlib import Path
from queue import Empty, Queue
from tkinter import ttk, filedialog
from gui.utils import safe_messagebox as messagebox
from gui.utils.db_tools_engine import DBToolsEngine
from gui.utils.dialog_helpers import ensure_dialog_focus
from datetime import datetime
from typing import Dict, List, Any


def show_export_dropdown(parent_window, button, selected_data, all_data, theme,
                         export_engine, active_db_path):
    """Post the button's export choices using the current selection and filters."""
    menu = tk.Menu(parent_window, tearoff=0)
    for label, export_type, data in (
        ("Selected", "selected", list(selected_data)),
        ("All shown", "all", list(all_data)),
    ):
        if export_type == "all":
            menu.add_separator()
        state = tk.NORMAL if data else tk.DISABLED
        menu.add_command(
            label=f"{label} → Database…", state=state,
            command=lambda rows=data: save_hosts_to_database(
                parent_window, rows, theme, active_db_path
            ),
        )
        for fmt in ("csv", "json", "zip"):
            menu.add_command(
                label=f"{label} → {fmt.upper()}", state=state,
                command=lambda rows=data, kind=export_type, format_type=fmt: export_servers_to_format(
                    rows, kind, format_type, parent_window, theme, export_engine
                ),
            )
    menu.post(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())


def _hosts_text(count):
    return f"{count} host" if count == 1 else f"{count} hosts"


def _host_counts_text(counts):
    return f"SMB {counts['S']} · FTP {counts['F']} · HTTP {counts['H']}"


def save_hosts_to_database(parent_window, servers, theme, active_db_path):
    """Choose a destination and confirm the host subset and credential policy."""
    if not servers:
        return
    row_keys = [row['row_key'] for row in servers]
    filename = filedialog.asksaveasfilename(
        parent=parent_window, title="Save Hosts to Database", defaultextension=".db",
        filetypes=[("SQLite databases", "*.db"), ("All files", "*.*")],
        initialfile=f"dirracuda_subset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
    )
    if not filename:
        return
    if Path(filename).resolve() == Path(active_db_path).resolve():
        messagebox.showerror(
            "Export Error", "Choose a different file. The active database cannot be overwritten.",
            parent=parent_window,
        )
        return

    dialog = tk.Toplevel(parent_window)
    dialog.title("Save Hosts to Database")
    dialog.transient(parent_window)
    theme.apply_to_widget(dialog, "main_window")
    counts = {kind: sum(key.startswith(kind + ':') for key in row_keys) for kind in 'SFH'}
    summary = tk.Label(
        dialog, text=f"Save {_hosts_text(len(row_keys))} ({_host_counts_text(counts)}) to {Path(filename).name}?",
        wraplength=520, justify=tk.LEFT,
    )
    theme.apply_to_widget(summary, "label")
    summary.pack(padx=20, pady=(20, 10), anchor="w")
    include_credentials = tk.BooleanVar(master=dialog, value=True)
    checkbox = tk.Checkbutton(dialog, text="Include saved credentials", variable=include_credentials)
    theme.apply_to_widget(checkbox, "checkbox")
    checkbox.pack(padx=20, anchor="w")
    theme.create_styled_label(
        dialog, "Anyone with this file can read these credentials.", "small",
        fg=theme.colors["text_secondary"],
    ).pack(padx=20, pady=(0, 10), anchor="w")
    note = tk.Label(dialog, text="Analyst results are not included.")
    theme.apply_to_widget(note, "label")
    note.pack(padx=20, anchor="w")

    def save():
        credentials = include_credentials.get()
        dialog.destroy()
        _run_subset_export(parent_window, filename, row_keys, credentials, theme, active_db_path)

    buttons = tk.Frame(dialog)
    theme.apply_to_widget(buttons, "main_window")
    buttons.pack(padx=20, pady=20, anchor="e")
    save_button = tk.Button(buttons, text="Save", command=save)
    theme.apply_to_widget(save_button, "button_primary")
    save_button.pack(side=tk.LEFT, padx=(0, 8))
    cancel_button = tk.Button(buttons, text="Cancel", command=dialog.destroy)
    theme.apply_to_widget(cancel_button, "button_secondary")
    cancel_button.pack(side=tk.LEFT)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.grab_set()
    ensure_dialog_focus(dialog, parent_window)


def _run_subset_export(parent_window, filename, row_keys, include_credentials, theme, active_db_path):
    """Run the engine off-thread; polling owns all Tk work and teardown."""
    updates = Queue()
    cancel_event = threading.Event()
    dialog = tk.Toplevel(parent_window)
    dialog.title("Saving Hosts to Database")
    dialog.transient(parent_window)
    theme.apply_to_widget(dialog, "main_window")
    label = tk.Label(dialog, text="Preparing export…", wraplength=480)
    theme.apply_to_widget(label, "label")
    label.pack(padx=20, pady=(20, 10))
    progress_bar = ttk.Progressbar(dialog, length=400, mode="determinate")
    theme.apply_to_widget(progress_bar, "progress_bar")
    progress_bar.pack(padx=20, pady=10)
    cancel_button = tk.Button(dialog, text="Cancel", command=cancel_event.set)
    theme.apply_to_widget(cancel_button, "button_secondary")
    cancel_button.pack(padx=20, pady=(10, 20))
    dialog.protocol("WM_DELETE_WINDOW", cancel_event.set)

    def worker():
        try:
            result = DBToolsEngine(str(active_db_path)).export_subset(
                filename, row_keys, include_credentials=include_credentials,
                progress_callback=lambda percent, text: updates.put(("progress", (percent, text))),
                cancel_event=cancel_event,
            )
        except Exception as exc:
            result = dict(success=False, cancelled=False, error=str(exc))
        updates.put(("result", result))

    def poll():
        nonlocal poll_id
        poll_id = None
        while True:
            try:
                kind, value = updates.get_nowait()
            except Empty:
                break
            if kind == "result":
                dialog.destroy()
                _show_subset_result(parent_window, value)
                return
            percent, text = value
            if percent >= 0:
                progress_bar.configure(value=percent)
            label.configure(text=text)
        poll_id = dialog.after(100, poll)

    def on_destroy(event):
        # Parent shutdown can destroy this dialog before the worker finishes.
        if event.widget == dialog:
            cancel_event.set()
            if poll_id is not None:
                dialog.after_cancel(poll_id)

    dialog.bind("<Destroy>", on_destroy, add="+")
    poll_id = dialog.after(100, poll)
    threading.Thread(target=worker, daemon=True).start()
    dialog.grab_set()
    ensure_dialog_focus(dialog, parent_window)


def _show_subset_result(parent_window, result):
    if result.get('cancelled'):
        messagebox.showinfo("Export Cancelled", "Export cancelled.", parent=parent_window)
    elif not result['success']:
        messagebox.showerror("Export Error", result['error'], parent=parent_window)
    else:
        counts = result['hosts']
        text = (
            f"Saved {_hosts_text(sum(counts.values()))} ({_host_counts_text(counts)})\n"
            f"Rows: {sum(result['rows'].values())}\n"
            f"Size: {result['size_bytes'] / (1024 * 1024):.2f} MB\n"
            f"File: {result['output_path']}\n"
            f"Missing hosts: {len(result['missing'])}"
        )
        if result['warnings']:
            text += "\nWarnings:\n" + "\n".join(result['warnings'])
        messagebox.showinfo("Export Complete", text, parent=parent_window)


def show_export_menu(parent_window, server_data, export_type, theme, export_engine):
    """
    Show export format selection menu for server data.

    Args:
        parent_window: Parent window for menu positioning
        server_data: List[Dict] of server data to export
        export_type: "selected" or "all" for filename generation
        theme: Theme object for styling
        export_engine: Export engine instance from get_export_engine()
    """
    if not server_data:
        messagebox.showwarning("No Data", "No servers to export.")
        return

    menu = tk.Menu(parent_window, tearoff=0)
    menu.add_command(
        label=f"Export {export_type.title()} as CSV",
        command=lambda: export_servers_to_format(
            server_data, export_type, 'csv', parent_window, theme, export_engine
        )
    )
    menu.add_command(
        label=f"Export {export_type.title()} as JSON",
        command=lambda: export_servers_to_format(
            server_data, export_type, 'json', parent_window, theme, export_engine
        )
    )
    menu.add_command(
        label=f"Export {export_type.title()} as ZIP (CSV+JSON)",
        command=lambda: export_servers_to_format(
            server_data, export_type, 'zip', parent_window, theme, export_engine
        )
    )

    # Show menu at mouse position
    try:
        menu.post(parent_window.winfo_pointerx(), parent_window.winfo_pointery())
    except tk.TclError:
        menu.post(parent_window.winfo_rootx() + 50, parent_window.winfo_rooty() + 50)


def export_servers_to_format(servers, export_type, format_type, parent_window, theme, export_engine):
    """
    Export servers using centralized data export engine.

    Args:
        servers: List of server dictionaries to export
        export_type: Type of export ("selected" or "all")
        format_type: Export format (csv, json, zip)
        parent_window: Parent window for dialogs
        theme: Theme object for styling
        export_engine: Export engine instance
    """
    if not servers:
        messagebox.showwarning("No Data", "No servers to export.")
        return

    # Generate filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extensions = {'csv': '.csv', 'json': '.json', 'zip': '.zip'}
    filetypes_map = {
        'csv': [("CSV files", "*.csv"), ("All files", "*.*")],
        'json': [("JSON files", "*.json"), ("All files", "*.*")],
        'zip': [("ZIP files", "*.zip"), ("All files", "*.*")]
    }

    default_filename = f"smbseek_servers_{export_type}_{timestamp}{extensions[format_type]}"

    # Ask for save location
    filename = filedialog.asksaveasfilename(
        title=f"Export {export_type.title()} Servers ({format_type.upper()})",
        defaultextension=extensions[format_type],
        filetypes=filetypes_map[format_type],
        initialfile=default_filename
    )

    if not filename:
        return

    try:
        # Create progress dialog
        progress_window = tk.Toplevel(parent_window)
        progress_window.title("Exporting...")
        progress_window.geometry("300x120")
        progress_window.transient(parent_window)
        progress_window.grab_set()

        progress_label = tk.Label(progress_window, text="Preparing export...")
        progress_label.pack(pady=10)

        progress_bar = ttk.Progressbar(progress_window, length=250, mode='determinate')
        progress_bar.pack(pady=10)

        progress_window.update()

        # Prepare filters applied info - simplified for enhanced tracking
        filters_applied = {}
        # Note: Filter metadata would need to be passed from ServerListWindow
        # For now, export without filter metadata since we don't have access

        # Progress callback
        def update_progress(percentage, message):
            if percentage >= 0:
                progress_bar['value'] = percentage
                progress_label.config(text=message)
                progress_window.update()

        # Use export engine
        result = export_engine.export_data(
            data=servers,
            data_type='servers',
            export_format=format_type,
            output_path=filename,
            include_metadata=True,
            filters_applied=filters_applied,
            progress_callback=update_progress
        )

        progress_window.destroy()

        if result['success']:
            file_size_mb = result['file_size'] / (1024 * 1024)
            messagebox.showinfo(
                "Export Complete",
                f"Successfully exported {result['records_exported']} servers\n"
                f"Format: {result['format'].upper()}\n"
                f"Size: {file_size_mb:.2f} MB\n"
                f"File: {filename}"
            )
        else:
            messagebox.showerror("Export Error", "Export failed")

    except Exception as e:
        try:
            progress_window.destroy()
        except:
            pass
        messagebox.showerror(
            "Export Error",
            f"Failed to export servers:\n{str(e)}"
        )
