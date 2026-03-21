"""
SMBSeek Application Configuration Dialog

Comprehensive configuration dialog for managing xsmbseek application settings
including SMBSeek installation path, configuration file path, and database path.
Replaces the existing config button functionality while preserving access to
the existing SMBSeek configuration file editor.

Design Decision: Form-based interface with real-time validation provides
clear feedback and reduces configuration errors compared to raw file editing.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import sys

from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus


class AppConfigDialog:
    """
    Application configuration dialog with form-based interface.
    
    Provides configuration management for:
    - SMBSeek installation path
    - SMBSeek configuration file path 
    - Database file path
    - Access to existing SMBSeek config editor
    
    Features real-time path validation, file browsers, and integration
    with the existing configuration editor functionality.
    """
    
    def __init__(self, parent: tk.Widget, settings_manager=None, 
                 config_editor_callback: Optional[Callable[[str], None]] = None,
                 main_config=None, refresh_callback: Optional[Callable[[], None]] = None):
        """
        Initialize application configuration dialog.
        
        Args:
            parent: Parent widget
            settings_manager: SettingsManager instance for persistence
            config_editor_callback: Callback to open existing config editor
            main_config: XSMBSeekConfig instance for main application config
            refresh_callback: Callback to refresh database connection after changes
        """
        self.parent = parent
        self.settings_manager = settings_manager
        self.config_editor_callback = config_editor_callback
        self.main_config = main_config
        self.refresh_callback = refresh_callback
        self.theme = get_theme()
        
        # Configuration paths
        self.smbseek_path = ""
        self.config_path = ""
        self.database_path = ""
        
        # Validation state
        self.validation_results = {
            'smbseek': {'valid': False, 'message': ''},
            'config': {'valid': False, 'message': ''},
            'database': {'valid': False, 'message': ''}
        }
        
        # UI components
        self.dialog = None
        self.smbseek_var = None
        self.config_var = None
        self.database_var = None
        self.smbseek_status_label = None
        self.config_status_label = None
        self.database_status_label = None
        
        # Load current settings
        self._load_current_settings()
        
        # Create dialog
        self._create_dialog()
        
    def _load_current_settings(self) -> None:
        """Load current configuration settings."""
        if self.main_config:
            self.smbseek_path = str(self.main_config.get_smbseek_path())
            self.config_path = str(self.main_config.get_config_path())
            self.database_path = str(self.main_config.get_database_path())
            return

        if self.settings_manager:
            self.smbseek_path = self.settings_manager.get_backend_path()
            
            # Derive config path from SMBSeek path
            smbseek_config = Path(self.smbseek_path) / "conf" / "config.json"
            self.config_path = str(smbseek_config)
            
            # Get database path from settings or derive from SMBSeek path
            db_path = self.settings_manager.get_setting('backend.database_path')
            if db_path and db_path != '../backend/smbseek.db':  # Skip old default
                self.database_path = db_path
            else:
                # Derive from SMBSeek path
                self.database_path = str(Path(self.smbseek_path) / "smbseek.db")
        else:
            # Fallback defaults
            self.smbseek_path = "./smbseek"
            self.config_path = "./conf/config.json"
            self.database_path = "./smbseek/smbseek.db"
    
    def _create_dialog(self) -> None:
        """Create the configuration dialog window."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("SMBSeek - Application Configuration")
        self.dialog.geometry("600x760")
        self.dialog.minsize(600, 700)
        
        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")
        
        # Make window modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        
        # Center window
        self._center_window()
        
        # Build UI sections
        self._create_header()
        self._create_paths_section()
        self._create_button_panel()

        # Initialize validation
        self._validate_all_paths()

        # Normalize all default Tk children (LabelFrame title, entries, etc.).
        self.theme.apply_theme_to_application(self.dialog)

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

    def _center_window(self) -> None:
        """Center the dialog window on screen."""
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def _create_header(self) -> None:
        """Create dialog header with title and description."""
        header_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=20, pady=20)
        
        # Title
        title_label = tk.Label(
            header_frame,
            text="Application Configuration",
            font=("Arial", 16, "bold")
        )
        self.theme.apply_to_widget(title_label, "heading")
        title_label.pack(anchor=tk.W)
        
        # Description
        desc_label = tk.Label(
            header_frame,
            text="Configure paths for SMBSeek integration and data storage.",
            font=("Arial", 10)
        )
        self.theme.apply_to_widget(desc_label, "text")
        desc_label.pack(anchor=tk.W, pady=(5, 0))
    
    def _create_paths_section(self) -> None:
        """Create the main paths configuration section."""
        # Main container with scrolling if needed
        main_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(main_frame, "main_window")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        
        # SMBSeek Installation Path
        self._create_path_config_section(
            main_frame,
            "SMBSeek Installation",
            "Path to your SMBSeek installation directory",
            "smbseek"
        )
        
        # Database Path - moved to second position
        self._create_path_config_section(
            main_frame,
            "Database Location",
            "Path to the SQLite database file",
            "database"
        )
        
        # SMBSeek Configuration Path - moved to third position
        self._create_path_config_section(
            main_frame,
            "SMBSeek Configuration",
            "Path to SMBSeek's config.json file",
            "config",
            show_edit_button=True
        )
    
    def _create_path_config_section(self, parent: tk.Widget, title: str, 
                                  description: str, path_type: str,
                                  show_edit_button: bool = False) -> None:
        """
        Create a configuration section for a specific path.
        
        Args:
            parent: Parent widget
            title: Section title
            description: Section description
            path_type: Type of path ('smbseek', 'config', 'database')
            show_edit_button: Whether to show "Edit Config" button
        """
        # Section frame
        section_frame = tk.LabelFrame(parent, text=title, font=("Arial", 12, "bold"))
        self.theme.apply_to_widget(section_frame, "card")
        try:
            section_frame.configure(fg=self.theme.colors["text"])
        except tk.TclError:
            pass
        section_frame.pack(fill=tk.X, pady=(0, 15), padx=5, ipady=10)
        
        # Description
        desc_label = tk.Label(section_frame, text=description, font=("Arial", 9))
        self.theme.apply_to_widget(desc_label, "text")
        desc_label.pack(anchor=tk.W, padx=15, pady=(5, 10))
        
        # Path input frame
        input_frame = tk.Frame(section_frame)
        self.theme.apply_to_widget(input_frame, "card")
        input_frame.pack(fill=tk.X, padx=15, pady=(0, 5))
        
        # Path variable and entry
        if path_type == "smbseek":
            self.smbseek_var = tk.StringVar(value=self.smbseek_path)
            path_var = self.smbseek_var
        elif path_type == "config":
            self.config_var = tk.StringVar(value=self.config_path)
            path_var = self.config_var
        else:  # database
            self.database_var = tk.StringVar(value=self.database_path)
            path_var = self.database_var
        
        # Path entry field
        path_entry = tk.Entry(input_frame, textvariable=path_var, font=("Arial", 10))
        self.theme.apply_to_widget(path_entry, "entry")
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Browse button
        browse_button = tk.Button(
            input_frame,
            text="Browse...",
            command=lambda: self._browse_path(path_type)
        )
        self.theme.apply_to_widget(browse_button, "button_secondary")
        browse_button.pack(side=tk.RIGHT, padx=(10, 0))
        
        # Status label
        status_frame = tk.Frame(section_frame)
        self.theme.apply_to_widget(status_frame, "card")
        status_frame.pack(fill=tk.X, padx=15, pady=(5, 5))
        
        if path_type == "smbseek":
            self.smbseek_status_label = tk.Label(status_frame, font=("Arial", 9))
            status_label = self.smbseek_status_label
        elif path_type == "config":
            self.config_status_label = tk.Label(status_frame, font=("Arial", 9))
            status_label = self.config_status_label
        else:  # database
            self.database_status_label = tk.Label(status_frame, font=("Arial", 9))
            status_label = self.database_status_label
        
        self.theme.apply_to_widget(status_label, "text")
        status_label.pack(anchor=tk.W)
        
        # Edit button for config section
        if show_edit_button:
            edit_frame = tk.Frame(section_frame)
            self.theme.apply_to_widget(edit_frame, "card")
            edit_frame.pack(fill=tk.X, padx=15, pady=(5, 5))
            
            edit_button = tk.Button(
                edit_frame,
                text="Edit SMBSeek Config...",
                command=self._open_smbseek_config_editor
            )
            self.theme.apply_to_widget(edit_button, "button_secondary")
            edit_button.pack(anchor=tk.W)
        
        # Bind validation to path changes
        path_var.trace('w', lambda *args: self._validate_path(path_type))
    
    def _browse_path(self, path_type: str) -> None:
        """
        Open file/folder browser for path selection.
        
        Args:
            path_type: Type of path to browse for
        """
        current_path = ""
        if path_type == "smbseek":
            current_path = self.smbseek_var.get()
            # Browse for directory
            selected = filedialog.askdirectory(
                title="Select SMBSeek Installation Directory",
                initialdir=os.path.dirname(current_path) if current_path else os.getcwd()
            )
            if selected:
                self.smbseek_var.set(selected)
                
        elif path_type == "config":
            current_path = self.config_var.get()
            # Browse for JSON file
            selected = filedialog.askopenfilename(
                title="Select SMBSeek Configuration File",
                initialdir=os.path.dirname(current_path) if current_path else os.getcwd(),
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
            )
            if selected:
                self.config_var.set(selected)
                
        else:  # database
            current_path = self.database_var.get()
            # Browse for database file
            selected = filedialog.askopenfilename(
                title="Select Database File",
                initialdir=os.path.dirname(current_path) if current_path else os.getcwd(),
                filetypes=[("SQLite files", "*.db"), ("All files", "*.*")]
            )
            if selected:
                self.database_var.set(selected)
    
    def _validate_path(self, path_type: str) -> None:
        """
        Validate a specific path and update UI status.
        
        Args:
            path_type: Type of path to validate
        """
        if path_type == "smbseek":
            path = self.smbseek_var.get()
            result = self._validate_smbseek_path(path)
            self.validation_results['smbseek'] = result
            self._update_status_label(self.smbseek_status_label, result)
            
            # Auto-update dependent paths
            if result['valid']:
                # Update config path
                config_path = str(Path(path) / "conf" / "config.json")
                self.config_var.set(config_path)
                
                # Update database path if it's still the default pattern
                current_db = self.database_var.get()
                if not current_db or "smbseek.db" in current_db:
                    db_path = str(Path(path) / "smbseek.db")
                    self.database_var.set(db_path)
            
        elif path_type == "config":
            path = self.config_var.get()
            result = self._validate_config_path(path)
            self.validation_results['config'] = result
            self._update_status_label(self.config_status_label, result)
            
        else:  # database
            path = self.database_var.get()
            result = self._validate_database_path(path)
            self.validation_results['database'] = result
            self._update_status_label(self.database_status_label, result)
    
    def _validate_smbseek_path(self, path: str) -> Dict[str, Any]:
        """
        Validate SMBSeek installation path.
        
        Args:
            path: Path to validate
            
        Returns:
            Validation result with 'valid' and 'message' keys
        """
        if not path:
            return {'valid': False, 'message': 'Please specify SMBSeek installation path'}
        
        path_obj = Path(path)
        
        if not path_obj.exists():
            return {'valid': False, 'message': '❌ Path does not exist'}
        
        if not path_obj.is_dir():
            return {'valid': False, 'message': '❌ Path is not a directory'}
        
        # Check for smbseek executable
        smbseek_script = path_obj / "smbseek"
        if not smbseek_script.exists():
            return {'valid': False, 'message': '❌ smbseek executable not found in directory'}

        # Try to get version to confirm it's working
        try:
            result = subprocess.run(
                [str(smbseek_script), "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return {'valid': True, 'message': f'✅ Valid SMBSeek installation ({version})'}
            else:
                return {'valid': False, 'message': '❌ SMBSeek executable not working'}
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            # Script exists but may not be executable - still mark as valid
            return {'valid': True, 'message': '✅ SMBSeek installation found (version check failed)'}
    
    def _validate_config_path(self, path: str) -> Dict[str, Any]:
        """
        Validate SMBSeek configuration file path.
        
        Args:
            path: Path to validate
            
        Returns:
            Validation result with 'valid' and 'message' keys
        """
        if not path:
            return {'valid': False, 'message': 'Please specify configuration file path'}
        
        path_obj = Path(path)
        
        if not path_obj.exists():
            # Check if parent directory exists and we can potentially create the config
            parent_dir = path_obj.parent
            if parent_dir.exists() and path_obj.name.endswith('.json'):
                return {'valid': True, 'message': '⚠️ Configuration file will be created'}
            else:
                return {'valid': False, 'message': '❌ Configuration file not found'}
        
        if not path_obj.is_file():
            return {'valid': False, 'message': '❌ Path is not a file'}
        
        # Try to parse as JSON
        try:
            with open(path_obj, 'r') as f:
                config_data = json.load(f)
            
            # Basic SMBSeek config validation
            if isinstance(config_data, dict):
                return {'valid': True, 'message': '✅ Valid configuration file'}
            else:
                return {'valid': False, 'message': '❌ Invalid JSON structure'}
                
        except json.JSONDecodeError as e:
            return {'valid': False, 'message': f'❌ JSON parsing error: {str(e)[:50]}...'}
        except Exception as e:
            return {'valid': False, 'message': f'❌ Error reading file: {str(e)[:50]}...'}
    
    def _validate_database_path(self, path: str) -> Dict[str, Any]:
        """
        Validate database file path.
        
        Args:
            path: Path to validate
            
        Returns:
            Validation result with 'valid' and 'message' keys
        """
        if not path:
            return {'valid': False, 'message': 'Please specify database file path'}
        
        path_obj = Path(path)
        
        if not path_obj.exists():
            # Database doesn't exist yet - that's okay
            parent_dir = path_obj.parent
            if not parent_dir.exists():
                return {'valid': False, 'message': '❌ Parent directory does not exist'}
            return {'valid': True, 'message': '⚠️ Database file will be created'}
        
        if not path_obj.is_file():
            return {'valid': False, 'message': '❌ Path is not a file'}
        
        # Try to validate as SQLite database
        try:
            import sqlite3
            with sqlite3.connect(str(path_obj)) as conn:
                cursor = conn.cursor()
                
                # Check for expected tables
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                
                expected_tables = ['smb_servers', 'scan_sessions']
                if any(table in tables for table in expected_tables):
                    # Get server count for display
                    if 'smb_servers' in tables:
                        cursor.execute("SELECT COUNT(*) FROM smb_servers")
                        count = cursor.fetchone()[0]
                        return {'valid': True, 'message': f'✅ Valid database ({count:,} servers)'}
                    else:
                        return {'valid': True, 'message': '✅ Valid SQLite database'}
                else:
                    return {'valid': False, 'message': '❌ Database does not contain expected tables'}
                    
        except sqlite3.Error as e:
            return {'valid': False, 'message': f'❌ SQLite error: {str(e)[:50]}...'}
        except Exception as e:
            return {'valid': False, 'message': f'❌ Database validation error: {str(e)[:50]}...'}
    
    def _update_status_label(self, label: tk.Label, result: Dict[str, Any]) -> None:
        """
        Update status label with validation result.
        
        Args:
            label: Label widget to update
            result: Validation result dictionary
        """
        label.config(text=result['message'])
        
        # Apply color based on validation result
        if result['valid']:
            if '✅' in result['message']:
                label.config(fg='green')
            else:  # Warning
                label.config(fg='orange')
        else:
            label.config(fg='red')
    
    def _validate_all_paths(self) -> None:
        """Validate all paths on initial load."""
        self._validate_path("smbseek")
        self._validate_path("config")
        self._validate_path("database")
    
    def _open_smbseek_config_editor(self) -> None:
        """Open the existing SMBSeek configuration editor."""
        config_path = self.config_var.get()
        
        if not config_path:
            messagebox.showwarning(
                "No Configuration File",
                "Please specify a configuration file path first."
            )
            return
        
        if self.config_editor_callback:
            try:
                self.config_editor_callback(config_path)
            except Exception as e:
                messagebox.showerror(
                    "Configuration Editor Error",
                    f"Failed to open configuration editor:\n{str(e)}"
                )
        else:
            messagebox.showinfo(
                "Configuration Editor",
                "Configuration editor callback not available."
            )
    
    def _create_button_panel(self) -> None:
        """Create dialog button panel."""
        button_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(button_frame, "main_window")
        button_frame.pack(fill=tk.X, padx=20, pady=(5, 20))
        
        # Cancel button
        cancel_button = tk.Button(
            button_frame,
            text="Cancel",
            command=self._on_cancel
        )
        self.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(side=tk.RIGHT, padx=(10, 0))
        
        # OK button
        ok_button = tk.Button(
            button_frame,
            text="OK",
            command=self._on_ok
        )
        self.theme.apply_to_widget(ok_button, "button_primary")
        ok_button.pack(side=tk.RIGHT)
    
    def _on_ok(self) -> None:
        """Save configuration and close dialog."""
        if self._validate_and_save():
            self.dialog.destroy()
    
    def _on_cancel(self) -> None:
        """Cancel and close dialog without saving."""
        self.dialog.destroy()
    
    def _validate_and_save(self) -> bool:
        """
        Validate all configurations and save if valid.
        
        Returns:
            True if validation and save successful, False otherwise
        """
        # Re-validate all paths
        self._validate_all_paths()
        
        # Check for any validation errors
        invalid_paths = []
        for path_type, result in self.validation_results.items():
            if not result['valid']:
                invalid_paths.append(path_type.title())
        
        if invalid_paths:
            messagebox.showerror(
                "Configuration Validation Failed",
                f"The following paths have validation errors:\n\n" +
                "\n".join(f"• {path}" for path in invalid_paths) +
                "\n\nPlease fix these issues before saving."
            )
            return False
        
        # Save to both settings manager (GUI preferences) and main config (application settings)
        try:
            # Save to settings manager (GUI preferences)
            if self.settings_manager:
                self.settings_manager.set_backend_path(self.smbseek_var.get())
                self.settings_manager.set_database_path(self.database_var.get())
                self.settings_manager.set_setting('backend.config_path', self.config_var.get())
            
            # Save to main config (application settings) - this is what the app actually uses
            if self.main_config:
                # Reload config from disk to avoid overwriting edits made via the config editor
                try:
                    self.main_config.config = self.main_config._load_config()
                except Exception:
                    pass
                old_db_path = str(self.main_config.get_database_path()) if self.main_config.get_database_path() else None
                
                self.main_config.set_smbseek_path(self.smbseek_var.get())
                self.main_config.set_database_path(self.database_var.get())
                self.main_config.save_config()
                
                # If database path changed, refresh the database connection
                new_db_path = self.database_var.get()
                if old_db_path != new_db_path and self.refresh_callback:
                    self.refresh_callback()
            
            return True
            
        except Exception as e:
            messagebox.showerror(
                "Configuration Save Failed",
                f"Failed to save configuration:\n{str(e)}\n\n"
                "Please check your settings and try again."
            )
            return False
        
        return True


def open_app_config_dialog(parent: tk.Widget, settings_manager=None, 
                          config_editor_callback: Optional[Callable[[str], None]] = None,
                          main_config=None, refresh_callback: Optional[Callable[[], None]] = None) -> None:
    """
    Open application configuration dialog.
    
    Args:
        parent: Parent widget
        settings_manager: SettingsManager instance for persistence
        config_editor_callback: Callback to open existing config editor
        main_config: XSMBSeekConfig instance for main application config
        refresh_callback: Callback to refresh database connection after changes
    """
    try:
        AppConfigDialog(parent, settings_manager, config_editor_callback, main_config, refresh_callback)
    except Exception as e:
        messagebox.showerror(
            "Configuration Dialog Error",
            f"Failed to open configuration dialog:\n{str(e)}"
        )
