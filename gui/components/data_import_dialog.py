"""
Dirracuda - Data Import Dialog

Interactive dialog for importing CSV/JSON data files with validation and preview.
Supports the team collaboration workflow where colleagues share exported data.

Design Decision: Modal dialog provides focused import experience with proper
validation feedback before committing changes to the database.
"""

import tkinter as tk
from tkinter import ttk, filedialog
from gui.utils import safe_messagebox as messagebox
from typing import Dict, List, Any, Optional, Callable
import os
import sys
from pathlib import Path

from gui.utils.style import get_theme
from gui.utils.data_import_engine import get_import_engine
from gui.utils.dialog_helpers import ensure_dialog_focus


def open_data_import_dialog(parent: tk.Tk, db_reader) -> None:
    """
    Open data import dialog.
    
    Args:
        parent: Parent window
        db_reader: Database reader instance (for getting database path)
    """
    dialog = DataImportDialog(parent, db_reader)


class DataImportDialog:
    """
    Data import dialog for importing CSV/JSON files.
    
    Provides file selection, data type selection, preview, validation,
    and import with progress feedback.
    """
    
    def __init__(self, parent: tk.Tk, db_reader):
        """
        Initialize data import dialog.
        
        Args:
            parent: Parent window
            db_reader: Database reader instance
        """
        self.parent = parent
        self.db_reader = db_reader
        self.theme = get_theme()
        
        # Get database path from the database reader
        self.db_path = getattr(db_reader, 'db_path', '../backend/dirracuda.db')
        self.import_engine = get_import_engine(self.db_path)
        
        # Dialog state
        self.dialog = None
        self.selected_file = None
        self.preview_data = None
        self.validation_result = None
        
        # UI variables
        self.file_path_var = tk.StringVar()
        self.data_type_var = tk.StringVar()
        self.import_mode_var = tk.StringVar()
        
        # UI components
        self.preview_tree = None
        self.validation_text = None
        self.import_button = None
        self.file_info_label = None
        self.mode_desc_label = None
        self.preview_info_label = None
        
        self._create_dialog()
    
    def _create_dialog(self) -> None:
        """Create and configure the import dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Dirracuda - Import Data")
        self.dialog.geometry("800x600")
        self.dialog.minsize(600, 500)
        
        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")
        
        # Configure dialog behavior
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        
        # Create main layout
        self._create_header()
        self._create_file_selection()
        self._create_options_panel()
        self._create_preview_panel()
        self._create_validation_panel()
        self._create_button_panel()
        
        # Setup event handlers
        self._setup_event_handlers()

        # Center dialog
        self._center_dialog()

        # Initialize default values
        self.data_type_var.set('servers')
        self.import_mode_var.set('merge')

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

    def _create_header(self) -> None:
        """Create dialog header with title and description."""
        header_frame = tk.Frame(self.dialog)
        header_frame.pack(fill=tk.X, padx=20, pady=10)
        
        title_label = self.theme.create_styled_label(
            header_frame,
            "📥 Import Data",
            "title"
        )
        title_label.pack(anchor=tk.W)
        
        desc_label = self.theme.create_styled_label(
            header_frame,
            "Import CSV/JSON data files exported from Dirracuda or shared by colleagues.",
            "body"
        )
        desc_label.pack(anchor=tk.W, pady=(5, 0))
    
    def _create_file_selection(self) -> None:
        """Create file selection section."""
        file_frame = tk.LabelFrame(self.dialog, text="File Selection")
        file_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
        
        # File path entry and browse button
        path_frame = tk.Frame(file_frame)
        path_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(path_frame, text="Import File:").pack(side=tk.LEFT)
        
        file_entry = tk.Entry(path_frame, textvariable=self.file_path_var, width=50)
        file_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 5))
        
        browse_button = tk.Button(
            path_frame,
            text="Browse...",
            command=self._browse_file
        )
        self.theme.apply_to_widget(browse_button, "button_secondary")
        browse_button.pack(side=tk.RIGHT)
        
        # File info display
        self.file_info_label = self.theme.create_styled_label(
            file_frame,
            "No file selected",
            "small"
        )
        self.file_info_label.pack(anchor=tk.W, padx=10, pady=(0, 10))
    
    def _create_options_panel(self) -> None:
        """Create import options panel."""
        options_frame = tk.LabelFrame(self.dialog, text="Import Options")
        options_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
        
        inner_frame = tk.Frame(options_frame)
        inner_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Data type selection
        tk.Label(inner_frame, text="Data Type:").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        
        data_type_combo = ttk.Combobox(
            inner_frame,
            textvariable=self.data_type_var,
            values=self.import_engine.get_supported_data_types(),
            state="readonly",
            width=15
        )
        data_type_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 20))
        data_type_combo.bind('<<ComboboxSelected>>', self._on_options_changed)
        
        # Import mode selection
        tk.Label(inner_frame, text="Import Mode:").grid(row=0, column=2, sticky=tk.W, padx=(0, 10))
        
        import_modes = self.import_engine.get_import_modes()
        mode_combo = ttk.Combobox(
            inner_frame,
            textvariable=self.import_mode_var,
            values=list(import_modes.keys()),
            state="readonly",
            width=15
        )
        mode_combo.grid(row=0, column=3, sticky=tk.W)
        mode_combo.bind('<<ComboboxSelected>>', self._on_options_changed)
        
        # Mode description
        self.mode_desc_label = self.theme.create_styled_label(
            options_frame,
            "",
            "small"
        )
        self.mode_desc_label.pack(anchor=tk.W, padx=10, pady=(0, 10))
        self._update_mode_description()
    
    def _create_preview_panel(self) -> None:
        """Create data preview panel."""
        preview_frame = tk.LabelFrame(self.dialog, text="Data Preview")
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        
        # Preview tree
        tree_frame = tk.Frame(preview_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.preview_tree = ttk.Treeview(tree_frame, show='headings', height=8)
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.preview_tree.yview)
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Pack tree and scrollbars
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        # Preview info label
        self.preview_info_label = self.theme.create_styled_label(
            preview_frame,
            "Select a file to preview data",
            "small"
        )
        self.preview_info_label.pack(anchor=tk.W, padx=10, pady=(0, 10))
    
    def _create_validation_panel(self) -> None:
        """Create validation results panel."""
        validation_frame = tk.LabelFrame(self.dialog, text="Validation Results")
        validation_frame.pack(fill=tk.X, padx=20, pady=(0, 10))
        
        # Validation text widget
        text_frame = tk.Frame(validation_frame)
        text_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.validation_text = tk.Text(text_frame, height=4, wrap=tk.WORD, state=tk.DISABLED)
        val_scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.validation_text.yview)
        self.validation_text.configure(yscrollcommand=val_scrollbar.set)
        
        self.validation_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        val_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _create_button_panel(self) -> None:
        """Create dialog button panel."""
        button_frame = tk.Frame(self.dialog)
        button_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Preview button
        preview_button = tk.Button(
            button_frame,
            text="Preview Data",
            command=self._preview_data
        )
        self.theme.apply_to_widget(preview_button, "button_secondary")
        preview_button.pack(side=tk.LEFT)
        
        # Right-aligned buttons
        right_buttons = tk.Frame(button_frame)
        right_buttons.pack(side=tk.RIGHT)
        
        # Cancel button
        cancel_button = tk.Button(
            right_buttons,
            text="Cancel",
            command=self._cancel
        )
        self.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(side=tk.LEFT, padx=(0, 5))
        
        # Import button
        self.import_button = tk.Button(
            right_buttons,
            text="Import Data",
            command=self._import_data,
            state=tk.DISABLED
        )
        self.theme.apply_to_widget(self.import_button, "button_primary")
        self.import_button.pack(side=tk.LEFT)
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers."""
        self.file_path_var.trace('w', self._on_file_path_changed)
        self.dialog.bind('<Escape>', lambda e: self._cancel())
    
    def _center_dialog(self) -> None:
        """Center dialog on parent window."""
        self.dialog.update_idletasks()
        x = self.parent.winfo_x() + (self.parent.winfo_width() // 2) - (self.dialog.winfo_width() // 2)
        y = self.parent.winfo_y() + (self.parent.winfo_height() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")
    
    def _browse_file(self) -> None:
        """Open file browser for selecting import file."""
        filetypes = [
            ("All supported", "*.csv;*.json;*.zip"),
            ("CSV files", "*.csv"),
            ("JSON files", "*.json"), 
            ("ZIP files", "*.zip"),
            ("All files", "*.*")
        ]
        
        filename = filedialog.askopenfilename(
            title="Select Import File",
            filetypes=filetypes
        )
        
        if filename:
            self.file_path_var.set(filename)
    
    def _on_file_path_changed(self, *args) -> None:
        """Handle file path changes."""
        file_path = self.file_path_var.get()
        
        if file_path and os.path.exists(file_path):
            # Validate file format
            format_result = self.import_engine.validate_file_format(file_path)
            
            if format_result['valid']:
                file_size_mb = format_result['file_size'] / (1024 * 1024)
                self.file_info_label.config(
                    text=f"Format: {format_result['format'].upper()}, Size: {file_size_mb:.2f} MB",
                    fg=self.theme.colors['success']
                )
                self.selected_file = file_path
            else:
                self.file_info_label.config(
                    text=f"Error: {format_result['error']}",
                    fg=self.theme.colors['error']
                )
                self.selected_file = None
        else:
            self.file_info_label.config(
                text="No file selected" if not file_path else "File not found",
                fg=self.theme.colors['text_secondary']
            )
            self.selected_file = None
        
        # Reset preview and validation
        self._clear_preview()
        self._clear_validation()
        self.import_button.config(state=tk.DISABLED)
    
    def _on_options_changed(self, *args) -> None:
        """Handle options changes."""
        self._update_mode_description()
        # Clear preview when options change
        if self.preview_data:
            self._clear_preview()
            self._clear_validation()
            self.import_button.config(state=tk.DISABLED)
    
    def _update_mode_description(self) -> None:
        """Update import mode description."""
        modes = self.import_engine.get_import_modes()
        mode = self.import_mode_var.get()
        description = modes.get(mode, "")
        self.mode_desc_label.config(text=description)
    
    def _preview_data(self) -> None:
        """Preview data from selected file."""
        if not self.selected_file:
            messagebox.showwarning("No File", "Please select a file to preview.")
            return
        
        data_type = self.data_type_var.get()
        if not data_type:
            messagebox.showwarning("No Data Type", "Please select a data type.")
            return
        
        try:
            # Show progress
            self.dialog.config(cursor="wait")
            self.dialog.update()
            
            # Get preview data
            preview_result = self.import_engine.preview_import_data(
                self.selected_file, data_type, max_records=20
            )
            
            if preview_result['success']:
                self.preview_data = preview_result
                self._display_preview(preview_result)
                self._display_validation(preview_result['validation_result'])
                
                # Enable import button if validation passed
                if preview_result['validation_result']['valid']:
                    self.import_button.config(state=tk.NORMAL)
                else:
                    self.import_button.config(state=tk.DISABLED)
            else:
                messagebox.showerror("Preview Error", f"Failed to preview data:\n{preview_result['error']}")
                
        except Exception as e:
            messagebox.showerror("Preview Error", f"Failed to preview data:\n{str(e)}")
        finally:
            self.dialog.config(cursor="")
    
    def _display_preview(self, preview_result: Dict[str, Any]) -> None:
        """Display preview data in tree view."""
        # Clear existing data
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        
        sample_data = preview_result['sample_data']
        if not sample_data:
            self.preview_info_label.config(text="No data found in file")
            return
        
        # Configure columns based on found fields
        fields = preview_result['fields_found']
        self.preview_tree['columns'] = tuple(fields)
        
        # Setup column headers and widths
        for field in fields:
            self.preview_tree.heading(field, text=field.replace('_', ' ').title())
            self.preview_tree.column(field, width=120, minwidth=80)
        
        # Add sample data
        for record in sample_data:
            values = [str(record.get(field, '')) for field in fields]
            self.preview_tree.insert('', 'end', values=values)
        
        # Update info label
        total_records = preview_result['total_records']
        shown_records = preview_result['preview_records']
        self.preview_info_label.config(
            text=f"Showing {shown_records} of {total_records} records"
        )
    
    def _display_validation(self, validation_result: Dict[str, Any]) -> None:
        """Display validation results."""
        self.validation_text.config(state=tk.NORMAL)
        self.validation_text.delete(1.0, tk.END)
        
        if validation_result['valid']:
            self.validation_text.insert(tk.END, "✓ Data validation passed\n", "success")
        else:
            self.validation_text.insert(tk.END, "✗ Data validation failed\n", "error")
        
        self.validation_text.insert(tk.END, f"Records validated: {validation_result['records_validated']}\n")
        
        if validation_result.get('warnings'):
            self.validation_text.insert(tk.END, "\nWarnings:\n")
            for warning in validation_result['warnings']:
                self.validation_text.insert(tk.END, f"• {warning}\n")
        
        if validation_result.get('errors'):
            self.validation_text.insert(tk.END, "\nErrors:\n")
            for error in validation_result['errors']:
                self.validation_text.insert(tk.END, f"• {error}\n")
        
        # Configure text tags for colored output
        self.validation_text.tag_config("success", foreground=self.theme.colors['success'])
        self.validation_text.tag_config("error", foreground=self.theme.colors['error'])
        
        self.validation_text.config(state=tk.DISABLED)
    
    def _clear_preview(self) -> None:
        """Clear preview data."""
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        self.preview_tree['columns'] = ()
        self.preview_info_label.config(text="Select a file to preview data")
    
    def _clear_validation(self) -> None:
        """Clear validation results."""
        self.validation_text.config(state=tk.NORMAL)
        self.validation_text.delete(1.0, tk.END)
        self.validation_text.config(state=tk.DISABLED)
    
    def _import_data(self) -> None:
        """Import data to database."""
        if not self.selected_file or not self.preview_data:
            messagebox.showwarning("Not Ready", "Please preview data before importing.")
            return
        
        data_type = self.data_type_var.get()
        import_mode = self.import_mode_var.get()
        
        # Confirm import
        preview_result = self.preview_data
        total_records = preview_result['total_records']
        
        confirm_msg = f"Import {total_records} {data_type} records using {import_mode} mode?\n\n"
        if import_mode == 'replace':
            confirm_msg += "WARNING: This will replace ALL existing records of this type!"
        
        if not messagebox.askyesno("Confirm Import", confirm_msg):
            return
        
        try:
            # Create progress dialog
            progress_dialog = tk.Toplevel(self.dialog)
            progress_dialog.title("Importing Data...")
            progress_dialog.geometry("400x150")
            progress_dialog.transient(self.dialog)
            progress_dialog.grab_set()
            
            progress_label = tk.Label(progress_dialog, text="Starting import...")
            progress_label.pack(pady=20)
            
            progress_bar = ttk.Progressbar(progress_dialog, length=300, mode='determinate')
            progress_bar.pack(pady=10)
            
            progress_dialog.update()
            
            # Progress callback
            def update_progress(percentage, message):
                if percentage >= 0:
                    progress_bar['value'] = percentage
                    progress_label.config(text=message)
                    progress_dialog.update()
            
            # Import data
            import_result = self.import_engine.import_data(
                file_path=self.selected_file,
                data_type=data_type,
                import_mode=import_mode,
                validate_only=False,
                progress_callback=update_progress
            )
            
            progress_dialog.destroy()
            
            if import_result['success']:
                # Show success message
                stats = import_result
                success_msg = f"Import completed successfully!\n\n"
                success_msg += f"Records processed: {stats['records_processed']}\n"
                success_msg += f"Records inserted: {stats['records_inserted']}\n"
                success_msg += f"Records updated: {stats['records_updated']}\n"
                success_msg += f"Records skipped: {stats['records_skipped']}\n"
                
                if stats.get('errors'):
                    success_msg += f"\nErrors: {len(stats['errors'])}"
                
                messagebox.showinfo("Import Complete", success_msg)
                
                # Close dialog
                self.dialog.destroy()
                
            else:
                error_msg = f"Import failed: {import_result.get('error', 'Unknown error')}"
                if import_result.get('validation_errors'):
                    error_msg += f"\n\nValidation errors:\n"
                    error_msg += "\n".join(import_result['validation_errors'][:5])
                    if len(import_result['validation_errors']) > 5:
                        error_msg += f"\n... and {len(import_result['validation_errors']) - 5} more"
                
                messagebox.showerror("Import Failed", error_msg)
                
        except Exception as e:
            try:
                progress_dialog.destroy()
            except:
                pass
            messagebox.showerror("Import Error", f"Import failed:\n{str(e)}")
    
    def _cancel(self) -> None:
        """Cancel and close dialog."""
        self.dialog.destroy()
