"""
SMBSeek GUI - Database Tools Dialog Operations Mixin

Background worker methods and progress queue handling extracted from
DBToolsDialog to reduce module size. No behavior changes.

Host class must supply these instance attributes:
    operation_queue, engine, progress_label, progress_bar,
    close_button, notebook, on_database_changed, dialog
"""

import os
import queue
import tkinter as tk
from tkinter import messagebox

from gui.utils.db_tools_engine import MergeConflictStrategy
from gui.utils.logging_config import get_logger

_logger = get_logger("db_tools_dialog")


class _DBToolsDialogOperationsMixin:

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
                    'refresh_needed': True,
                    'import_completed': True,
                    'import_path': external_path,
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
                    'refresh_needed': True,
                    'import_completed': True,
                    'import_path': csv_path,
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
                        if update.get('import_completed'):
                            self._lock_import_source_until_changed(update.get('import_path', ''))
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
