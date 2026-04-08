"""
Batch Extract Settings Dialog

Unified dialog for configuring batch file extraction from SMB servers.
Supports two modes:
- "preflight": Used during scan workflow setup
- "on-demand": Used for extracting from server list selections

Design Decision: Single dialog class reduces code duplication and ensures
consistent extension filter display across both workflows.
"""

import tkinter as tk
from tkinter import filedialog, simpledialog
from gui.utils import safe_messagebox as messagebox
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
import sys
import os

from gui.utils.dialog_helpers import ensure_dialog_focus

# Special display token for extensionless files
NO_EXTENSION_TOKEN = "<no extension>"


class BatchExtractSettingsDialog:
    """
    Modal dialog for batch extract settings configuration.

    Supports two operational modes with different button layouts and return values:
    - preflight: Scan workflow (Save & Continue / Disable / Abort buttons)
    - on-demand: Server list extraction (Start / Cancel buttons)

    Both modes display extension filter info and provide access to config editor.
    """

    def __init__(
        self,
        parent: tk.Toplevel,
        theme,
        settings_manager,
        config_path: str,
        mode: str = "on-demand",
        target_count: Optional[int] = None,
        config_editor_callback: Optional[callable] = None
    ):
        """
        Initialize batch extract settings dialog.

        Args:
            parent: Parent window
            theme: Theme object for styling
            settings_manager: Settings manager instance
            config_path: Path to config.json
            mode: "preflight" or "on-demand"
            target_count: Number of targets (optional, for on-demand mode)
            config_editor_callback: Callback to open config editor (optional)
        """
        self.parent = parent
        self.theme = theme
        self.settings = settings_manager
        resolved_config = None
        if config_path:
            resolved_config = Path(config_path).expanduser().resolve()
        elif self.settings:
            try:
                cfg = self.settings.get_setting('backend.config_path', '')
                if cfg:
                    resolved_config = Path(cfg).expanduser().resolve()
            except Exception:
                resolved_config = None
        if resolved_config is None:
            resolved_config = (Path.cwd() / "conf" / "config.json").resolve()
        self.config_path = resolved_config
        self.mode = mode
        self.target_count = target_count
        self.config_editor_callback = config_editor_callback
        self.dialog: Optional[tk.Toplevel] = None
        self.result: Optional[Dict[str, Any]] = None

        # Load defaults from settings and config
        self._load_defaults()

    def _load_defaults(self):
        """Load default values from settings manager and config file."""
        # Basic defaults
        defaults = {
            "workers": 2,
            "path": str(Path.home()),
            "max_file": 50,
            "max_total": 200,
            "max_time": 300,
            "max_files": 10,
            "max_directory_depth": 3,
            "download_delay_seconds": 0.5,
            "connection_timeout": 30
        }

        # Load from settings manager
        if self.settings:
            try:
                defaults["workers"] = int(self.settings.get_setting('extract.batch_max_workers', defaults['workers']))
                defaults["path"] = str(self.settings.get_setting('extract.last_directory', defaults['path']))
                defaults["max_file"] = int(self.settings.get_setting('extract.max_file_size_mb', defaults['max_file']))
                defaults["max_total"] = int(self.settings.get_setting('extract.max_total_size_mb', defaults['max_total']))
                defaults["max_time"] = int(self.settings.get_setting('extract.max_time_seconds', defaults['max_time']))
                defaults["max_files"] = int(self.settings.get_setting('extract.max_files_per_target', defaults['max_files']))
            except Exception:
                pass

        # Load extension mode
        defaults["extension_mode"] = "allow_only"  # Default
        if self.settings:
            try:
                defaults["extension_mode"] = str(self.settings.get_setting(
                    'extract.extension_mode',
                    defaults['extension_mode']
                ))
            except Exception:
                pass

        # Validate mode (fallback to allow_only if invalid)
        if defaults["extension_mode"] not in ("download_all", "allow_only", "deny_only"):
            defaults["extension_mode"] = "allow_only"

        # Load from config file for additional settings
        if self.config_path and self.config_path.exists():
            try:
                config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                defaults["max_directory_depth"] = int(file_cfg.get("max_directory_depth", defaults["max_directory_depth"]))
                defaults["download_delay_seconds"] = float(file_cfg.get("download_delay_seconds", defaults["download_delay_seconds"]))
                defaults["connection_timeout"] = int(file_cfg.get("enumeration_timeout_seconds", defaults["connection_timeout"]))
            except Exception:
                pass
        else:
            # Ensure config file exists with baseline structure
            try:
                base_cfg = {"file_collection": {}}
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(json.dumps(base_cfg, indent=2), encoding="utf-8")
            except Exception:
                pass

        # Create tkinter variables
        self.worker_var = tk.IntVar(value=defaults['workers'])
        self.path_var = tk.StringVar(value=defaults['path'])
        self.max_file_var = tk.IntVar(value=defaults['max_file'])
        self.max_total_var = tk.IntVar(value=defaults['max_total'])
        self.max_time_var = tk.IntVar(value=defaults['max_time'])
        self.max_files_var = tk.IntVar(value=defaults['max_files'])

        # Create mode variable
        self.extension_mode_var = tk.StringVar(value=defaults['extension_mode'])

        # Store for on-demand mode return value
        self.max_directory_depth = defaults["max_directory_depth"]
        self.download_delay_seconds = defaults["download_delay_seconds"]
        self.connection_timeout = defaults["connection_timeout"]

    def show(self) -> Optional[Dict[str, Any]]:
        """
        Display dialog and return result.

        Returns:
            Dict with settings (format depends on mode) or None if cancelled
        """
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Batch Extract Settings")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        if self.theme:
            self.theme.apply_to_widget(self.dialog, "main_window")

        # Main container
        main_frame = tk.Frame(self.dialog)
        if self.theme:
            self.theme.apply_to_widget(main_frame, "main_window")
        main_frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        row = 0

        # Target count (on-demand mode only)
        if self.mode == "on-demand" and self.target_count is not None:
            label = tk.Label(main_frame, text=f"Targets selected: {self.target_count}")
            label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))
            row += 1

        # Create input fields
        row = self._create_fields(main_frame, row)

        # Add extension filter display
        row = self._create_extension_display(main_frame, row)

        # Create buttons based on mode
        self._create_buttons(main_frame, row)

        # Handle window close
        if self.mode == "preflight":
            self.dialog.protocol("WM_DELETE_WINDOW", self._on_abort)
        else:
            self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)

        if self.theme:
            self.theme.apply_theme_to_application(self.dialog)

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

        self.parent.wait_window(self.dialog)
        return self.result

    def _create_fields(self, parent: tk.Frame, start_row: int) -> int:
        """Create input fields. Returns next available row."""
        row = start_row

        # Worker threads
        tk.Label(parent, text="Worker threads (max 8):").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=self.worker_var, width=15).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Quarantine path
        tk.Label(parent, text="Quarantine path:").grid(row=row, column=0, sticky="w", pady=5)
        path_frame = tk.Frame(parent)
        path_frame.grid(row=row, column=1, sticky="w", pady=5)
        tk.Entry(path_frame, textvariable=self.path_var, width=30).pack(side=tk.LEFT)
        browse_btn = tk.Button(path_frame, text="Browse", command=self._browse_path)
        if self.theme:
            self.theme.apply_to_widget(browse_btn, "button_secondary")
        browse_btn.pack(side=tk.LEFT, padx=(5, 0))
        row += 1

        # Max file size
        tk.Label(parent, text="Max file size (MB):").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=self.max_file_var, width=15).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Max total size
        tk.Label(parent, text="Max total size (MB):").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=self.max_total_var, width=15).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Max run time
        tk.Label(parent, text="Max run time (seconds):").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=self.max_time_var, width=15).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Max files per host
        tk.Label(parent, text="Max files per host:").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(parent, textvariable=self.max_files_var, width=15).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        return row

    def _create_extension_display(self, parent: tk.Frame, start_row: int) -> int:
        """Create extension filter display section. Returns next available row."""
        row = start_row

        # Add separator
        separator = tk.Frame(parent, height=2, bd=1, relief=tk.SUNKEN)
        separator.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(15, 10))
        row += 1

        # Load filters
        filters = self._load_extension_filters()
        allowed_count = len(filters["included_extensions"])
        denied_count = len(filters["excluded_extensions"])

        # Build count display text
        if allowed_count == 0:
            allowed_text = "None configured"
        else:
            allowed_text = f"{allowed_count} allowed"

        if denied_count == 0:
            denied_text = "No restrictions"
        else:
            denied_text = f"{denied_count} denied"

        # Extension mode selector frame
        mode_frame = tk.Frame(parent)
        if self.theme:
            self.theme.apply_to_widget(mode_frame, "main_window")
        mode_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # Heading label
        mode_heading = tk.Label(mode_frame, text="Extensions", font=("TkDefaultFont", 9, "bold"))
        mode_heading.grid(row=0, column=0, sticky="w", pady=(0, 5))

        # Radio buttons
        radio_all = tk.Radiobutton(
            mode_frame,
            text="Download all files",
            variable=self.extension_mode_var,
            value="download_all"
        )
        radio_all.grid(row=1, column=0, sticky="w", padx=(10, 0))

        radio_allow = tk.Radiobutton(
            mode_frame,
            text="Download only allowed extensions (Allow list)",
            variable=self.extension_mode_var,
            value="allow_only"
        )
        radio_allow.grid(row=2, column=0, sticky="w", padx=(10, 0))

        radio_deny = tk.Radiobutton(
            mode_frame,
            text="Download all except excluded extensions (Deny list)",
            variable=self.extension_mode_var,
            value="deny_only"
        )
        radio_deny.grid(row=3, column=0, sticky="w", padx=(10, 0))

        # Apply theme styling to heading and radio controls
        if self.theme:
            self.theme.apply_to_widget(mode_heading, "label")
            radio_style = {
                "bg": self.theme.colors["primary_bg"],
                "fg": self.theme.colors["text"],
                "activebackground": self.theme.colors["primary_bg"],
                "activeforeground": self.theme.colors["text"],
                "highlightthickness": 0,
                "selectcolor": self.theme.colors["secondary_bg"],
            }
            if self.theme.get_mode() == "dark":
                # High-contrast fill makes selected state visible in dark mode.
                radio_style["selectcolor"] = self.theme.colors["accent"]
            for radio in (radio_all, radio_allow, radio_deny):
                radio.configure(**radio_style)

        row += 1

        # Summary counts label
        count_text = f"{allowed_count} allowed / {denied_count} denied"
        self.extension_count_label = tk.Label(parent, text=count_text, justify="left")
        self.extension_count_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(5, 5))
        row += 1

        # Button frame for side-by-side buttons
        button_frame = tk.Frame(parent)
        button_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=5)

        # Edit Filters button
        view_button = tk.Button(button_frame, text="Edit Filters", command=self._show_extension_table)
        if self.theme:
            self.theme.apply_to_widget(view_button, "button_secondary")
        view_button.pack(side=tk.LEFT, padx=(0, 5))

        row += 1

        # Add separator
        separator2 = tk.Frame(parent, height=2, bd=1, relief=tk.SUNKEN)
        separator2.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 15))
        row += 1

        return row

    def _load_extension_filters(self) -> Dict[str, List[str]]:
        """Load extension filters from config.json."""
        defaults = {
            "included_extensions": [],
            "excluded_extensions": []
        }

        if self.config_path and self.config_path.exists():
            try:
                config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                defaults["included_extensions"] = self._normalize_loaded_extensions(file_cfg.get("included_extensions", []))
                defaults["excluded_extensions"] = self._normalize_loaded_extensions(file_cfg.get("excluded_extensions", []))
            except Exception:
                pass  # Use defaults on any error

        # Ensure we always have the no-extension token available
        included_lower = [e.lower() for e in defaults["included_extensions"]]
        excluded_lower = [e.lower() for e in defaults["excluded_extensions"]]
        if NO_EXTENSION_TOKEN not in included_lower and NO_EXTENSION_TOKEN not in excluded_lower:
            defaults["included_extensions"].insert(0, NO_EXTENSION_TOKEN)

        return defaults

    @staticmethod
    def _normalize_loaded_extensions(values: List[Any]) -> List[str]:
        """Clean extensions loaded from config (dedupe, lowercase, map blanks to token)."""
        cleaned: List[str] = []
        seen = set()
        for value in values or []:
            if not isinstance(value, str):
                continue
            normalized = value.strip()
            if not normalized:
                normalized = NO_EXTENSION_TOKEN
            normalized = normalized.lower()
            key = normalized
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    def _validate_extension_mode(self, mode: str, filters: Dict[str, List[str]]) -> bool:
        """
        Validate extension mode against current filter configuration.
        Shows warning dialog if configuration may produce unexpected results.

        NOTE: These warnings are "future-ready" - they describe behavior once
        the extract runner is updated to honor extension_mode. Until then,
        the runner uses its current logic (deny-first, then allow).

        Args:
            mode: Selected extension mode
            filters: Current extension filter configuration

        Returns:
            True to proceed, False if user cancelled after warning
        """
        included_count = len(filters["included_extensions"])
        excluded_count = len(filters["excluded_extensions"])

        # Check for empty allow list in allow_only mode
        if mode == "allow_only" and included_count == 0:
            # Use askokcancel which returns True/False
            result = messagebox.askokcancel(
                "Empty Allow List",
                "No allowed extensions configured.\n\n"
                "With allow-only selected, nothing will be downloaded until at least one extension (or '<no extension>') is allowed.\n\n"
                "Click OK to save anyway, or Cancel to review settings.",
                parent=self.dialog,
                icon=messagebox.WARNING
            )
            return result  # True = OK, False = Cancel

        # Check for empty deny list in deny_only mode
        if mode == "deny_only" and excluded_count == 0:
            result = messagebox.askokcancel(
                "Empty Deny List",
                "No excluded extensions configured.\n\n"
                "Deny-only will download everything when the deny list is empty.\n\n"
                "Click OK to save anyway, or Cancel to review settings.",
                parent=self.dialog,
                icon=messagebox.WARNING
            )
            return result  # True = OK, False = Cancel

        return True

    def _show_extension_table(self):
        """Launch extension editor dialog."""
        # Load current filters
        filters = self._load_extension_filters()

        # Launch editor dialog
        editor = ExtensionEditorDialog(
            parent=self.dialog,
            theme=self.theme,
            config_path=self.config_path,
            initial_included=filters["included_extensions"],
            initial_excluded=filters["excluded_extensions"]
        )

        result = editor.show()

        # If user saved changes, update the summary label
        if result is not None:
            included, excluded = result

            # Update summary count with new format
            allowed_count = len(included)
            denied_count = len(excluded)
            count_text = f"{allowed_count} allowed / {denied_count} denied"
            self.extension_count_label.config(text=count_text)

    def _open_config_editor(self):
        """Open configuration editor."""
        if self.config_editor_callback and self.config_path:
            try:
                self.config_editor_callback(str(self.config_path))
            except Exception as e:
                messagebox.showerror(
                    "Configuration Editor Error",
                    f"Failed to open configuration editor:\n{str(e)}",
                    parent=self.dialog
                )

    def _create_buttons(self, parent: tk.Frame, start_row: int):
        """Create buttons based on mode."""
        button_frame = tk.Frame(parent)
        button_frame.grid(row=start_row, column=0, columnspan=2, pady=(15, 0))

        if self.mode == "preflight":
            # Preflight mode: Save & Continue / Disable Extract / Abort Scan
            save_btn = tk.Button(button_frame, text="Save & Continue", command=self._on_save)
            disable_btn = tk.Button(button_frame, text="Disable Extract", command=self._on_disable)
            abort_btn = tk.Button(button_frame, text="Abort Scan", command=self._on_abort)

            for btn in (save_btn, disable_btn, abort_btn):
                if self.theme:
                    self.theme.apply_to_widget(btn, "button_secondary")
                btn.pack(side=tk.LEFT, padx=5)
        else:
            # On-demand mode: Cancel / Start
            cancel_btn = tk.Button(button_frame, text="Cancel", command=self._on_cancel)
            start_btn = tk.Button(button_frame, text="Start", command=self._on_start)

            for btn in (cancel_btn, start_btn):
                if self.theme:
                    self.theme.apply_to_widget(btn, "button_secondary")

            start_btn.pack(side=tk.RIGHT, padx=5)
            cancel_btn.pack(side=tk.RIGHT)

    def _browse_path(self):
        """Open file dialog to select quarantine path."""
        selection = filedialog.askdirectory(parent=self.dialog, title="Select Quarantine Path")
        if selection:
            self.path_var.set(selection)

    def _validate_inputs(self) -> Optional[Dict[str, Any]]:
        """Validate input fields and return sanitized values or None on error."""
        try:
            workers = max(1, min(8, int(self.worker_var.get())))
            path = self.path_var.get().strip() or str(Path.home())
            max_file = max(1, int(self.max_file_var.get()))
            max_total = max(1, int(self.max_total_var.get()))
            max_time = max(30, int(self.max_time_var.get()))
            max_files = max(1, int(self.max_files_var.get()))

            return {
                "workers": workers,
                "path": path,
                "max_file": max_file,
                "max_total": max_total,
                "max_time": max_time,
                "max_files": max_files
            }
        except (ValueError, tk.TclError):
            messagebox.showerror(
                "Invalid Input",
                "Please enter numeric values for extraction limits.",
                parent=self.dialog
            )
            return None

    def _save_settings(self, values: Dict[str, Any]):
        """Save settings to settings manager."""
        if self.settings:
            try:
                self.settings.set_setting('extract.batch_max_workers', values['workers'])
                self.settings.set_setting('extract.last_directory', values['path'])
                self.settings.set_setting('extract.max_file_size_mb', values['max_file'])
                self.settings.set_setting('extract.max_total_size_mb', values['max_total'])
                self.settings.set_setting('extract.max_time_seconds', values['max_time'])
                self.settings.set_setting('extract.max_files_per_target', values['max_files'])
                self.settings.set_setting('extract.extension_mode', self.extension_mode_var.get())
            except Exception:
                pass

    def _on_save(self):
        """Handle Save & Continue button (preflight mode)."""
        values = self._validate_inputs()
        if values is None:
            return

        # Validate extension mode
        filters = self._load_extension_filters()
        if not self._validate_extension_mode(self.extension_mode_var.get(), filters):
            return  # User cancelled after warning

        self._save_settings(values)

        self.result = {
            "status": "ok",
            "workers": values["workers"],
            "path": values["path"],
            "max_file": values["max_file"],
            "max_total": values["max_total"],
            "max_time": values["max_time"],
            "max_files": values["max_files"],
            "extension_mode": self.extension_mode_var.get()
        }
        self.dialog.destroy()

    def _on_disable(self):
        """Handle Disable Extract button (preflight mode)."""
        self.result = {"status": "disable"}
        self.dialog.destroy()

    def _on_abort(self):
        """Handle Abort Scan button (preflight mode)."""
        self.result = {"status": "abort"}
        self.dialog.destroy()

    def _on_start(self):
        """Handle Start button (on-demand mode)."""
        values = self._validate_inputs()
        if values is None:
            return

        # Load extension filters for validation and return value
        filters = self._load_extension_filters()

        # Validate extension mode
        if not self._validate_extension_mode(self.extension_mode_var.get(), filters):
            return  # User cancelled after warning

        self._save_settings(values)

        self.result = {
            "worker_count": values["workers"],
            "download_path": values["path"],
            "max_file_size_mb": values["max_file"],
            "max_total_size_mb": values["max_total"],
            "max_time_seconds": values["max_time"],
            "max_files_per_target": values["max_files"],
            "max_directory_depth": self.max_directory_depth,
            "download_delay_seconds": self.download_delay_seconds,
            "included_extensions": filters["included_extensions"],
            "excluded_extensions": filters["excluded_extensions"],
            "connection_timeout": self.connection_timeout,
            "extension_mode": self.extension_mode_var.get()
        }
        self.dialog.destroy()

    def _on_cancel(self):
        """Handle Cancel button (on-demand mode)."""
        self.result = None
        self.dialog.destroy()


