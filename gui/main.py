#!/usr/bin/env python3
"""
Dirracuda - Legacy Entry Point

DEPRECATED: This module is maintained for backward compatibility only.
Prefer ./dirracuda as the supported GUI entry point.

This module provides SMBSeekGUI for backward compatibility but delegates
all scan operations to the unified ScanManager path established in dirracuda.
Direct invocation via `python gui/main.py` is deprecated.

Usage:
    ./dirracuda [--mock]                   # Preferred
    python gui/main.py [--mock] [--config] # Legacy (deprecated)
"""

import tkinter as tk
from tkinter import ttk
from gui.utils import safe_messagebox as messagebox
import argparse
import sys
import os
import time
import threading
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
from shared.tmpfs_quarantine import (
    bootstrap_tmpfs_quarantine,
    cleanup_tmpfs_quarantine,
    consume_tmpfs_startup_warning,
    get_tmpfs_runtime_state,
    tmpfs_has_quarantined_files,
)
from gui.utils.database_access import DatabaseReader
from gui.utils.backend_interface import BackendInterface
from gui.utils.style import get_theme, apply_theme_to_window
from gui.utils.settings_manager import get_settings_manager
from gui.utils.ui_dispatcher import UIDispatcher
from gui.utils.scan_manager import get_scan_manager
from gui.utils.db_unification import (
    apply_probe_cleanup_choice,
    run_startup_db_unification,
)


