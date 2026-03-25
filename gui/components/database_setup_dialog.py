"""
Dirracuda - Database Setup Dialog

Professional database initialization dialog providing three user-friendly options
when the database is missing or invalid. Ensures graceful startup experience
without application crashes.

Design Decision: Modal dialog with clear options prevents startup crashes
and guides users through database setup with appropriate feedback.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import sys
import threading
import queue
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from gui.utils.style import get_theme
from gui.utils.backend_interface import BackendInterface
from gui.utils.database_access import DatabaseReader
from gui.utils.error_codes import get_error, format_error_message
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.logging_config import get_logger

_logger = get_logger("database_setup_dialog")


class DatabaseSetupDialog:
    """
    Database setup dialog for handling missing or invalid databases.
    
    Provides three options: Import existing database, Initialize new database,
    or Exit application. Ensures professional user experience with clear
    feedback and progress tracking.
    """
    
    def __init__(self, parent: Optional[tk.Tk] = None, 
                 initial_db_path: Optional[str] = None,
                 config_path: Optional[str] = None):
        """
        Initialize database setup dialog.
        
        Args:
            parent: Parent window (None for standalone)
            initial_db_path: Initial database path that failed
            config_path: Path to SMBSeek configuration file
        """
        self.parent = parent
        self.initial_db_path = initial_db_path
        self.config_path = config_path or "./smbseek/conf/config.json"
        self.theme = get_theme()
        
        # Dialog result
        self.result = None  # Will be set to database path or None for exit
        self.dialog = None
        
        # Background operation tracking
        self.operation_thread = None
        self.operation_queue = queue.Queue()
        
        # UI components
        self.cancel_button = None
        self.options_frame = None
        self.progress_bar = None
        self.progress_frame = None
        self.progress_label = None
        
        # Create dialog
        self._create_dialog()
    
    def _create_dialog(self) -> None:
        """Create and configure the setup dialog."""
        if self.parent:
            self.dialog = tk.Toplevel(self.parent)
            self.dialog.transient(self.parent)
        else:
            self.dialog = tk.Tk()
        
        self.dialog.title("Dirracuda - Database Setup")
        self.dialog.geometry("700x560")
        self.dialog.resizable(True, True)
        self.dialog.minsize(650, 520)  # Prevent dialog from becoming too small
        
        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")
        
        # Make modal
        self.dialog.grab_set()
        
        # Prevent closing with window manager
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_exit_option)
        
        # Create layout
        self._create_header()
        self._create_options_frame()
        self._create_progress_frame()
        self._create_button_frame()
        
        # Center dialog
        self._center_dialog()

        # Ensure dialog appears on top and gains focus (critical for VMs)
        if self.parent:  # Only apply if this is a child dialog, not root window
            ensure_dialog_focus(self.dialog, self.parent)

        # Start processing background updates
        self._process_operation_queue()

    def _create_header(self) -> None:
        """Create dialog header with title and description."""
        header_frame = tk.Frame(self.dialog)
        header_frame.pack(fill=tk.X, padx=20, pady=20)
        
        # Title with icon
        title_label = self.theme.create_styled_label(
            header_frame,
            "🗄️ Database Setup Required",
            "title"
        )
        title_label.pack(anchor=tk.W)
        
        # Description
        if self.initial_db_path:
            desc_text = f"Database not found at: {self.initial_db_path}\n\n"
        else:
            desc_text = "No database configured.\n\n"
        
        desc_text += "Choose how to proceed:"
        
        desc_label = self.theme.create_styled_label(
            header_frame,
            desc_text,
            "body"
        )
        desc_label.pack(anchor=tk.W, pady=(10, 0))
    
    def _create_options_frame(self) -> None:
        """Create options selection frame."""
        self.options_frame = tk.Frame(self.dialog)
        self.options_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        # Option 1: Import Database
        self._create_option_card(
            "📁 Import Existing Database",
            "Select an existing Dirracuda database file\nfrom a previous scan or shared by colleagues.",
            self._on_import_option,
            0
        )
        
        # Option 2: Initialize Database  
        self._create_option_card(
            "🔍 Initialize New Database",
            f"Run a new scan to create database\nusing configuration: {self.config_path}",
            self._on_initialize_option,
            1
        )
        
        # Option 3: Exit
        self._create_option_card(
            "❌ Exit Application",
            "Close Dirracuda without\nsetting up a database.",
            self._on_exit_option,
            2
        )
    
    def _create_option_card(self, title: str, description: str, 
                           command: Callable, row: int) -> None:
        """
        Create an option card with hover effects.
        
        Args:
            title: Option title
            description: Option description
            command: Command to execute when clicked
            row: Grid row position
        """
        # Card frame
        card_frame = tk.Frame(self.options_frame)
        card_frame.grid(row=row, column=0, sticky="ew", pady=8)
        self.options_frame.grid_rowconfigure(row, weight=0)
        self.options_frame.grid_columnconfigure(0, weight=1)
        
        # Apply card styling
        self.theme.apply_to_widget(card_frame, "metric_card")
        
        # Title label
        title_label = self.theme.create_styled_label(
            card_frame,
            title,
            "heading"
        )
        title_label.pack(anchor=tk.W)
        
        # Description label
        desc_label = self.theme.create_styled_label(
            card_frame,
            description,
            "body"
        )
        desc_label.pack(anchor=tk.W, pady=(5, 0))
        
        # Make entire card clickable
        def on_click(event):
            command()
        
        card_frame.bind("<Button-1>", on_click)
        title_label.bind("<Button-1>", on_click)
        desc_label.bind("<Button-1>", on_click)
        
        # Add hover effect
        self.theme.create_hover_effect(card_frame)
    
    def _create_progress_frame(self) -> None:
        """Create progress display frame."""
        self.progress_frame = tk.Frame(self.dialog)
        self.progress_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Progress bar
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            mode='indeterminate',
            style="SMBSeek.Horizontal.TProgressbar"
        )
        
        # Progress label
        self.progress_label = self.theme.create_styled_label(
            self.progress_frame,
            "",
            "body"
        )
        
        # Initially hidden
        self._hide_progress()
    
    def _create_button_frame(self) -> None:
        """Create button frame with cancel option."""
        try:
            button_frame = tk.Frame(self.dialog)
            button_frame.pack(fill=tk.X, padx=20, pady=20)
            
            # Cancel button (initially hidden)
            self.cancel_button = tk.Button(
                button_frame,
                text="Cancel Operation",
                command=self._cancel_operation,
                state=tk.DISABLED
            )
            
            # Apply theme with error handling
            try:
                self.theme.apply_to_widget(self.cancel_button, "button_secondary")
            except Exception as e:
                _logger.warning("Failed to apply theme to cancel button: %s", e)
            
            self.cancel_button.pack(side=tk.RIGHT)
            
        except Exception as e:
            # Ensure cancel_button always exists even if creation fails
            _logger.error("Error creating button frame: %s", e)
            self.cancel_button = None
    
    def _center_dialog(self) -> None:
        """Center dialog on screen or parent."""
        self.dialog.update_idletasks()
        
        if self.parent:
            # Center on parent
            x = self.parent.winfo_x() + (self.parent.winfo_width() // 2) - (self.dialog.winfo_width() // 2)
            y = self.parent.winfo_y() + (self.parent.winfo_height() // 2) - (self.dialog.winfo_height() // 2)
        else:
            # Center on screen
            x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
            y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        
        self.dialog.geometry(f"+{x}+{y}")
    
    def _show_progress(self, message: str) -> None:
        """Show progress bar and message."""
        self.progress_label.config(text=message)
        self.progress_label.pack(pady=(0, 5))
        self.progress_bar.pack(fill=tk.X)
        self.progress_bar.start(10)  # Animation speed
        
        # Enable cancel button if it exists
        if hasattr(self, 'cancel_button') and self.cancel_button:
            self.cancel_button.config(state=tk.NORMAL)
        
        # Hide options
        self.options_frame.pack_forget()
    
    def _hide_progress(self) -> None:
        """Hide progress bar and message."""
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.progress_label.pack_forget()
        
        # Disable cancel button if it exists
        if hasattr(self, 'cancel_button') and self.cancel_button:
            self.cancel_button.config(state=tk.DISABLED)
        
        # Show options
        self.options_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
    
    def _on_import_option(self) -> None:
        """Handle import database option."""
        # File selection dialog
        filetypes = [
            ("SQLite databases", "*.db *.sqlite *.sqlite3"),
            ("All files", "*.*")
        ]
        
        initial_dir = "./smbseek" if os.path.exists("./smbseek") else "."
        
        filename = filedialog.askopenfilename(
            title="Select Dirracuda Database File",
            filetypes=filetypes,
            initialdir=initial_dir
        )
        
        if filename:
            # Validate database file
            self._show_progress("Validating database file...")
            
            # Start validation in background
            self.operation_thread = threading.Thread(
                target=self._validate_database_worker,
                args=(filename,),
                daemon=True
            )
            self.operation_thread.start()
    
    def _on_initialize_option(self) -> None:
        """Handle initialize database option."""
        # Confirm initialization
        confirm_msg = f"Initialize new database using configuration:\n{self.config_path}\n\n"
        confirm_msg += "This will run a scan to create the database.\nThis may take several minutes.\n\n"
        confirm_msg += "Continue?"
        
        if messagebox.askyesno("Confirm Initialization", confirm_msg):
            self._show_progress(f"Initiating scan using {self.config_path}")
            
            # Start initialization in background
            self.operation_thread = threading.Thread(
                target=self._initialize_database_worker,
                daemon=True
            )
            self.operation_thread.start()
    
    def _on_exit_option(self) -> None:
        """Handle exit application option."""
        # Confirm exit
        if messagebox.askyesno(
            "Exit Dirracuda",
            "Are you sure you want to exit without setting up a database?"
        ):
            self.result = None
            self._close_dialog()
    
    def _validate_database_worker(self, db_path: str) -> None:
        """
        Background worker to validate database file.
        
        Args:
            db_path: Path to database file to validate
        """
        try:
            # First validate file exists and is readable
            if not os.path.exists(db_path):
                raise FileNotFoundError(f"Database file not found: {db_path}")
            
            if not os.access(db_path, os.R_OK):
                raise PermissionError(f"Cannot read database file: {db_path}")
            
            # Update progress
            self.operation_queue.put({
                'type': 'progress',
                'message': 'Validating database structure...'
            })
            
            # Test database access and structure
            db_reader = DatabaseReader(db_path)
            
            # Perform comprehensive schema analysis
            analysis = db_reader.analyze_database_schema(db_path)
            
            if not analysis['valid']:
                error_msg = "Database validation failed:\n"
                if analysis['errors']:
                    error_msg += f"• {analysis['errors'][0]}\n"
                error_msg += f"• Compatibility: {analysis['compatibility_level']}\n"
                error_msg += f"• Recommendation: {analysis['import_recommendation']}"
                raise ValueError(error_msg)
            
            # Update progress with schema analysis results
            self.operation_queue.put({
                'type': 'progress', 
                'message': f'Schema analysis: {analysis["compatibility_level"]} compatibility'
            })
            
            # Try to read basic structure to ensure data is accessible
            stats = db_reader.get_dashboard_summary()
            
            # Update progress with database info
            self.operation_queue.put({
                'type': 'progress',
                'message': f'Data validation successful - {stats.get("total_servers", 0)} servers found'
            })
            
            # Success - provide comprehensive database information
            success_message = f"Database imported successfully!\n\n"
            success_message += f"📊 **Database Summary:**\n"
            success_message += f"• Compatibility: {analysis['compatibility_level'].title()} Dirracuda database\n"
            success_message += f"• Total tables: {analysis['schema_info']['total_tables']}\n"
            success_message += f"• Total records: {analysis['schema_info']['total_records']:,}\n\n"
            
            success_message += f"🎯 **Core Data:**\n"
            success_message += f"• SMB servers: {stats.get('total_servers', 0):,}\n"
            success_message += f"• Share access records: {stats.get('accessible_shares', 0):,}\n"
            success_message += f"• High-risk vulnerabilities: {stats.get('high_risk_vulnerabilities', 0)}\n"
            recent_discoveries = stats.get('recent_discoveries', {})
            if isinstance(recent_discoveries, dict):
                success_message += f"• Recent discoveries: {recent_discoveries.get('display', '--')}\n"
            else:
                success_message += f"• Recent discoveries: {recent_discoveries}\n"
            
            if analysis['warnings']:
                success_message += f"\n⚠️  **Warnings:**\n"
                for warning in analysis['warnings'][:3]:  # Show first 3 warnings
                    success_message += f"• {warning}\n"
                if len(analysis['warnings']) > 3:
                    success_message += f"• ... and {len(analysis['warnings']) - 3} more warnings"
            
            self.operation_queue.put({
                'type': 'complete',
                'success': True,
                'result': db_path,
                'message': success_message
            })
            
        except FileNotFoundError as e:
            error_info = get_error("DB001", {"path": db_path})
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': error_info['full_message']
            })
        except PermissionError as e:
            error_info = get_error("DB002", {"path": db_path})
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': error_info['full_message']
            })
        except ValueError as e:
            # This is a validation error, likely VAL001 or VAL002
            error_msg = str(e)
            if "validation failed" in error_msg.lower():
                # Use the error as-is since it may already have error codes from analysis
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': error_msg
                })
            else:
                error_info = get_error("VAL002", {"compatibility_level": "unknown"})
                self.operation_queue.put({
                    'type': 'complete',
                    'success': False,
                    'error': error_info['full_message']
                })
        except Exception as e:
            # General import failure
            error_info = get_error("IMP001", {"stage": "validation", "error": str(e)})
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': error_info['full_message']
            })
    
    def _initialize_database_worker(self) -> None:
        """Background worker to initialize new database."""
        try:
            # Update progress
            self.operation_queue.put({
                'type': 'progress',
                'message': 'Checking backend availability...'
            })
            
            # Check backend interface
            backend = BackendInterface("./smbseek")
            if not backend.is_backend_available():
                raise RuntimeError("SMBSeek backend not available")
            
            self.operation_queue.put({
                'type': 'progress',
                'message': 'Starting initialization scan...'
            })
            
            # Progress callback for scan updates
            def progress_callback(percentage, message):
                self.operation_queue.put({
                    'type': 'progress',
                    'message': f"Scan progress: {message}"
                })
            
            # Run initialization scan
            result = backend.run_initialization_scan(
                config_path=self.config_path,
                progress_callback=progress_callback
            )
            
            if result.get('success'):
                db_path = result.get('database_path', './smbseek/smbseek.db')
                self.operation_queue.put({
                    'type': 'complete',
                    'success': True,
                    'result': db_path,
                    'message': f"Database initialized: {result.get('servers_found', 0)} servers found"
                })
            else:
                raise RuntimeError(result.get('error', 'Initialization scan failed'))
            
        except Exception as e:
            self.operation_queue.put({
                'type': 'complete',
                'success': False,
                'error': str(e)
            })
    
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
                        # Success
                        messagebox.showinfo(
                            "Success",
                            update.get('message', 'Operation completed successfully')
                        )
                        self.result = update['result']
                        self._close_dialog()
                    else:
                        # Error
                        error_msg = f"Operation failed:\n{update['error']}\n\n"
                        error_msg += "Please try a different option."
                        messagebox.showerror("Operation Failed", error_msg)
                        
        except queue.Empty:
            pass
        
        # Schedule next check
        self.dialog.after(100, self._process_operation_queue)
    
    def _cancel_operation(self) -> None:
        """Cancel current background operation."""
        if self.operation_thread and self.operation_thread.is_alive():
            # Note: Python threads cannot be forcibly terminated
            # This provides user feedback but the operation will complete
            messagebox.showinfo(
                "Cancelling",
                "Operation is being cancelled.\nPlease wait for it to complete."
            )
            self._hide_progress()
    
    def _close_dialog(self) -> None:
        """Close the dialog."""
        if self.dialog:
            self.dialog.destroy()
    
    def show_modal(self) -> Optional[str]:
        """
        Show dialog modally and return result.
        
        Returns:
            Database path if successful, None if user chose to exit
        """
        if self.dialog:
            self.dialog.wait_window()
        return self.result


def show_database_setup_dialog(parent: Optional[tk.Tk] = None,
                              initial_db_path: Optional[str] = None,
                              config_path: Optional[str] = None) -> Optional[str]:
    """
    Show database setup dialog and return selected database path.
    
    Args:
        parent: Parent window (None for standalone)
        initial_db_path: Initial database path that failed
        config_path: Path to SMBSeek configuration file
        
    Returns:
        Database path if successful, None if user chose to exit
    """
    dialog = DatabaseSetupDialog(parent, initial_db_path, config_path)
    return dialog.show_modal()