"""
SMBSeek GUI - Database Tools Dialog

Modal dialog providing database management capabilities including import/merge,
export/backup, statistics, and maintenance operations.

Design Decision: Follows the dialog pattern from database_setup_dialog.py with
tabbed interface for organizing multiple related functions. Threading is used
for long-running operations to keep the UI responsive.
"""

import os
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Optional

from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.db_tools_engine import (
    DBToolsEngine,
    MergeConflictStrategy,
    MergeResult,
    DatabaseStats,
    PurgePreview,
)
from gui.utils.logging_config import get_logger

_logger = get_logger("db_tools_dialog")


class DBToolsDialog:
    """
    Database tools dialog with tabbed interface.

    Provides:
    - Import & Merge: Import external databases with conflict resolution
    - Export & Backup: Export database copies and quick backups
    - Statistics: View database metrics and country breakdown
    - Maintenance: Vacuum, integrity check, purge old data
    """

    def __init__(
        self,
        parent: tk.Widget,
        db_path: str,
        on_database_changed: Optional[Callable[[], None]] = None
    ):
        """
        Initialize database tools dialog.

        Args:
            parent: Parent window
            db_path: Path to the current database
            on_database_changed: Callback to refresh UI after database changes
        """
        self.parent = parent
        self.db_path = db_path
        self.on_database_changed = on_database_changed
        self.theme = get_theme()
        self.engine = DBToolsEngine(db_path)

        # Dialog result and state
        self.dialog = None
        self.notebook = None

        # Background operation tracking
        self.operation_thread = None
        self.operation_queue = queue.Queue()
        self.cancel_requested = False

        # UI component references
        self.progress_frame = None
        self.progress_bar = None
        self.progress_label = None
        self.close_button = None

        # Import tab components
        self.import_path_var = None
        self.import_status_label = None
        self.import_preview_frame = None
        self.import_source_type = "db"
        self.merge_strategy_var = None
        self.auto_backup_var = None
        self.merge_button = None

        # Stats tab components
        self.stats_labels = {}

        # Maintenance tab components
        self.purge_days_var = None
        self.purge_preview_label = None

        # Create dialog
        self._create_dialog()

    def _create_dialog(self) -> None:
        """Create and configure the dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Database Tools")
        self.dialog.geometry("700x790")
        self.dialog.resizable(True, True)
        self.dialog.minsize(650, 700)

        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")

        # Make modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Handle window close
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)

        # Create layout
        self._create_header()
        self._create_notebook()
        self._create_progress_frame()
        self._create_button_frame()
        self.theme.apply_theme_to_application(self.dialog)

        # Center dialog
        self._center_dialog()

        # Ensure dialog appears on top (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

        # Start processing background updates
        self._process_operation_queue()

        # Load initial stats
        self._refresh_stats()

    def _create_header(self) -> None:
        """Create dialog header."""
        header_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))

        title_label = self.theme.create_styled_label(
            header_frame,
            "Database Tools",
            "title"
        )
        title_label.pack(anchor=tk.W)

        db_name = os.path.basename(self.db_path)
        desc_label = self.theme.create_styled_label(
            header_frame,
            f"Manage database: {db_name}",
            "body"
        )
        desc_label.pack(anchor=tk.W, pady=(5, 0))

    def _create_notebook(self) -> None:
        """Create tabbed notebook interface."""
        self.notebook = ttk.Notebook(self.dialog)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        # Create tabs
        self._create_import_tab()
        self._create_export_tab()
        self._create_stats_tab()
        self._create_maintenance_tab()

    # -------------------------------------------------------------------------
    # Import & Merge Tab
    # -------------------------------------------------------------------------

    def _create_import_tab(self) -> None:
        """Create the Import & Merge tab."""
        tab = tk.Frame(self.notebook)
        self.theme.apply_to_widget(tab, "main_window")
        self.notebook.add(tab, text="Import & Merge")

        # File selection section
        file_frame = tk.LabelFrame(tab, text="External Data Source")
        self._style_labelframe(file_frame)
        file_frame.pack(fill=tk.X, padx=10, pady=10)

        path_frame = tk.Frame(file_frame)
        self.theme.apply_to_widget(path_frame, "main_window")
        path_frame.pack(fill=tk.X, padx=10, pady=10)

        self.import_path_var = tk.StringVar()
        path_entry = tk.Entry(path_frame, textvariable=self.import_path_var, width=50)
        self.theme.apply_to_widget(path_entry, "entry")
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        browse_btn = tk.Button(
            path_frame,
            text="Browse...",
            command=self._browse_import_file
        )
        self.theme.apply_to_widget(browse_btn, "button_secondary")
        browse_btn.pack(side=tk.RIGHT)

        # Status label
        self.import_status_label = self.theme.create_styled_label(
            file_frame, "", "body"
        )
        self.import_status_label.pack(anchor=tk.W, padx=10, pady=(0, 10))

        # Preview section
        self.import_preview_frame = tk.LabelFrame(tab, text="Import Preview")
        self._style_labelframe(self.import_preview_frame)
        self.import_preview_frame.pack(fill=tk.X, padx=10, pady=10)

        preview_info = self.theme.create_styled_label(
            self.import_preview_frame,
            "Select a database or CSV file to preview",
            "body"
        )
        preview_info.pack(padx=10, pady=10)

        # Strategy selection
        strategy_frame = tk.LabelFrame(tab, text="Conflict Resolution Strategy")
        self._style_labelframe(strategy_frame)
        strategy_frame.pack(fill=tk.X, padx=10, pady=10)

        self.merge_strategy_var = tk.StringVar(value=MergeConflictStrategy.KEEP_NEWER.value)

        strategies = [
            (MergeConflictStrategy.KEEP_NEWER.value, "Keep newer (by last_seen timestamp)", True),
            (MergeConflictStrategy.KEEP_SOURCE.value, "Prefer source database", False),
            (MergeConflictStrategy.KEEP_CURRENT.value, "Prefer current database", False),
        ]

        for value, text, recommended in strategies:
            label = text + (" (Recommended)" if recommended else "")
            rb = tk.Radiobutton(
                strategy_frame,
                text=label,
                variable=self.merge_strategy_var,
                value=value
            )
            self.theme.apply_to_widget(rb, "checkbox")
            rb.pack(anchor=tk.W, padx=10, pady=2)

        # Auto-backup checkbox
        self.auto_backup_var = tk.BooleanVar(value=True)
        backup_cb = tk.Checkbutton(
            strategy_frame,
            text="Auto-backup before merge (recommended)",
            variable=self.auto_backup_var
        )
        self.theme.apply_to_widget(backup_cb, "checkbox")
        backup_cb.pack(anchor=tk.W, padx=10, pady=(10, 10))

        # Merge button
        btn_frame = tk.Frame(tab)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        self.merge_button = tk.Button(
            btn_frame,
            text="Start Import",
            command=self._start_merge,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.merge_button, "button_primary")
        self.merge_button.pack(side=tk.RIGHT)

    def _browse_import_file(self) -> None:
        """Open file browser for import file selection."""
        filetypes = [
            ("SQLite databases", "*.db *.sqlite *.sqlite3"),
            ("CSV files", "*.csv"),
            ("Supported import files", "*.db *.sqlite *.sqlite3 *.csv"),
            ("All files", "*.*")
        ]

        filename = filedialog.askopenfilename(
            title="Select SMBSeek Database or CSV to Import",
            filetypes=filetypes,
            initialdir=os.path.dirname(self.db_path) or "."
        )

        if filename:
            self.import_path_var.set(filename)
            self._validate_import_file(filename)

    def _set_import_preview_text(self, preview_text: str) -> None:
        """Replace preview panel body text with provided content."""
        for widget in self.import_preview_frame.winfo_children():
            widget.destroy()
        preview_label = self.theme.create_styled_label(
            self.import_preview_frame, preview_text, "body"
        )
        preview_label.pack(padx=10, pady=10, anchor=tk.W)

    def _validate_import_file(self, path: str) -> None:
        """Validate the selected import file."""
        self.import_status_label.config(text="Validating...")
        self.import_source_type = "db"
        self.merge_button.config(state=tk.DISABLED, text="Start Import")

        suffix = Path(path).suffix.lower()
        if suffix == ".csv":
            self._validate_csv_import_file(path)
            return

        self._validate_db_import_file(path)

    def _validate_db_import_file(self, path: str) -> None:
        """Validate and preview database merge source."""
        validation = self.engine.validate_external_schema(path)

        if not validation.valid:
            self.import_status_label.config(
                text=f"Invalid: {'; '.join(validation.errors)}"
            )
            self.merge_button.config(state=tk.DISABLED)
            return

        preview = self.engine.preview_merge(path)
        if not preview.get('valid'):
            self.import_status_label.config(
                text=f"Preview failed: {'; '.join(preview.get('errors', []))}"
            )
            self.merge_button.config(state=tk.DISABLED)
            return

        self.import_source_type = "db"
        self.import_status_label.config(text="Database schema validated successfully")
        preview_text = (
            f"External servers: {preview['external_servers']}\n"
            f"New servers: {preview['new_servers']}\n"
            f"Existing servers: {preview['existing_servers']} (will be merged per strategy)\n"
            f"Total shares: {preview['total_shares']}\n"
            f"Total vulnerabilities: {preview['total_vulnerabilities']}\n"
            f"Total file manifests: {preview['total_file_manifests']}"
        )
        warnings = preview.get('warnings') or []
        if warnings:
            preview_text += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)

        self._set_import_preview_text(preview_text)
        self.merge_button.config(state=tk.NORMAL, text="Start Merge")

    def _validate_csv_import_file(self, path: str) -> None:
        """Validate and preview CSV host import source."""
        preview = self.engine.preview_csv_import(path)
        if not preview.get('valid'):
            self.import_status_label.config(
                text=f"Invalid CSV: {'; '.join(preview.get('errors', []))}"
            )
            errors = preview.get('errors') or ["CSV validation failed"]
            self._set_import_preview_text("CSV preview failed:\n" + "\n".join(f"- {e}" for e in errors))
            self.merge_button.config(state=tk.DISABLED, text="Start Import")
            return

        self.import_source_type = "csv"
        self.import_status_label.config(text="CSV validated successfully")

        protocol_counts = preview.get('protocol_counts') or {}
        preview_text = (
            f"CSV rows: {preview['total_rows']}\n"
            f"Valid rows: {preview['valid_rows']}\n"
            f"Skipped rows: {preview['skipped_rows']}\n"
            f"New servers: {preview['new_servers']}\n"
            f"Existing servers: {preview['existing_servers']}\n"
            f"Protocol split: "
            f"S={protocol_counts.get('S', 0)}, "
            f"F={protocol_counts.get('F', 0)}, "
            f"H={protocol_counts.get('H', 0)}"
        )

        warnings = preview.get('warnings') or []
        if warnings:
            preview_text += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in warnings)

        self._set_import_preview_text(preview_text)
        self.merge_button.config(state=tk.NORMAL, text="Start CSV Import")

    def _start_merge(self) -> None:
        """Start the merge operation."""
        external_path = self.import_path_var.get()
        if not external_path or not os.path.exists(external_path):
            messagebox.showerror("Error", "Please select a valid import file")
            return

        strategy_value = self.merge_strategy_var.get()
        strategy = MergeConflictStrategy(strategy_value)
        auto_backup = self.auto_backup_var.get()

        if self.import_source_type == "csv":
            if not messagebox.askyesno(
                "Confirm CSV Import",
                f"Import CSV hosts from:\n{external_path}\n\n"
                f"Strategy: {strategy.value}\n"
                f"Auto-backup: {'Yes' if auto_backup else 'No'}\n\n"
                "Continue?"
            ):
                return

            self._show_progress("Starting CSV import...")
            self.operation_thread = threading.Thread(
                target=self._csv_import_worker,
                args=(external_path, strategy, auto_backup),
                daemon=True
            )
            self.operation_thread.start()
            return

        # Default: database merge path
        if not messagebox.askyesno(
            "Confirm Merge",
            f"Merge database from:\n{external_path}\n\n"
            f"Strategy: {strategy.value}\n"
            f"Auto-backup: {'Yes' if auto_backup else 'No'}\n\n"
            "Continue?"
        ):
            return

        self._show_progress("Starting merge...")
        self.operation_thread = threading.Thread(
            target=self._merge_worker,
            args=(external_path, strategy, auto_backup),
            daemon=True
        )
        self.operation_thread.start()

    def _merge_worker(
        self,
        external_path: str,
        strategy: MergeConflictStrategy,
        auto_backup: bool
    ) -> None:
        """Background worker for merge operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.merge_database(
                external_path,
                strategy=strategy,
                auto_backup=auto_backup,
                progress_callback=progress_callback
            )

            if result.success:
                summary = (
                    f"Merge completed in {result.duration_seconds:.1f}s\n\n"
                    f"Servers added: {result.servers_added}\n"
                    f"Servers updated: {result.servers_updated}\n"
                    f"Servers skipped: {result.servers_skipped}\n"
                    f"Shares imported: {result.shares_imported}\n"
                    f"Vulnerabilities imported: {result.vulnerabilities_imported}\n"
                    f"File manifests imported: {result.file_manifests_imported}"
                )
                if result.backup_path:
                    summary += f"\n\nBackup created: {os.path.basename(result.backup_path)}"
                if result.warnings:
                    summary += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in result.warnings)

                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': summary,
                    'refresh_needed': True
                })
            else:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': '\n'.join(result.errors)
                })

        except Exception as e:
            _logger.exception("Merge operation failed")
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    def _csv_import_worker(
        self,
        csv_path: str,
        strategy: MergeConflictStrategy,
        auto_backup: bool
    ) -> None:
        """Background worker for CSV host import operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.import_csv_hosts(
                csv_path,
                strategy=strategy,
                auto_backup=auto_backup,
                progress_callback=progress_callback
            )

            if result.success:
                summary = (
                    f"CSV import completed in {result.duration_seconds:.1f}s\n\n"
                    f"Rows total: {result.rows_total}\n"
                    f"Rows valid: {result.rows_valid}\n"
                    f"Rows skipped: {result.rows_skipped}\n"
                    f"Servers added: {result.servers_added}\n"
                    f"Servers updated: {result.servers_updated}\n"
                    f"Servers skipped (strategy): {result.servers_skipped}\n"
                    f"Protocol rows: "
                    f"S={result.protocol_counts.get('S', 0)}, "
                    f"F={result.protocol_counts.get('F', 0)}, "
                    f"H={result.protocol_counts.get('H', 0)}"
                )
                if result.backup_path:
                    summary += f"\n\nBackup created: {os.path.basename(result.backup_path)}"
                if result.warnings:
                    summary += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in result.warnings)

                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': summary,
                    'refresh_needed': True
                })
            else:
                error_text = '\\n'.join(result.errors) if result.errors else 'CSV import failed'
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': error_text
                })

        except Exception as e:
            _logger.exception("CSV import operation failed")
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    # -------------------------------------------------------------------------
    # Export & Backup Tab
    # -------------------------------------------------------------------------

    def _create_export_tab(self) -> None:
        """Create the Export & Backup tab."""
        tab = tk.Frame(self.notebook)
        self.theme.apply_to_widget(tab, "main_window")
        self.notebook.add(tab, text="Export & Backup")

        # Export section
        export_frame = tk.LabelFrame(tab, text="Export Database")
        self._style_labelframe(export_frame)
        export_frame.pack(fill=tk.X, padx=10, pady=10)

        export_desc = self.theme.create_styled_label(
            export_frame,
            "Create an optimized copy of the database at a chosen location.\n"
            "Uses VACUUM INTO for a clean, defragmented copy.",
            "body"
        )
        export_desc.pack(anchor=tk.W, padx=10, pady=(10, 5))

        export_btn = tk.Button(
            export_frame,
            text="Export As...",
            command=self._export_as
        )
        self.theme.apply_to_widget(export_btn, "button_primary")
        export_btn.pack(anchor=tk.W, padx=10, pady=(5, 10))

        # Quick backup section
        backup_frame = tk.LabelFrame(tab, text="Quick Backup")
        self._style_labelframe(backup_frame)
        backup_frame.pack(fill=tk.X, padx=10, pady=10)

        backup_desc = self.theme.create_styled_label(
            backup_frame,
            "Create a timestamped backup in the same directory as the database.\n"
            "Format: smbseek_backup_YYYYMMDD_HHMMSS.db",
            "body"
        )
        backup_desc.pack(anchor=tk.W, padx=10, pady=(10, 5))

        backup_btn = tk.Button(
            backup_frame,
            text="Quick Backup",
            command=self._quick_backup
        )
        self.theme.apply_to_widget(backup_btn, "button_primary")
        backup_btn.pack(anchor=tk.W, padx=10, pady=(5, 10))

    def _export_as(self) -> None:
        """Export database to chosen location."""
        filetypes = [
            ("SQLite databases", "*.db"),
            ("All files", "*.*")
        ]

        initial_name = f"{os.path.splitext(os.path.basename(self.db_path))[0]}_export.db"

        filename = filedialog.asksaveasfilename(
            title="Export Database As",
            filetypes=filetypes,
            initialfile=initial_name,
            defaultextension=".db"
        )

        if filename:
            self._show_progress("Exporting database...")

            self.operation_thread = threading.Thread(
                target=self._export_worker,
                args=(filename,),
                daemon=True
            )
            self.operation_thread.start()

    def _export_worker(self, output_path: str) -> None:
        """Background worker for export operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.export_database(output_path, progress_callback)

            if result['success']:
                size_mb = result['size_bytes'] / (1024 * 1024)
                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': f"Database exported successfully.\n\n"
                               f"Path: {result['output_path']}\n"
                               f"Size: {size_mb:.2f} MB"
                })
            else:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': result.get('error', 'Export failed')
                })

        except Exception as e:
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    def _quick_backup(self) -> None:
        """Create quick timestamped backup."""
        self._show_progress("Creating backup...")

        self.operation_thread = threading.Thread(
            target=self._backup_worker,
            daemon=True
        )
        self.operation_thread.start()

    def _backup_worker(self) -> None:
        """Background worker for backup operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.quick_backup(progress_callback=progress_callback)

            if result['success']:
                size_mb = result['size_bytes'] / (1024 * 1024)
                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': f"Backup created successfully.\n\n"
                               f"Path: {result['backup_path']}\n"
                               f"Size: {size_mb:.2f} MB"
                })
            else:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': result.get('error', 'Backup failed')
                })

        except Exception as e:
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    # -------------------------------------------------------------------------
    # Statistics Tab
    # -------------------------------------------------------------------------

    def _create_stats_tab(self) -> None:
        """Create the Statistics tab."""
        tab = tk.Frame(self.notebook)
        self.theme.apply_to_widget(tab, "main_window")
        self.notebook.add(tab, text="Statistics")

        # Header with refresh button
        header_frame = tk.Frame(tab)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=10, pady=10)

        title = self.theme.create_styled_label(
            header_frame, "Database Statistics", "heading"
        )
        title.pack(side=tk.LEFT)

        refresh_btn = tk.Button(
            header_frame,
            text="Refresh",
            command=self._refresh_stats
        )
        self.theme.apply_to_widget(refresh_btn, "button_secondary")
        refresh_btn.pack(side=tk.RIGHT)

        # Stats grid
        stats_frame = tk.Frame(tab)
        self.theme.apply_to_widget(stats_frame, "main_window")
        stats_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Create stat cards in 2x2 grid
        stats_to_show = [
            ("servers", "Servers"),
            ("shares", "Shares"),
            ("vulnerabilities", "Vulnerabilities"),
            ("file_manifests", "File Manifests"),
        ]

        for i, (key, label) in enumerate(stats_to_show):
            row, col = divmod(i, 2)
            card = tk.Frame(stats_frame)
            self.theme.apply_to_widget(card, "metric_card")
            card.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")

            label_widget = self.theme.create_styled_label(card, label, "body")
            label_widget.pack(anchor=tk.W, padx=10, pady=(10, 0))

            value_widget = self.theme.create_styled_label(card, "0", "heading")
            value_widget.pack(anchor=tk.W, padx=10, pady=(5, 10))

            self.stats_labels[key] = value_widget

        stats_frame.grid_columnconfigure(0, weight=1)
        stats_frame.grid_columnconfigure(1, weight=1)

        # Additional info section
        info_frame = tk.LabelFrame(tab, text="Details")
        self._style_labelframe(info_frame)
        info_frame.pack(fill=tk.X, padx=10, pady=10)

        self.stats_labels['details'] = self.theme.create_styled_label(
            info_frame, "Loading...", "body"
        )
        self.stats_labels['details'].pack(anchor=tk.W, padx=10, pady=10)

        # Country distribution
        country_frame = tk.LabelFrame(tab, text="Country Distribution")
        self._style_labelframe(country_frame)
        country_frame.pack(fill=tk.X, padx=10, pady=10)

        self.stats_labels['countries'] = self.theme.create_styled_label(
            country_frame, "Loading...", "body"
        )
        self.stats_labels['countries'].pack(anchor=tk.W, padx=10, pady=10)

    def _refresh_stats(self) -> None:
        """Refresh database statistics."""
        try:
            stats = self.engine.get_database_stats()

            self.stats_labels['servers'].config(
                text=f"{stats.total_servers:,} ({stats.active_servers:,} active)"
            )
            self.stats_labels['shares'].config(
                text=f"{stats.total_shares:,} ({stats.accessible_shares:,} accessible)"
            )
            self.stats_labels['vulnerabilities'].config(
                text=f"{stats.total_vulnerabilities:,}"
            )
            self.stats_labels['file_manifests'].config(
                text=f"{stats.total_file_manifests:,}"
            )

            size_mb = stats.database_size_bytes / (1024 * 1024)
            details_text = (
                f"Database size: {size_mb:.2f} MB\n"
                f"Scan sessions: {stats.total_sessions:,}\n"
                f"Credentials: {stats.total_credentials:,}\n"
                f"Oldest record: {stats.oldest_record or 'N/A'}\n"
                f"Newest record: {stats.newest_record or 'N/A'}"
            )
            self.stats_labels['details'].config(text=details_text)

            # Format country distribution (top 10)
            if stats.countries:
                top_countries = list(stats.countries.items())[:10]
                country_text = " | ".join(
                    f"{country}: {count:,}" for country, count in top_countries
                )
                if len(stats.countries) > 10:
                    country_text += f" | ... ({len(stats.countries) - 10} more)"
            else:
                country_text = "No country data available"

            self.stats_labels['countries'].config(text=country_text)

        except Exception as e:
            _logger.warning("Failed to refresh stats: %s", e)
            self.stats_labels['details'].config(text=f"Error loading stats: {e}")

    # -------------------------------------------------------------------------
    # Maintenance Tab
    # -------------------------------------------------------------------------

    def _create_maintenance_tab(self) -> None:
        """Create the Maintenance tab."""
        tab = tk.Frame(self.notebook)
        self.theme.apply_to_widget(tab, "main_window")
        self.notebook.add(tab, text="Maintenance")

        # Vacuum section
        vacuum_frame = tk.LabelFrame(tab, text="Optimize Database")
        self._style_labelframe(vacuum_frame)
        vacuum_frame.pack(fill=tk.X, padx=10, pady=10)

        vacuum_desc = self.theme.create_styled_label(
            vacuum_frame,
            "Reclaim disk space and optimize database indexes.\n"
            "Recommended after large deletions or imports.",
            "body"
        )
        vacuum_desc.pack(anchor=tk.W, padx=10, pady=(10, 5))

        btn_frame = tk.Frame(vacuum_frame)
        self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        vacuum_btn = tk.Button(
            btn_frame,
            text="Vacuum/Optimize",
            command=self._run_vacuum
        )
        self.theme.apply_to_widget(vacuum_btn, "button_primary")
        vacuum_btn.pack(side=tk.LEFT, padx=(0, 10))

        integrity_btn = tk.Button(
            btn_frame,
            text="Integrity Check",
            command=self._run_integrity_check
        )
        self.theme.apply_to_widget(integrity_btn, "button_secondary")
        integrity_btn.pack(side=tk.LEFT)

        # Purge section
        purge_frame = tk.LabelFrame(tab, text="Purge Old Data")
        self._style_labelframe(purge_frame)
        purge_frame.pack(fill=tk.X, padx=10, pady=10)

        purge_desc = self.theme.create_styled_label(
            purge_frame,
            "Delete servers that haven't been seen recently.\n"
            "WARNING: This will also delete all related shares, files, and vulnerabilities.",
            "body"
        )
        purge_desc.pack(anchor=tk.W, padx=10, pady=(10, 5))

        days_frame = tk.Frame(purge_frame)
        self.theme.apply_to_widget(days_frame, "main_window")
        days_frame.pack(fill=tk.X, padx=10, pady=5)

        days_label = self.theme.create_styled_label(
            days_frame, "Delete servers not seen in:", "body"
        )
        days_label.pack(side=tk.LEFT)

        self.purge_days_var = tk.StringVar(value="30")
        days_entry = tk.Entry(days_frame, textvariable=self.purge_days_var, width=5)
        self.theme.apply_to_widget(days_entry, "entry")
        days_entry.pack(side=tk.LEFT, padx=5)

        days_suffix = self.theme.create_styled_label(days_frame, "days", "body")
        days_suffix.pack(side=tk.LEFT)

        # Preview label
        self.purge_preview_label = self.theme.create_styled_label(
            purge_frame, "", "body"
        )
        self.purge_preview_label.pack(anchor=tk.W, padx=10, pady=5)

        purge_btn_frame = tk.Frame(purge_frame)
        self.theme.apply_to_widget(purge_btn_frame, "main_window")
        purge_btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        preview_btn = tk.Button(
            purge_btn_frame,
            text="Preview",
            command=self._preview_purge
        )
        self.theme.apply_to_widget(preview_btn, "button_secondary")
        preview_btn.pack(side=tk.LEFT, padx=(0, 10))

        purge_btn = tk.Button(
            purge_btn_frame,
            text="Purge",
            command=self._execute_purge
        )
        self.theme.apply_to_widget(purge_btn, "button_primary")
        purge_btn.pack(side=tk.LEFT)

    def _run_vacuum(self) -> None:
        """Run database vacuum/optimize."""
        if not messagebox.askyesno(
            "Confirm Vacuum",
            "This will optimize the database and reclaim disk space.\n\n"
            "The operation may take a moment for large databases.\n\n"
            "Continue?"
        ):
            return

        self._show_progress("Optimizing database...")

        self.operation_thread = threading.Thread(
            target=self._vacuum_worker,
            daemon=True
        )
        self.operation_thread.start()

    def _vacuum_worker(self) -> None:
        """Background worker for vacuum operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.vacuum_database(progress_callback)

            if result['success']:
                saved_kb = result['space_saved'] / 1024
                before_mb = result['size_before'] / (1024 * 1024)
                after_mb = result['size_after'] / (1024 * 1024)

                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': f"Database optimized successfully.\n\n"
                               f"Before: {before_mb:.2f} MB\n"
                               f"After: {after_mb:.2f} MB\n"
                               f"Space saved: {saved_kb:.1f} KB",
                    'refresh_needed': True
                })
            else:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': result.get('error', 'Vacuum failed')
                })

        except Exception as e:
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    def _run_integrity_check(self) -> None:
        """Run database integrity check."""
        result = self.engine.integrity_check()

        if result['success']:
            if result['integrity_ok']:
                messagebox.showinfo(
                    "Integrity Check",
                    "Database integrity check passed.\n\n"
                    "No issues found."
                )
            else:
                messagebox.showwarning(
                    "Integrity Check",
                    f"Database integrity issues detected:\n\n{result['message']}"
                )
        else:
            messagebox.showerror(
                "Integrity Check Failed",
                f"Could not run integrity check:\n\n{result.get('error', 'Unknown error')}"
            )

    def _preview_purge(self) -> None:
        """Preview purge operation."""
        try:
            days = int(self.purge_days_var.get())
            if days < 1:
                raise ValueError("Days must be at least 1")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Invalid number of days: {e}")
            return

        preview = self.engine.preview_purge(days)

        if preview.servers_to_delete == 0:
            self.purge_preview_label.config(
                text="No servers match the purge criteria."
            )
        else:
            preview_text = (
                f"Servers to delete: {preview.servers_to_delete}\n"
                f"Cascading deletions:\n"
                f"  - Share access: {preview.shares_to_delete}\n"
                f"  - Credentials: {preview.credentials_to_delete}\n"
                f"  - File manifests: {preview.file_manifests_to_delete}\n"
                f"  - Vulnerabilities: {preview.vulnerabilities_to_delete}\n"
                f"  - User flags: {preview.user_flags_to_delete}\n"
                f"  - Probe cache: {preview.probe_cache_to_delete}\n"
                f"TOTAL: {preview.total_records} records"
            )
            self.purge_preview_label.config(text=preview_text)

    def _execute_purge(self) -> None:
        """Execute purge operation."""
        try:
            days = int(self.purge_days_var.get())
            if days < 1:
                raise ValueError("Days must be at least 1")
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Invalid number of days: {e}")
            return

        preview = self.engine.preview_purge(days)

        if preview.servers_to_delete == 0:
            messagebox.showinfo("No Data to Purge", "No servers match the purge criteria.")
            return

        if not messagebox.askyesno(
            "Confirm Purge",
            f"WARNING: This will permanently delete:\n\n"
            f"Servers: {preview.servers_to_delete}\n"
            f"Total records (including cascades): {preview.total_records}\n\n"
            f"This action cannot be undone!\n\n"
            "Continue?"
        ):
            return

        self._show_progress("Purging old data...")

        self.operation_thread = threading.Thread(
            target=self._purge_worker,
            args=(days,),
            daemon=True
        )
        self.operation_thread.start()

    def _purge_worker(self, days: int) -> None:
        """Background worker for purge operation."""
        try:
            def progress_callback(pct: int, msg: str):
                self.operation_queue.put({
                    'type': 'progress',
                    'percent': pct,
                    'message': msg
                })

            result = self.engine.execute_purge(days, progress_callback)

            if result['success']:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'message': f"Purge completed successfully.\n\n"
                               f"Servers deleted: {result['servers_deleted']}\n"
                               f"Total records deleted: {result['total_records_deleted']}",
                    'refresh_needed': True
                })
            else:
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': result.get('error', 'Purge failed')
                })

        except Exception as e:
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })

    # -------------------------------------------------------------------------
    # Progress and Queue Management
    # -------------------------------------------------------------------------

    def _create_progress_frame(self) -> None:
        """Create progress display frame."""
        self.progress_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(self.progress_frame, "main_window")
        self.progress_frame.pack(fill=tk.X, padx=20, pady=10)

        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            mode='indeterminate',
            style="SMBSeek.Horizontal.TProgressbar"
        )

        self.progress_label = self.theme.create_styled_label(
            self.progress_frame, "", "body"
        )

        # Initially hidden
        self._hide_progress()

    def _show_progress(self, message: str) -> None:
        """Show progress bar and message."""
        self.progress_label.config(text=message)
        self.progress_label.pack(pady=(0, 5))
        self.progress_bar.pack(fill=tk.X)
        self.progress_bar.start(10)

        # Disable close button during operation
        if self.close_button:
            self.close_button.config(state=tk.DISABLED)

        # Hide notebook
        self.notebook.pack_forget()

    def _hide_progress(self) -> None:
        """Hide progress bar and message."""
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.progress_label.pack_forget()

        # Re-enable close button
        if self.close_button:
            self.close_button.config(state=tk.NORMAL)

        # Show notebook
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

    def _process_operation_queue(self) -> None:
        """Process background operation updates."""
        try:
            while True:
                update = self.operation_queue.get_nowait()

                if update['type'] == 'progress':
                    self.progress_label.config(text=update['message'])

                elif update['type'] == 'complete':
                    self._hide_progress()

                    if update['success']:
                        messagebox.showinfo("Success", update.get('message', 'Operation completed'))
                        if update.get('refresh_needed') and self.on_database_changed:
                            self.on_database_changed()
                        self._refresh_stats()
                    else:
                        messagebox.showerror(
                            "Operation Failed",
                            f"Operation failed:\n\n{update.get('error', 'Unknown error')}"
                        )

        except queue.Empty:
            pass

        # Schedule next check
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.after(100, self._process_operation_queue)

    # -------------------------------------------------------------------------
    # Dialog Management
    # -------------------------------------------------------------------------

    def _create_button_frame(self) -> None:
        """Create button frame."""
        button_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(button_frame, "main_window")
        button_frame.pack(fill=tk.X, padx=20, pady=(0, 20))

        self.close_button = tk.Button(
            button_frame,
            text="Close",
            command=self._on_close
        )
        self.theme.apply_to_widget(self.close_button, "button_secondary")
        self.close_button.pack(side=tk.RIGHT)

    def _style_labelframe(self, frame: tk.LabelFrame) -> None:
        """Apply consistent themed styling to LabelFrame title and body."""
        self.theme.apply_to_widget(frame, "main_window")
        try:
            frame.configure(
                bg=self.theme.colors["primary_bg"],
                fg=self.theme.colors["text"],
                highlightbackground=self.theme.colors["border"],
                highlightcolor=self.theme.colors["border"],
            )
        except tk.TclError:
            pass

    def _center_dialog(self) -> None:
        """Center dialog on parent window."""
        self.dialog.update_idletasks()

        x = self.parent.winfo_x() + (self.parent.winfo_width() // 2) - (self.dialog.winfo_width() // 2)
        y = self.parent.winfo_y() + (self.parent.winfo_height() // 2) - (self.dialog.winfo_height() // 2)

        self.dialog.geometry(f"+{x}+{y}")

    def _on_close(self) -> None:
        """Handle dialog close."""
        if self.operation_thread and self.operation_thread.is_alive():
            if not messagebox.askyesno(
                "Operation in Progress",
                "An operation is still running.\n\n"
                "Are you sure you want to close?"
            ):
                return

        self.dialog.destroy()

    def show_modal(self) -> None:
        """Show dialog modally."""
        if self.dialog:
            self.dialog.wait_window()


def show_db_tools_dialog(
    parent: tk.Widget,
    db_path: str,
    on_database_changed: Optional[Callable[[], None]] = None
) -> None:
    """
    Show the database tools dialog.

    Args:
        parent: Parent window
        db_path: Path to the current database
        on_database_changed: Callback to refresh UI after database changes
    """
    dialog = DBToolsDialog(parent, db_path, on_database_changed)
    dialog.show_modal()