class ExtensionEditorDialog:
    """
    Modal dialog for editing extension filters with list-based UI.

    Provides two-pane interface for managing included and excluded extensions
    with validation, sorting, and persistence to config.json.
    """

    def __init__(
        self,
        parent: tk.Toplevel,
        theme,
        config_path: Path,
        initial_included: List[str],
        initial_excluded: List[str]
    ):
        """
        Initialize extension editor dialog.

        Args:
            parent: Parent window
            theme: Theme object for styling
            config_path: Path to config.json
            initial_included: Initial included extensions list
            initial_excluded: Initial excluded extensions list
        """
        self.parent = parent
        self.theme = theme
        self.config_path = config_path
        self.included_extensions = list(initial_included)
        self.excluded_extensions = list(initial_excluded)
        self.window: Optional[tk.Toplevel] = None
        self.result: Optional[tuple] = None

        # Ensure the no-extension token is present (default in included list)
        self._ensure_no_extension_token()

        # UI widgets (initialized in show())
        self.included_listbox: Optional[tk.Listbox] = None
        self.excluded_listbox: Optional[tk.Listbox] = None
        self.extension_count_label: Optional[tk.Label] = None

    def show(self) -> Optional[tuple]:
        """
        Display dialog and return result.

        Returns:
            Tuple of (included_list, excluded_list) or None if cancelled
        """
        self.window = tk.Toplevel(self.parent)
        self.window.title("Extension Filter Editor")
        self.window.geometry("815x690")
        self.window.transient(self.parent)
        self.window.grab_set()

        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Main container
        main_frame = tk.Frame(self.window)
        if self.theme:
            self.theme.apply_to_widget(main_frame, "main_window")
        main_frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        # Create two-column layout
        self._create_list_columns(main_frame)

        # Create control buttons
        self._create_control_buttons(main_frame)

        # Create bottom buttons
        self._create_bottom_buttons(main_frame)

        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self._on_cancel)

        if self.theme:
            self.theme.apply_theme_to_application(self.window)

        # Ensure dialog appears on top and gains focus
        ensure_dialog_focus(self.window, self.parent)

        self.parent.wait_window(self.window)
        return self.result

    def _create_list_columns(self, parent: tk.Frame):
        """Create two-column layout with listboxes."""
        # Container for both columns
        columns_frame = tk.Frame(parent)
        columns_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Left column - Included Extensions
        left_frame = tk.Frame(columns_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        included_label = tk.Label(left_frame, text="Included Extensions", font=("TkDefaultFont", 10, "bold"))
        included_label.pack(anchor="w", pady=(0, 5))

        # Listbox with scrollbar
        included_scroll_frame = tk.Frame(left_frame)
        included_scroll_frame.pack(fill=tk.BOTH, expand=True)

        included_scrollbar = tk.Scrollbar(included_scroll_frame)
        included_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.included_listbox = tk.Listbox(
            included_scroll_frame,
            height=20,
            yscrollcommand=included_scrollbar.set,
            selectmode=tk.SINGLE
        )
        self.included_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        included_scrollbar.config(command=self.included_listbox.yview)

        if self.theme:
            self.theme.apply_to_widget(self.included_listbox, "listbox")

        # Right column - Excluded Extensions
        right_frame = tk.Frame(columns_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        excluded_label = tk.Label(right_frame, text="Excluded Extensions", font=("TkDefaultFont", 10, "bold"))
        excluded_label.pack(anchor="w", pady=(0, 5))

        # Listbox with scrollbar
        excluded_scroll_frame = tk.Frame(right_frame)
        excluded_scroll_frame.pack(fill=tk.BOTH, expand=True)

        excluded_scrollbar = tk.Scrollbar(excluded_scroll_frame)
        excluded_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.excluded_listbox = tk.Listbox(
            excluded_scroll_frame,
            height=20,
            yscrollcommand=excluded_scrollbar.set,
            selectmode=tk.SINGLE
        )
        self.excluded_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        excluded_scrollbar.config(command=self.excluded_listbox.yview)

        if self.theme:
            self.theme.apply_to_widget(self.excluded_listbox, "listbox")

        # Populate and sort lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

    def _create_control_buttons(self, parent: tk.Frame):
        """Create control buttons for list operations."""
        control_frame = tk.Frame(parent)
        control_frame.pack(fill=tk.X, pady=10)

        # Left side buttons (Add, Edit, Remove)
        left_buttons = tk.Frame(control_frame)
        left_buttons.pack(side=tk.LEFT)

        add_btn = tk.Button(left_buttons, text="Add", command=self._on_add, width=10)
        edit_btn = tk.Button(left_buttons, text="Edit", command=self._on_edit, width=10)
        remove_btn = tk.Button(left_buttons, text="Remove", command=self._on_remove, width=10)

        for btn in (add_btn, edit_btn, remove_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=5)

        # Right side buttons (Move operations)
        right_buttons = tk.Frame(control_frame)
        right_buttons.pack(side=tk.RIGHT)

        move_to_excluded_btn = tk.Button(
            right_buttons,
            text="→ Move to Excluded",
            command=self._on_move_to_excluded,
            width=18
        )
        move_to_included_btn = tk.Button(
            right_buttons,
            text="← Move to Included",
            command=self._on_move_to_included,
            width=18
        )

        for btn in (move_to_excluded_btn, move_to_included_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=5)

    def _create_bottom_buttons(self, parent: tk.Frame):
        """Create bottom action buttons."""
        button_frame = tk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        # Left side - Reset button
        reset_btn = tk.Button(
            button_frame,
            text="Reset to Defaults",
            command=self._on_reset,
            width=15
        )
        if self.theme:
            self.theme.apply_to_widget(reset_btn, "button_secondary")
        reset_btn.pack(side=tk.LEFT)

        # Right side - Save and Cancel buttons
        cancel_btn = tk.Button(button_frame, text="Cancel", command=self._on_cancel, width=10)
        save_btn = tk.Button(button_frame, text="Save", command=self._on_save, width=10)

        for btn in (cancel_btn, save_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_primary")

        save_btn.pack(side=tk.RIGHT, padx=(5, 0))
        cancel_btn.pack(side=tk.RIGHT)

    def _validate_extension(
        self,
        ext: str,
        source_list: List[str],
        other_list: List[str]
    ) -> tuple:
        """
        Validate extension format and uniqueness.

        Args:
            ext: Extension to validate
            source_list: Current list being edited
            other_list: The opposite list (to check cross-list conflicts)

        Returns:
            Tuple of (is_valid, normalized_ext, error_message)
        """
        # 1. Strip whitespace
        ext = ext.strip()

        # Special case: no-extension token
        if ext.lower() in (NO_EXTENSION_TOKEN, NO_EXTENSION_TOKEN.strip("<>"), "no extension"):
            token = NO_EXTENSION_TOKEN
            # Check uniqueness across lists
            if any(e.lower() == token for e in source_list):
                return (False, "", f"Entry '{token}' already exists in this list")
            if any(e.lower() == token for e in other_list):
                return (False, "", f"Entry '{token}' exists in the other list")
            return (True, token, "")

        # 2. Check not empty
        if not ext:
            return (False, "", "Extension cannot be empty")

        # 3. Enforce leading dot
        if not ext.startswith("."):
            ext = "." + ext

        # 4. Convert to lowercase
        ext = ext.lower()

        # 5. Block only dangerous characters (path separators, control chars)
        unsafe_chars = set('\\/\x00')
        if any(c in unsafe_chars or ord(c) < 32 for c in ext):
            return (False, "", "Extension cannot contain path separators or control characters")

        # 6. Check uniqueness in source list (case-insensitive)
        if any(e.lower() == ext for e in source_list):
            return (False, "", f"Extension '{ext}' already exists in this list")

        # 7. Check uniqueness in other list (case-insensitive)
        if any(e.lower() == ext for e in other_list):
            return (False, "", f"Extension '{ext}' exists in the other list")

        return (True, ext, "")

    def _sort_list(self, listbox: tk.Listbox, extensions: List[str]) -> List[str]:
        """
        Sort extensions alphabetically (case-insensitive) and update listbox.

        Args:
            listbox: Tkinter Listbox widget
            extensions: List of extension strings

        Returns:
            Sorted list
        """
        # Keep the no-extension token pinned to the top if present
        has_no_ext = any(ext.lower() == NO_EXTENSION_TOKEN for ext in extensions)
        # Remove duplicates while preserving case
        unique_exts = []
        seen = set()
        for ext in extensions:
            key = ext.lower()
            if key in seen:
                continue
            seen.add(key)
            unique_exts.append(ext)

        sorted_exts = sorted(
            [ext for ext in unique_exts if ext.lower() != NO_EXTENSION_TOKEN],
            key=str.lower
        )
        if has_no_ext:
            sorted_exts.insert(0, NO_EXTENSION_TOKEN)

        # Update the passed list in-place
        extensions.clear()
        extensions.extend(sorted_exts)

        # Update listbox
        listbox.delete(0, tk.END)
        for ext in sorted_exts:
            listbox.insert(tk.END, ext)

        return sorted_exts

    def _get_active_list(self) -> tuple:
        """
        Determine which listbox has focus and return (active_listbox, active_list, other_list).

        Returns:
            Tuple of (active_listbox, active_extensions, other_extensions)
        """
        # Check which widget has focus
        focused = self.window.focus_get()

        if focused == self.included_listbox:
            return (self.included_listbox, self.included_extensions, self.excluded_extensions)
        elif focused == self.excluded_listbox:
            return (self.excluded_listbox, self.excluded_extensions, self.included_extensions)
        else:
            # Default to included if no focus
            return (self.included_listbox, self.included_extensions, self.excluded_extensions)

    def _ensure_no_extension_token(self) -> None:
        """Guarantee the '<no extension>' token exists in one of the lists (default: included)."""
        included_lower = [e.lower() for e in self.included_extensions]
        excluded_lower = [e.lower() for e in self.excluded_extensions]
        if NO_EXTENSION_TOKEN in included_lower or NO_EXTENSION_TOKEN in excluded_lower:
            return
        self.included_extensions.insert(0, NO_EXTENSION_TOKEN)

    def _load_extension_filters(self) -> Dict[str, List[str]]:
        """Load extension filters from config.json."""
        defaults = {
            "included_extensions": [],
            "excluded_extensions": []
        }

        if self.config_path and self.config_path.exists():
            try:
                config_data = json.loads(self.config_path.read_text(encoding="utf-8"))
                file_cfg = config_data.get("file_collection", {})
                defaults["included_extensions"] = file_cfg.get("included_extensions", [])
                defaults["excluded_extensions"] = file_cfg.get("excluded_extensions", [])
            except Exception:
                pass  # Use defaults on any error

        return defaults

    def _on_add(self):
        """Prompt for new extension and add to active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Prompt user
        ext = simpledialog.askstring(
            "Add Extension",
            "Enter file extension (e.g., .txt or txt):",
            parent=self.window
        )

        if ext is None:  # User cancelled
            return

        # Validate
        is_valid, normalized_ext, error = self._validate_extension(ext, active_list, other_list)

        if not is_valid:
            messagebox.showerror("Invalid Extension", error, parent=self.window)
            return

        # Add and sort
        active_list.append(normalized_ext)
        self._sort_list(active_listbox, active_list)

        # Select the newly added item
        idx = active_list.index(normalized_ext)
        active_listbox.selection_clear(0, tk.END)
        active_listbox.selection_set(idx)
        active_listbox.see(idx)

    def _on_edit(self):
        """Edit selected extension in active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Get selection
        selection = active_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to edit", parent=self.window)
            return

        idx = selection[0]
        current_ext = active_list[idx]

        # Prompt user
        new_ext = simpledialog.askstring(
            "Edit Extension",
            "Edit file extension:",
            initialvalue=current_ext,
            parent=self.window
        )

        if new_ext is None:  # User cancelled
            return

        # Create list without current item for validation
        temp_list = active_list[:idx] + active_list[idx+1:]

        # Validate
        is_valid, normalized_ext, error = self._validate_extension(new_ext, temp_list, other_list)

        if not is_valid:
            messagebox.showerror("Invalid Extension", error, parent=self.window)
            return

        # Update and sort
        active_list[idx] = normalized_ext
        self._sort_list(active_listbox, active_list)

        # Re-select the item at its new position
        new_idx = active_list.index(normalized_ext)
        active_listbox.selection_clear(0, tk.END)
        active_listbox.selection_set(new_idx)
        active_listbox.see(new_idx)

    def _on_remove(self):
        """Remove selected extension from active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Get selection
        selection = active_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to remove", parent=self.window)
            return

        idx = selection[0]
        ext = active_list[idx]

        # Confirm removal
        if not messagebox.askyesno(
            "Confirm Removal",
            f"Remove extension '{ext}'?",
            parent=self.window
        ):
            return

        # Remove
        active_list.pop(idx)
        active_listbox.delete(idx)

        # Select next item if available
        if active_listbox.size() > 0:
            new_idx = min(idx, active_listbox.size() - 1)
            active_listbox.selection_set(new_idx)
            active_listbox.see(new_idx)

    def _on_move_to_excluded(self):
        """Move selected extension from included to excluded."""
        selection = self.included_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to move", parent=self.window)
            return

        idx = selection[0]
        ext = self.included_extensions[idx]

        # Move
        self.included_extensions.pop(idx)
        self.excluded_extensions.append(ext)

        # Sort both lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

        # Select the moved item in excluded list
        new_idx = self.excluded_extensions.index(ext)
        self.excluded_listbox.selection_clear(0, tk.END)
        self.excluded_listbox.selection_set(new_idx)
        self.excluded_listbox.see(new_idx)
        self.excluded_listbox.focus_set()

    def _on_move_to_included(self):
        """Move selected extension from excluded to included."""
        selection = self.excluded_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to move", parent=self.window)
            return

        idx = selection[0]
        ext = self.excluded_extensions[idx]

        # Move
        self.excluded_extensions.pop(idx)
        self.included_extensions.append(ext)

        # Sort both lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

        # Select the moved item in included list
        new_idx = self.included_extensions.index(ext)
        self.included_listbox.selection_clear(0, tk.END)
        self.included_listbox.selection_set(new_idx)
        self.included_listbox.see(new_idx)
        self.included_listbox.focus_set()

    def _on_reset(self):
        """Reset to default extensions from config."""
        if not messagebox.askyesno(
            "Reset to Defaults",
            "This will reset all extensions to the defaults from config.json. Continue?",
            parent=self.window
        ):
            return

        # Reload from config
        filters = self._load_extension_filters()
        self.included_extensions = list(filters["included_extensions"])
        self.excluded_extensions = list(filters["excluded_extensions"])
        self._ensure_no_extension_token()

        # Update both listboxes
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

    def _on_save(self):
        """Save changes to config.json and close."""
        if not self.config_path or not self.config_path.exists():
            messagebox.showerror(
                "Configuration Error",
                "Cannot find config.json. Please check your configuration path.",
                parent=self.window
            )
            return

        try:
            # Load current config
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            config_data = json.loads(self.config_path.read_text(encoding="utf-8"))

            # Update file_collection section
            if "file_collection" not in config_data:
                config_data["file_collection"] = {}

            config_data["file_collection"]["included_extensions"] = self.included_extensions
            config_data["file_collection"]["excluded_extensions"] = self.excluded_extensions

            # Write back to file
            self.config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

            # Set result and close
            self.result = (self.included_extensions, self.excluded_extensions)
            self.window.destroy()

        except Exception as e:
            messagebox.showerror(
                "Save Failed",
                f"Failed to save configuration: {str(e)}",
                parent=self.window
            )

    def _on_cancel(self):
        """Close without saving."""
        self.result = None
        self.window.destroy()