class SMBSeekGUI:
    """
    Main Dirracuda application.
    
    Coordinates between dashboard, backend interface, and drill-down windows.
    Handles application lifecycle, error recovery, and user interactions.
    
    Design Pattern: Main controller that orchestrates all GUI components
    while maintaining separation of concerns through dependency injection.
    """
    
    def __init__(self, mock_mode: bool = False, config_path: Optional[str] = None, backend_path: Optional[str] = None):
        """
        Initialize Dirracuda application.
        
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
        self._pending_tmpfs_startup_warning: Optional[str] = None
        self._db_unification_running = False
        self._pending_db_unification_error: Optional[str] = None

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
            self._bootstrap_tmpfs_runtime()
            self._setup_scan_manager()
            self._create_dashboard()
            self._start_db_unification_tasks()
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
                    "Dirracuda backend is not accessible. Would you like to continue in mock mode for testing?",
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
        self.root.title("Dirracuda")
        self.root.geometry("700x250")
        
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
        Center the main window on screen using current tuning defaults.
        """
        target_width = 700
        target_height = 250
        
        # Calculate center position based on intended dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (target_width // 2)
        y = (screen_height // 2) - (target_height // 2)
        
        # Set initial geometry and centered position.
        self.root.geometry(f"{target_width}x{target_height}+{x}+{y}")
    
    def _enforce_window_size(self) -> None:
        """
        Temporary no-op while dashboard sizing is being tuned.

        We intentionally do not enforce minimum dimensions so the user can
        freely drag/resize the window during layout calibration.
        """
        return
    
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
        self.dashboard.set_server_list_getter(
            lambda: self.drill_down_windows.get('server_list')
        )
    
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
        self.root.title("Dirracuda (Mock Mode)")
    
    def _handle_initialization_error(self, error: Exception) -> None:
        """Handle application initialization errors."""
        error_message = f"Failed to initialize Dirracuda: {error}"
        
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

    def _start_db_unification_tasks(self) -> None:
        """Run startup DB unification in background (legacy entrypoint parity)."""
        if self.mock_mode:
            return
        if self._db_unification_running:
            return
        db_path = str(getattr(self.db_reader, "db_path", "") or "").strip()
        if not db_path or self.root is None:
            return
        self._db_unification_running = True
        worker = threading.Thread(
            target=self._run_db_unification_worker,
            args=(db_path,),
            daemon=True,
            name="db-unification-startup",
        )
        worker.start()

    def _run_db_unification_worker(self, db_path: str) -> None:
        try:
            result = run_startup_db_unification(db_path)
        except Exception as exc:
            result = {
                "success": False,
                "errors": [str(exc)],
                "probe_backfill": {},
                "sidecar_import": {},
                "prompt_cleanup": False,
            }
        if self.root is None:
            return
        try:
            self.root.after(0, self._handle_db_unification_result, result)
        except tk.TclError:
            self._db_unification_running = False

    def _handle_db_unification_result(self, result: Dict[str, Any]) -> None:
        self._db_unification_running = False
        if self.root is None or not self.root.winfo_exists():
            return

        try:
            if result.get("prompt_cleanup"):
                keep_files = messagebox.askyesno(
                    "Probe Cache Cleanup",
                    "Legacy probe cache files were imported into dirracuda.db.\n\n"
                    "Keep old local cache files for safety?\n\n"
                    "Yes = keep files.\n"
                    "No = discard old cache files now.",
                    icon="question",
                    default=messagebox.YES,
                    parent=self.root,
                )
                apply_probe_cleanup_choice(self.db_reader, keep_files=bool(keep_files))
        except Exception:
            pass

        if result.get("success"):
            return

        error_text = "; ".join(str(e) for e in (result.get("errors") or []) if e) or "Unknown startup migration failure."
        self._pending_db_unification_error = error_text
        try:
            if self.dashboard and hasattr(self.dashboard, "_show_status_bar"):
                self.dashboard._show_status_bar(
                    "DB unification warning: startup migration failed. Retry available."
                )
        except Exception:
            pass
        try:
            retry = messagebox.askretrycancel(
                "Startup Data Migration Warning",
                "Dirracuda could not complete startup data migration.\n\n"
                f"Details: {error_text}\n\n"
                "You can continue using the app. Retry now?",
                icon="warning",
                parent=self.root,
            )
        except Exception:
            retry = False
        if retry:
            self._start_db_unification_tasks()

    def _bootstrap_tmpfs_runtime(self) -> None:
        """Initialize tmpfs quarantine runtime and show one-time fallback warning."""
        try:
            state = bootstrap_tmpfs_quarantine(config_path=self.config_path)
            print(
                "tmpfs bootstrap:",
                {
                    "use_tmpfs": state.get("use_tmpfs"),
                    "tmpfs_active": state.get("tmpfs_active"),
                    "effective_root": state.get("effective_root"),
                    "fallback_reason": state.get("fallback_reason"),
                },
            )
            warning = consume_tmpfs_startup_warning()
            if warning:
                self._pending_tmpfs_startup_warning = warning
                self._schedule_tmpfs_startup_warning_dialog()
        except Exception as exc:
            print(f"tmpfs bootstrap warning: {exc}")

    def _schedule_tmpfs_startup_warning_dialog(self) -> None:
        """Queue tmpfs fallback warning for idle time after root is alive."""
        if not self._pending_tmpfs_startup_warning or self.root is None:
            return
        try:
            if not self.root.winfo_exists():
                return
            self.root.after_idle(self._show_pending_tmpfs_startup_warning)
        except tk.TclError:
            return

    def _show_pending_tmpfs_startup_warning(self) -> None:
        """Show queued tmpfs warning only when window is mapped and valid."""
        warning = self._pending_tmpfs_startup_warning
        if not warning or self.root is None:
            return
        try:
            if not self.root.winfo_exists():
                self._pending_tmpfs_startup_warning = None
                return
            if not self.root.winfo_ismapped():
                self.root.after(50, self._show_pending_tmpfs_startup_warning)
                return
            self._pending_tmpfs_startup_warning = None
            messagebox.showwarning(
                "tmpfs Quarantine Fallback",
                warning,
                parent=self.root,
            )
        except tk.TclError:
            self._pending_tmpfs_startup_warning = None

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
        self._pending_tmpfs_startup_warning = None
        dashboard = self.dashboard
        has_active_work = False
        try:
            if dashboard and hasattr(dashboard, "has_active_or_queued_work"):
                has_active_work = bool(dashboard.has_active_or_queued_work())
            elif self.scan_manager and self.scan_manager.is_scanning:
                has_active_work = True
        except Exception:
            has_active_work = bool(self.scan_manager and self.scan_manager.is_scanning)

        if has_active_work:
            response = messagebox.askyesno(
                "Tasks in Progress",
                "A scan is running or scans/tasks are queued.\n\n"
                "Stop all running and queued tasks and exit?",
                icon="warning",
                parent=self.root,
            )
            if not response:
                return

            # Graceful cancel request.
            try:
                if dashboard and hasattr(dashboard, "request_cancel_active_or_queued_work"):
                    dashboard.request_cancel_active_or_queued_work()
                elif self.scan_manager and self.scan_manager.is_scanning:
                    self.scan_manager.interrupt_scan()
            except Exception:
                pass

            # Wait briefly for cancellation, then retry once with forceful termination.
            start = time.time()
            retried = False
            while (time.time() - start) < 6.0:
                try:
                    if self.root and self.root.winfo_exists():
                        self.root.update_idletasks()
                        self.root.update()
                except tk.TclError:
                    break

                try:
                    if dashboard and hasattr(dashboard, "has_active_or_queued_work"):
                        still_active = bool(dashboard.has_active_or_queued_work())
                    else:
                        still_active = bool(self.scan_manager and self.scan_manager.is_scanning)
                except Exception:
                    still_active = bool(self.scan_manager and self.scan_manager.is_scanning)

                if not still_active:
                    break

                elapsed = time.time() - start
                if (not retried) and elapsed >= 3.0:
                    retried = True
                    try:
                        if dashboard and hasattr(dashboard, "request_cancel_active_or_queued_work"):
                            dashboard.request_cancel_active_or_queued_work()
                        if dashboard and hasattr(dashboard, "force_terminate_active_work"):
                            dashboard.force_terminate_active_work()
                        elif self.scan_manager and self.scan_manager.is_scanning:
                            self.scan_manager.interrupt_scan()
                    except Exception:
                        pass
                time.sleep(0.10)

            # Emergency path: force terminate without additional prompt.
            try:
                if dashboard and hasattr(dashboard, "has_active_or_queued_work"):
                    if dashboard.has_active_or_queued_work() and hasattr(dashboard, "force_terminate_active_work"):
                        dashboard.force_terminate_active_work()
            except Exception:
                pass

        try:
            state = get_tmpfs_runtime_state()
            if state.get("tmpfs_active") and tmpfs_has_quarantined_files():
                proceed = messagebox.askyesno(
                    "In-Memory Quarantine Will Be Lost",
                    "There are quarantined files stored in memory (tmpfs).\n\n"
                    "Closing now will permanently delete them.\n\n"
                    "Do you want to continue?",
                    icon="warning",
                    parent=self.root,
                )
                if not proceed:
                    return
        except Exception as exc:
            print(f"tmpfs close-warning check failed: {exc}")

        # Clean up and exit
        try:
            # Close any open drill-down windows
            for window in self.drill_down_windows.values():
                try:
                    window.destroy()
                except:
                    pass

            if dashboard and hasattr(dashboard, "teardown_dashboard_monitors"):
                try:
                    dashboard.teardown_dashboard_monitors()
                except Exception:
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
            try:
                cleanup_result = cleanup_tmpfs_quarantine()
                if not cleanup_result.get("ok", False):
                    print(f"tmpfs cleanup warning: {cleanup_result.get('message')}")
            except Exception as exc:
                print(f"tmpfs cleanup exception: {exc}")
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
    print("Warning: gui/main.py is deprecated. Use ./dirracuda instead.", file=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Dirracuda"
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
        version="Dirracuda 1.0.0"
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
