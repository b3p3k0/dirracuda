"""
Server List Export Operations

Handles CSV/JSON/ZIP export functionality with progress dialogs.
All operations use explicit dependency injection to avoid tight coupling.
"""

import tkinter as tk
from tkinter import ttk, filedialog
from gui.utils import safe_messagebox as messagebox
from datetime import datetime
from typing import Dict, List, Any


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