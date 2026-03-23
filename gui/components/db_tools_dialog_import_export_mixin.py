"""
SMBSeek GUI - DBToolsDialog Import/Export Mixin

Private mixin extracted from DBToolsDialog containing the Import & Merge tab
and Export & Backup tab construction plus all associated handlers.
"""

import os
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from gui.utils.db_tools_engine import MergeConflictStrategy


class _DBToolsDialogImportExportMixin:
    """Private mixin for DBToolsDialog import/export tab construction and handlers."""

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

    def _normalize_import_source_path(self, path: str) -> str:
        """Return normalized absolute path for import-source identity checks."""
        return os.path.abspath(os.path.realpath(path))

    def _is_last_completed_import_source(self, path: str) -> bool:
        """Return True when path matches the last successfully imported source."""
        if not path or not self.last_completed_import_source:
            return False
        return self._normalize_import_source_path(path) == self.last_completed_import_source

    def _lock_import_source_until_changed(self, source_path: str) -> None:
        """Disable merge button until user selects a different source file."""
        if source_path:
            self.last_completed_import_source = self._normalize_import_source_path(source_path)

        if self.merge_button:
            self.merge_button.config(state=tk.DISABLED, text="Start Import")

        if self.import_status_label:
            source_name = os.path.basename(source_path) if source_path else "selected source"
            self.import_status_label.config(
                text=(
                    f"Import complete for {source_name}. "
                    "Select a different source file to import again."
                )
            )

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
        if self._is_last_completed_import_source(path):
            self.import_status_label.config(
                text="Import already completed for this source. Select a different source file."
            )
            self.merge_button.config(state=tk.DISABLED, text="Start Import")
            return
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
        if self._is_last_completed_import_source(path):
            self.import_status_label.config(
                text="Import already completed for this source. Select a different source file."
            )
            self.merge_button.config(state=tk.DISABLED, text="Start Import")
            return
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

    def _quick_backup(self) -> None:
        """Create quick timestamped backup."""
        self._show_progress("Creating backup...")

        self.operation_thread = threading.Thread(
            target=self._backup_worker,
            daemon=True
        )
        self.operation_thread.start()
