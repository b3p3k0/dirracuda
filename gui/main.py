#!/usr/bin/env python3
"""
SMBSeek GUI - Legacy Entry Point

DEPRECATED: This module is maintained for backward compatibility only.
Prefer ./xsmbseek as the supported GUI entry point.

This module provides SMBSeekGUI for backward compatibility but delegates
all scan operations to the unified ScanManager path established in xsmbseek.
Direct invocation via `python gui/main.py` is deprecated.

Usage:
    ./xsmbseek [--mock]                    # Preferred
    python gui/main.py [--mock] [--config] # Legacy (deprecated)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import argparse
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure project root is in path for package imports (handles direct invocation)
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from gui.components.dashboard import DashboardWidget
from gui.components.server_list_window import open_server_list_window, ServerListWindow
from gui.components.config_editor_window import open_config_editor_window
from gui.components.app_config_dialog import open_app_config_dialog
from gui.components.data_import_dialog import open_data_import_dialog
from gui.components.database_setup_dialog import show_database_setup_dialog
from shared.db_migrations import run_migrations
from gui.utils.database_access import DatabaseReader
from gui.utils.backend_interface import BackendInterface
from gui.utils.style import get_theme, apply_theme_to_window
from gui.utils.settings_manager import get_settings_manager
from gui.utils.ui_dispatcher import UIDispatcher
from gui.utils.scan_manager import get_scan_manager


class SMBSeekGUI:
    """
    Main SMBSeek GUI application.
    
    Coordinates between dashboard, backend interface, and drill-down windows.
    Handles application lifecycle, error recovery, and user interactions.
    
    Design Pattern: Main controller that orchestrates all GUI components
    while maintaining separation of concerns through dependency injection.
    """
    
    def __init__(self, mock_mode: bool = False, config_path: Optional[str] = None, backend_path: Optional[str] = None):
        """
        Initialize SMBSeek GUI application.
        
        Args:
            mock_mode: Whether to use mock data for testing
            config_path: Optional path to configuration file
            backend_path: Optional path to backend directory
        """
        self.mock_mode = mock_mode
        self.config_path = None  # will be resolved below
        self.backend_path = backend_path
        
        # GUI state
        self.root = None
        self.dashboard = None
        self.drill_down_windows = {}
        
        # Backend interfaces
        self.db_reader = None
        self.backend_interface = None
        
        # Settings manager
        self.settings_manager = get_settings_manager()
        try:
            preferred_theme = self.settings_manager.get_setting("interface.theme", "light")
            get_theme().set_mode(preferred_theme)
        except Exception:
            get_theme().set_mode("light")
        # Resolve config path preference: prioritize CLI, else settings, else repo conf/config.json
        try:
            if config_path:
                resolved_cfg = Path(config_path).expanduser().resolve()
            else:
                stored = self.settings_manager.get_setting('backend.config_path', '') if self.settings_manager else ''
                if stored:
                    resolved_cfg = Path(stored).expanduser().resolve()
                else:
                    resolved_cfg = Path.cwd() / "conf" / "config.json"
            if not resolved_cfg.exists():
                fallback = Path(__file__).resolve().parent.parent / "conf" / "config.json"
                resolved_cfg = fallback
            self.config_path = str(resolved_cfg)
            if self.settings_manager:
                try:
                    self.settings_manager.set_setting('backend.config_path', self.config_path)
                except Exception:
                    pass
        except Exception:
            self.config_path = config_path or str(Path.cwd() / "conf" / "config.json")

        # Thread-safe UI dispatcher (initialized after root creation)
        self.ui_dispatcher = None
        self.scan_manager = None

        self._initialize_application()
        
        # Set up global exception handler
        self._setup_global_exception_handler()
    
    def _setup_global_exception_handler(self) -> None:
        """Set up global exception handler for unhandled errors."""
        def handle_exception(exc_type, exc_value, exc_traceback):
            # Don't catch KeyboardInterrupt (Ctrl+C)
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_traceback)
                return
            
            error_msg = f"Unhandled error: {exc_type.__name__}: {exc_value}"
            
            try:
                # Try to show GUI error dialog
                messagebox.showerror(
                    "Unexpected Error",
                    f"An unexpected error occurred:\n\n{error_msg}\n\n"
                    "The application may continue to work, but you should save your work "
                    "and restart if you experience issues.\n\n"
                    "Please report this error if it persists."
                )
            except:
                # Fall back to console
                print(f"CRITICAL ERROR: {error_msg}")
                import traceback
                traceback.print_exception(exc_type, exc_value, exc_traceback)
        
        # Install the handler
        sys.excepthook = handle_exception
    
    def _get_backend_path(self) -> str:
        """
        Get backend path with proper precedence: CLI arg > settings > default.
        
        Returns:
            Backend path to use for initialization
        """
        # CLI argument takes precedence
        if self.backend_path:
            return self.backend_path
        
        # Then settings manager
        return self.settings_manager.get_backend_path()
    
    def _initialize_application(self) -> None:
        """Initialize all application components."""
        try:
            self._setup_backend_interfaces()
            self._create_main_window()
            self._setup_scan_manager()
            self._create_dashboard()
            self._setup_event_handlers()
            
            if self.mock_mode:
                self._enable_mock_mode()
            
        except Exception as e:
            self._handle_initialization_error(e)
    
    def _setup_backend_interfaces(self) -> None:
        """Initialize backend communication interfaces with graceful database setup."""
        try:
            # Get database path from settings (last used or default)
            db_path = self.settings_manager.get_database_path()
            
            # Initialize backend interface first
            self.backend_interface = BackendInterface(self._get_backend_path())
            
            # Handle database setup
            validated_db_path = self._ensure_database_available(db_path)
            if not validated_db_path:
                # User chose to exit during database setup
                sys.exit(0)

            # Run lightweight migrations (idempotent) before opening readers
            try:
                run_migrations(validated_db_path)
            except Exception as mig_err:
                # Warn but continue; DatabaseReader may still work
                print(f"Warning: failed to apply migrations: {mig_err}")
            
            # Initialize database reader with validated path
            self.db_reader = DatabaseReader(validated_db_path)
            
            # Update settings with successful database path
            self.settings_manager.set_database_path(validated_db_path, validate=True)
            
            # Test backend availability for non-mock mode
            if not self.mock_mode and not self.backend_interface.is_backend_available():
                response = messagebox.askyesno(
                    "Backend Not Available",
                    "SMBSeek backend is not accessible. Would you like to continue in mock mode for testing?",
                    icon="warning"
                )
                if response:
                    self.mock_mode = True
                else:
                    # Return to database setup instead of crashing
                    raise RuntimeError("Backend not available and mock mode declined")
            
        except Exception as e:
            # Show error dialog and return to database setup instead of crashing
            self._handle_backend_setup_error(e)
    
    def _ensure_database_available(self, initial_db_path: str) -> Optional[str]:
        """
        Ensure database is available, showing setup dialog if needed.
        
        Args:
            initial_db_path: Initial database path to try
            
        Returns:
            Validated database path or None if user chose to exit
        """
        # Try to validate the current database path
        temp_db_reader = DatabaseReader()  # Create temporary instance for validation
        validation_result = temp_db_reader.validate_database(initial_db_path)
        
        if validation_result['valid']:
            # Database is valid, use it
            return initial_db_path
        
        # Database is missing or invalid, show setup dialog
        while True:
            selected_db_path = show_database_setup_dialog(
                parent=self.root,
                initial_db_path=initial_db_path,
                config_path=self.config_path
            )
            
            if selected_db_path is None:
                # User chose to exit
                return None
            
            # Validate the selected database
            validation_result = temp_db_reader.validate_database(selected_db_path)
            if validation_result['valid']:
                return selected_db_path
            else:
                # Show error and loop back to setup dialog
                messagebox.showerror(
                    "Database Validation Failed",
                    f"Selected database is not valid:\n{validation_result['error']}\n\n"
                    "Please try a different option."
                )
                initial_db_path = selected_db_path  # Show the failed path in dialog
    
    def _handle_backend_setup_error(self, error: Exception) -> None:
        """
        Handle backend setup errors gracefully by returning to database setup.
        
        Args:
            error: The exception that occurred
        """
        error_msg = f"Backend setup failed: {str(error)}\n\n"
        error_msg += "Would you like to try setting up the database again?"
        
        if messagebox.askyesno("Backend Setup Error", error_msg):
            # Retry database setup
            try:
                validated_db_path = self._ensure_database_available("../backend/smbseek.db")
                if validated_db_path:
                    # Try again with new database
                    self._setup_backend_interfaces()
                    return
            except Exception as retry_error:
                messagebox.showerror(
                    "Setup Failed", 
                    f"Database setup failed again: {retry_error}\n\n"
                    "The application will start in mock mode."
                )
        
        # Fall back to mock mode instead of crashing
        self.mock_mode = True
        try:
            self.db_reader = DatabaseReader("../backend/smbseek.db")  # Will use mock mode
            self.backend_interface = BackendInterface(self._get_backend_path())
        except Exception:
            # If even mock mode fails, this is a critical error
            self._handle_initialization_error(Exception("Failed to initialize even in mock mode"))
    
    def _create_main_window(self) -> None:
        """Create and configure main application window."""
        self.root = tk.Tk()
        self.root.title("SMBSeek Security Toolkit")
        self.root.geometry("800x250")
        self.root.minsize(800, 240)
        
        # Apply theme
        apply_theme_to_window(self.root)
        
        # Configure window behavior
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Center window on screen
        self._center_window()

    def _setup_scan_manager(self) -> None:
        """Initialize thread-safe UI dispatcher and scan manager."""
        # Must happen after root is created but before dashboard
        self.ui_dispatcher = UIDispatcher(self.root)
        self.scan_manager = get_scan_manager(str(gui_dir), ui_dispatcher=self.ui_dispatcher)

    def _center_window(self) -> None:
        """
        Center the main window on screen using fixed dimensions.
        
        Forces window to maintain intended 800x250 size instead of
        auto-sizing based on content. This ensures consistent compact
        layout across different screen configurations.
        
        Design Decision: Fixed dimensions prevent tkinter's automatic
        content-based sizing from overriding our intended compact layout.
        """
        # Force our intended dimensions instead of querying auto-sized dimensions
        # This prevents tkinter from expanding the window based on content
        target_width = 800   # Intended width for dashboard
        target_height = 250  # Intended height with expanded text progress area
        
        # Calculate center position based on intended dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (target_width // 2)
        y = (screen_height // 2) - (target_height // 2)
        
        # Force our intended dimensions and center position
        self.root.geometry(f"{target_width}x{target_height}+{x}+{y}")
    
    def _enforce_window_size(self) -> None:
        """
        Enforce minimum window size constraints while respecting user preferences.
        
        This method ensures the window doesn't shrink below minimum requirements
        but allows users to manually resize larger without forcing back to defaults.
        Implements industry-standard UX behavior for window management.
        """
        min_width = 800
        min_height = 240
        
        # Get current geometry
        current_geometry = self.root.geometry()
        if 'x' in current_geometry and '+' in current_geometry:
            # Parse current dimensions and position
            size_part = current_geometry.split('+')[0]
            pos_part = current_geometry[len(size_part):]
            
            current_width, current_height = map(int, size_part.split('x'))
            
            # Only enforce minimum constraints - respect user's larger choices
            needs_adjustment = False
            new_width = current_width
            new_height = current_height
            
            if current_width < min_width:
                new_width = min_width
                needs_adjustment = True
                
            if current_height < min_height:
                new_height = min_height
                needs_adjustment = True
            
            # Only adjust if window is below minimum - preserve user's larger sizing
            if needs_adjustment:
                self.root.geometry(f"{new_width}x{new_height}{pos_part}")
        else:
            # Fallback: ensure minimum size without forcing position
            self.root.minsize(min_width, min_height)
    
    def _create_dashboard(self) -> None:
        """Create main dashboard widget."""
        self.dashboard = DashboardWidget(
            self.root,
            self.db_reader,
            self.backend_interface
        )
        
        # Set callbacks
        self.dashboard.set_drill_down_callback(self._open_drill_down_window)
        self.dashboard.set_config_editor_callback(self._open_config_editor_direct)
        self.dashboard.set_size_enforcement_callback(self._enforce_window_size)
    
    def _setup_event_handlers(self) -> None:
        """Setup application-wide event handlers."""
        # Keyboard shortcuts
        self.root.bind("<Control-q>", lambda e: self._on_closing())
        self.root.bind("<F5>", lambda e: self._refresh_dashboard())
        self.root.bind("<Control-r>", lambda e: self._refresh_dashboard())
        self.root.bind("<Control-i>", lambda e: self._open_drill_down_window("data_import", {}))
        self.root.bind("<F1>", lambda e: self._toggle_interface_mode())
    
    def _enable_mock_mode(self) -> None:
        """Enable mock mode for testing."""
        self.db_reader.enable_mock_mode()
        self.backend_interface.enable_mock_mode()
        self.dashboard.enable_mock_mode()
        
        # Update window title to indicate mock mode
        self.root.title("SMBSeek Security Toolkit (Mock Mode)")
    
    def _handle_initialization_error(self, error: Exception) -> None:
        """Handle application initialization errors."""
        error_message = f"Failed to initialize SMBSeek GUI: {error}"
        
        # Try to show error in GUI if possible
        try:
            root = tk.Tk()
            root.withdraw()  # Hide main window
            messagebox.showerror("Initialization Error", error_message)
            root.destroy()
        except:
            # Fall back to console output
            print(f"ERROR: {error_message}")
        
        sys.exit(1)
    
    def _open_drill_down_window(self, window_type: str, data: Dict[str, Any]) -> None:
        """
        Open drill-down window for detailed analysis.

        Args:
            window_type: Type of window to open
            data: Data to pass to the window
        """
        try:
            if window_type == "server_list":
                # Check if server list window already exists
                existing_window = self.drill_down_windows.get('server_list')

                if existing_window and existing_window.window and existing_window.window.winfo_exists():
                    # Reuse existing window: restore and focus
                    existing_window.restore_and_focus()
                else:
                    # Create new window and track it
                    window = open_server_list_window(self.root, self.db_reader, data, self.settings_manager)
                    self.drill_down_windows['server_list'] = window
            elif window_type == "config_editor":
                # Open configuration editor window
                config_path = self.config_path or "../backend/conf/config.json"
                open_config_editor_window(self.root, config_path)
            elif window_type == "app_config":
                # Open application configuration dialog
                open_app_config_dialog(
                    self.root, 
                    self.settings_manager,
                    self._open_config_editor_direct
                )
            elif window_type == "data_import":
                # Open data import dialog
                open_data_import_dialog(self.root, self.db_reader)
            elif window_type == "recent_activity":
                # Open server list window with recent discoveries filter
                server_window = ServerListWindow(self.root, self.db_reader, None, self.settings_manager)
                server_window.apply_recent_discoveries_filter()
            else:
                # For other window types, show placeholder message
                window_titles = {
                    "share_details": "Share Access Details", 
                    "recent_activity": "Recent Activity Timeline",
                    "geographic_report": "Geographic Distribution",
                    "activity_timeline": "Activity Timeline",
                    "config_editor": "Configuration Editor",
                    "data_import": "Data Import",
                }
                
                title = window_titles.get(window_type, "Detail Window")
                
                messagebox.showinfo(
                    title,
                    f"Drill-down window '{title}' will be implemented in upcoming phases.\n\n"
                    f"This would show detailed information for: {window_type}"
                )
        except Exception as e:
            messagebox.showerror(
                "Window Error",
                f"Failed to open {window_type} window:\n{str(e)}"
            )
    
    def _open_config_editor_direct(self, config_path: str) -> None:
        """
        Open configuration editor directly with specified path.
        
        Args:
            config_path: Path to configuration file to edit
        """
        try:
            open_config_editor_window(self.root, config_path)
        except Exception as e:
            messagebox.showerror(
                "Configuration Editor Error",
                f"Failed to open configuration editor:\n{str(e)}"
            )
    
    def _refresh_dashboard(self) -> None:
        """Manually refresh dashboard data."""
        if self.dashboard:
            self.dashboard._refresh_dashboard_data()

    def _toggle_interface_mode(self) -> None:
        """Toggle between simple and advanced interface modes."""
        new_mode = self.settings_manager.toggle_interface_mode()
        
        # Show notification of mode change
        mode_name = "Advanced" if new_mode == "advanced" else "Simple"
        messagebox.showinfo(
            "Interface Mode Changed",
            f"Interface mode switched to {mode_name} Mode.\n\n"
            "New windows will open in the selected mode.\n"
            "Press F1 to toggle modes."
        )
        
        # Update window title to show current mode
        current_title = self.root.title()
        if " - " in current_title:
            base_title = current_title.split(" - ")[0]
        else:
            base_title = current_title
        
        if new_mode == "advanced":
            self.root.title(f"{base_title} - Advanced Mode")
        else:
            self.root.title(base_title)
    
    def _on_closing(self) -> None:
        """Handle application closing."""
        # Check for active scans via scan_manager
        if self.scan_manager and self.scan_manager.is_scanning:
            response = messagebox.askyesno(
                "Scan in Progress",
                "A scan is currently running. Are you sure you want to exit?",
                icon="warning"
            )
            if not response:
                return
            # User confirmed exit during scan - interrupt it
            self.scan_manager.interrupt_scan()

        # Clean up and exit
        try:
            # Close any open drill-down windows
            for window in self.drill_down_windows.values():
                try:
                    window.destroy()
                except:
                    pass

            # Clean up backend interfaces
            if self.db_reader:
                self.db_reader.clear_cache()

            # Stop UI dispatcher before destroying root to prevent TclError
            if self.ui_dispatcher:
                self.ui_dispatcher.stop()

        except Exception as e:
            print(f"Cleanup error: {e}")
        finally:
            self.root.destroy()
    
    def run(self) -> None:
        """Start the GUI application main loop."""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._on_closing()
        except Exception as e:
            messagebox.showerror("Application Error", f"Unexpected error: {e}")
            self._on_closing()


def main():
    """Main entry point for SMBSeek GUI (deprecated)."""
    print("Warning: gui/main.py is deprecated. Use ./xsmbseek instead.", file=sys.stderr)

    parser = argparse.ArgumentParser(
        description="SMBSeek GUI - Graphical interface for SMBSeek security toolkit"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode with test data (for development/testing)"
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration file (default: ../backend/conf/config.json)"
    )
    parser.add_argument(
        "--backend-path",
        type=str,
        help="Path to backend directory (default: ../backend)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version="SMBSeek GUI 1.0.0"
    )
    
    args = parser.parse_args()
    
    try:
        app = SMBSeekGUI(
            mock_mode=args.mock,
            config_path=args.config,
            backend_path=getattr(args, 'backend_path', None)
        )
        app.run()
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
