"""
Batch Extract Settings Dialog

Modal dialog for configuring batch file extraction from SMB servers.
Supports two modes:
- "preflight": Used during scan workflow setup
- "on-demand": Used for extracting from server list selections

Refactored (Slice 4B): ExtensionEditorDialog extracted to
gui/components/extension_editor_dialog.py; pure logic extracted to
gui/utils/batch_extract_helpers.py.  Both are re-exported below for
backward compatibility.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.batch_extract_helpers import (
    NO_EXTENSION_TOKEN,
    load_extension_filters,
)
from gui.components.extension_editor_dialog import ExtensionEditorDialog  # re-export


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

        # Apply theme styling to heading only
        if self.theme:
            self.theme.apply_to_widget(mode_heading, "label")

        row += 1

        # Backend status note (italic, small font)
        status_note = tk.Label(
            parent,
            text="Extension mode now applies during extract. In allow-only, add '<no extension>' to permit extensionless files.",
            font=("TkDefaultFont", 8, "italic"),
            fg=self.theme.colors["text_secondary"] if self.theme else "gray"
        )
        status_note.grid(row=row, column=0, columnspan=2, sticky="w", padx=(10, 0), pady=(0, 10))
        row += 1

        # Summary counts label (moved below radio buttons and note)
        count_text = f"Included: {allowed_count} allowed    Excluded: {denied_count} denied"
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
        """Load extension filters from config.json (normalized, with token ensured)."""
        return load_extension_filters(self.config_path, normalize=True)

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
            count_text = f"Included: {allowed_count} allowed    Excluded: {denied_count} denied"
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
