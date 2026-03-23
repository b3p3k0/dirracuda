"""
SMBSeek Application Configuration Dialog

Compact configuration dialog for managing xsmbseek integration settings.
This includes backend paths plus runtime settings that should propagate
to scan, browse, and extract workflows.
"""

import json
import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, Optional

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme


def _ensure_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get_nested(data: Dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _set_nested(data: Dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


class AppConfigDialog:
    """
    Application configuration dialog with compact, form-based UI.

    Managed settings:
    - SMBSeek installation root
    - SMBSeek config.json path
    - Database path
    - Shodan API key
    - Quarantine directory (shared SMB/FTP/HTTP browser + extract default)
    - Protocol baseline discovery dorks (SMB/FTP/HTTP)
    """

    DORK_DEFAULTS = {
        "smb_dork": "smb authentication: disabled",
        "ftp_dork": 'port:21 "230 Login successful"',
        "http_dork": 'http.title:"Index of /"',
    }
    DORK_FIELDS = ("smb_dork", "ftp_dork", "http_dork")
    REQUIRED_FIELDS = ("smbseek", "database", "config", "quarantine", "smb_dork", "ftp_dork", "http_dork")
    FIELD_LABELS = {
        "smbseek": "SMBSeek Root",
        "database": "Database File",
        "config": "SMBSeek Config",
        "api_key": "Shodan API Key",
        "quarantine": "Quarantine Directory",
        "wordlist": "Pry Wordlist Path",
        "smb_dork": "SMB Base Query",
        "ftp_dork": "FTP Base Query",
        "http_dork": "HTTP Base Query",
    }

    def __init__(
        self,
        parent: tk.Widget,
        settings_manager=None,
        config_editor_callback: Optional[Callable[[str], None]] = None,
        main_config=None,
        refresh_callback: Optional[Callable[[], None]] = None,
    ):
        self.parent = parent
        self.settings_manager = settings_manager
        self.config_editor_callback = config_editor_callback
        self.main_config = main_config
        self.refresh_callback = refresh_callback
        self.theme = get_theme()

        self.smbseek_path = ""
        self.config_path = ""
        self.database_path = ""
        self.api_key = ""
        self.quarantine_path = "~/.smbseek/quarantine"
        self.wordlist_path = ""
        self.smb_dork = self.DORK_DEFAULTS["smb_dork"]
        self.ftp_dork = self.DORK_DEFAULTS["ftp_dork"]
        self.http_dork = self.DORK_DEFAULTS["http_dork"]
        self._open_dork_values = self.DORK_DEFAULTS.copy()

        self.validation_results: Dict[str, Dict[str, Any]] = {
            "smbseek": {"valid": False, "message": ""},
            "database": {"valid": False, "message": ""},
            "config": {"valid": False, "message": ""},
            "api_key": {"valid": False, "message": ""},
            "quarantine": {"valid": False, "message": ""},
            "wordlist": {"valid": False, "message": ""},
            "smb_dork": {"valid": False, "message": ""},
            "ftp_dork": {"valid": False, "message": ""},
            "http_dork": {"valid": False, "message": ""},
        }

        self.dialog: Optional[tk.Toplevel] = None
        self.status_labels: Dict[str, tk.Label] = {}

        self.smbseek_var: Optional[tk.StringVar] = None
        self.database_var: Optional[tk.StringVar] = None
        self.config_var: Optional[tk.StringVar] = None
        self.api_key_var: Optional[tk.StringVar] = None
        self.quarantine_var: Optional[tk.StringVar] = None
        self.wordlist_var: Optional[tk.StringVar] = None
        self.smb_dork_var: Optional[tk.StringVar] = None
        self.ftp_dork_var: Optional[tk.StringVar] = None
        self.http_dork_var: Optional[tk.StringVar] = None

        self._load_current_settings()
        self._create_dialog()

    # ------------------------------------------------------------------
    # Load/init
    # ------------------------------------------------------------------

    def _load_current_settings(self) -> None:
        """Load current configuration values from app config + runtime config file."""
        if self.main_config:
            self.smbseek_path = str(self.main_config.get_smbseek_path())
            self.config_path = str(self.main_config.get_config_path())
            db_path = self.main_config.get_database_path()
            self.database_path = str(db_path) if db_path else ""
        elif self.settings_manager:
            self.smbseek_path = self.settings_manager.get_backend_path()
            self.config_path = self.settings_manager.get_setting(
                "backend.config_path",
                str(Path(self.smbseek_path) / "conf" / "config.json"),
            )
            db_path = self.settings_manager.get_setting("backend.database_path")
            if db_path and db_path != "../backend/smbseek.db":
                self.database_path = db_path
            else:
                self.database_path = str(Path(self.smbseek_path) / "smbseek.db")
        else:
            self.smbseek_path = str(Path.cwd())
            self.config_path = str(Path.cwd() / "conf" / "config.json")
            self.database_path = str(Path.cwd() / "smbseek.db")

        self._load_runtime_settings_from_config(self.config_path)
        self._capture_open_dork_values()

    def _load_runtime_settings_from_config(self, config_path: str) -> None:
        """Load runtime settings from config.json."""
        path_obj = Path(config_path).expanduser()
        if not path_obj.exists():
            return

        try:
            config_data = json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception:
            return

        self.api_key = str(_get_nested(config_data, ("shodan", "api_key"), "") or "")
        self.wordlist_path = str(_get_nested(config_data, ("pry", "wordlist_path"), "") or "")

        quarantine_candidates = [
            _get_nested(config_data, ("file_browser", "quarantine_root"), ""),
            _get_nested(config_data, ("ftp_browser", "quarantine_base"), ""),
            _get_nested(config_data, ("http_browser", "quarantine_base"), ""),
            _get_nested(config_data, ("file_collection", "quarantine_base"), ""),
        ]
        for candidate in quarantine_candidates:
            if isinstance(candidate, str) and candidate.strip():
                self.quarantine_path = candidate.strip()
                break

        self.smb_dork = str(
            _get_nested(
                config_data,
                ("shodan", "query_components", "base_query"),
                self.DORK_DEFAULTS["smb_dork"],
            )
            or self.DORK_DEFAULTS["smb_dork"]
        )
        self.ftp_dork = str(
            _get_nested(
                config_data,
                ("ftp", "shodan", "query_components", "base_query"),
                self.DORK_DEFAULTS["ftp_dork"],
            )
            or self.DORK_DEFAULTS["ftp_dork"]
        )
        self.http_dork = str(
            _get_nested(
                config_data,
                ("http", "shodan", "query_components", "base_query"),
                self.DORK_DEFAULTS["http_dork"],
            )
            or self.DORK_DEFAULTS["http_dork"]
        )

    def _capture_open_dork_values(self) -> None:
        """Snapshot dork values at dialog open for per-row reset actions."""
        self._open_dork_values = {
            "smb_dork": self.smb_dork,
            "ftp_dork": self.ftp_dork,
            "http_dork": self.http_dork,
        }

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _create_dialog(self) -> None:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("SMBSeek - Application Configuration")
        self.dialog.geometry("760x700")
        self.dialog.minsize(720, 660)
        self.theme.apply_to_widget(self.dialog, "main_window")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        self._center_window()
        self._create_header()
        self._create_sections()
        self._create_button_panel()
        self._validate_all_fields()

        self.theme.apply_theme_to_application(self.dialog)
        ensure_dialog_focus(self.dialog, self.parent)

    def _center_window(self) -> None:
        self.dialog.update_idletasks()
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = (self.dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (height // 2)
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _create_header(self) -> None:
        header = tk.Frame(self.dialog)
        self.theme.apply_to_widget(header, "main_window")
        header.pack(fill=tk.X, padx=18, pady=(16, 10))

        title = self.theme.create_styled_label(header, "Application Configuration", "heading")
        title.pack(anchor=tk.W)

        desc = self.theme.create_styled_label(
            header,
            "Set shared paths and runtime options used by scan, browse, and extract workflows.",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        desc.pack(anchor=tk.W, pady=(4, 0))

    def _create_sections(self) -> None:
        container = tk.Frame(self.dialog)
        self.theme.apply_to_widget(container, "main_window")
        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 8))

        self._create_compact_card(container, "Core Paths", ("smbseek", "database", "config"))
        self._create_compact_card(container, "Runtime Settings", ("api_key", "quarantine", "wordlist"))
        self._create_dork_card(container)

        action_row = tk.Frame(container)
        self.theme.apply_to_widget(action_row, "main_window")
        action_row.pack(fill=tk.X, padx=8, pady=(4, 0))

        edit_button = tk.Button(
            action_row,
            text="Edit SMBSeek Config...",
            command=self._open_smbseek_config_editor,
        )
        self.theme.apply_to_widget(edit_button, "button_secondary")
        edit_button.pack(anchor=tk.W)

    def _create_dork_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(parent, highlightthickness=1, bd=0)
        self.theme.apply_to_widget(card, "card")
        try:
            card.configure(highlightbackground=self.theme.colors["border"], highlightcolor=self.theme.colors["border"])
        except tk.TclError:
            pass
        card.pack(fill=tk.X, pady=(0, 10))

        heading = self.theme.create_styled_label(card, "Discovery Dorks", "body")
        heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

        for field in self.DORK_FIELDS:
            self._create_dork_row(card, field)

    def _create_compact_card(self, parent: tk.Widget, title: str, fields: tuple[str, ...]) -> None:
        card = tk.Frame(parent, highlightthickness=1, bd=0)
        self.theme.apply_to_widget(card, "card")
        try:
            card.configure(highlightbackground=self.theme.colors["border"], highlightcolor=self.theme.colors["border"])
        except tk.TclError:
            pass
        card.pack(fill=tk.X, pady=(0, 10))

        heading = self.theme.create_styled_label(card, title, "body")
        heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

        for field in fields:
            self._create_field_row(card, field)

    def _create_field_row(self, parent: tk.Widget, field: str) -> None:
        row = tk.Frame(parent)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, padx=10, pady=(0, 8))

        label = self.theme.create_styled_label(
            row,
            f"{self.FIELD_LABELS[field]}:",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        label.pack(side=tk.LEFT, padx=(0, 8))

        variable = self._field_var(field)
        show_mask = "*" if field == "api_key" else ""
        entry = tk.Entry(row, textvariable=variable, font=("Arial", 10), show=show_mask)
        self.theme.apply_to_widget(entry, "entry")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_needed = field in {"smbseek", "database", "config", "quarantine", "wordlist"}
        if browse_needed:
            browse_button = tk.Button(
                row,
                text="Browse...",
                command=lambda ft=field: self._browse_path(ft),
            )
            self.theme.apply_to_widget(browse_button, "button_secondary")
            browse_button.pack(side=tk.LEFT, padx=(0, 8))

        status_label = tk.Label(row, text="", font=("Arial", 11, "bold"), width=2)
        self.theme.apply_to_widget(status_label, "text")
        status_label.pack(side=tk.RIGHT)
        self.status_labels[field] = status_label

        variable.trace_add("write", lambda *_args, ft=field: self._validate_field(ft))

    def _create_dork_row(self, parent: tk.Widget, field: str) -> None:
        row = tk.Frame(parent)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, padx=10, pady=(0, 8))

        label = self.theme.create_styled_label(
            row,
            f"{self.FIELD_LABELS[field]}:",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        label.pack(side=tk.LEFT, padx=(0, 8))

        variable = self._field_var(field)
        entry = tk.Entry(row, textvariable=variable, font=("Arial", 10))
        self.theme.apply_to_widget(entry, "entry")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        reset_button = tk.Button(
            row,
            text="Reset",
            command=lambda ft=field: self._reset_dork_to_open(ft),
        )
        self.theme.apply_to_widget(reset_button, "button_secondary")
        reset_button.pack(side=tk.LEFT, padx=(0, 6))

        default_button = tk.Button(
            row,
            text="Default",
            command=lambda ft=field: self._set_dork_default(ft),
        )
        self.theme.apply_to_widget(default_button, "button_secondary")
        default_button.pack(side=tk.LEFT, padx=(0, 8))

        status_label = tk.Label(row, text="", font=("Arial", 11, "bold"), width=2)
        self.theme.apply_to_widget(status_label, "text")
        status_label.pack(side=tk.RIGHT)
        self.status_labels[field] = status_label

        variable.trace_add("write", lambda *_args, ft=field: self._validate_field(ft))

    def _reset_dork_to_open(self, field: str) -> None:
        variable = self._field_var(field)
        variable.set(self._open_dork_values.get(field, self.DORK_DEFAULTS[field]))

    def _set_dork_default(self, field: str) -> None:
        variable = self._field_var(field)
        variable.set(self.DORK_DEFAULTS[field])

    def _field_var(self, field: str) -> tk.StringVar:
        if field == "smbseek":
            if self.smbseek_var is None:
                self.smbseek_var = tk.StringVar(value=self.smbseek_path)
            return self.smbseek_var
        if field == "database":
            if self.database_var is None:
                self.database_var = tk.StringVar(value=self.database_path)
            return self.database_var
        if field == "config":
            if self.config_var is None:
                self.config_var = tk.StringVar(value=self.config_path)
            return self.config_var
        if field == "api_key":
            if self.api_key_var is None:
                self.api_key_var = tk.StringVar(value=self.api_key)
            return self.api_key_var
        if field == "smb_dork":
            if self.smb_dork_var is None:
                self.smb_dork_var = tk.StringVar(value=self.smb_dork)
            return self.smb_dork_var
        if field == "ftp_dork":
            if self.ftp_dork_var is None:
                self.ftp_dork_var = tk.StringVar(value=self.ftp_dork)
            return self.ftp_dork_var
        if field == "http_dork":
            if self.http_dork_var is None:
                self.http_dork_var = tk.StringVar(value=self.http_dork)
            return self.http_dork_var
        if field == "wordlist":
            if self.wordlist_var is None:
                self.wordlist_var = tk.StringVar(value=self.wordlist_path)
            return self.wordlist_var
        if self.quarantine_var is None:
            self.quarantine_var = tk.StringVar(value=self.quarantine_path)
        return self.quarantine_var

    def _create_button_panel(self) -> None:
        panel = tk.Frame(self.dialog)
        self.theme.apply_to_widget(panel, "main_window")
        panel.pack(fill=tk.X, padx=18, pady=(4, 16))

        cancel_btn = tk.Button(panel, text="Cancel", command=self._on_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.RIGHT, padx=(10, 0))

        save_btn = tk.Button(panel, text="Save", command=self._on_ok)
        self.theme.apply_to_widget(save_btn, "button_primary")
        save_btn.pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # Browse/validation
    # ------------------------------------------------------------------

    def _browse_path(self, field: str) -> None:
        initial = str(Path.cwd())
        if field == "smbseek" and self.smbseek_var:
            initial = os.path.dirname(self.smbseek_var.get()) or initial
            selected = filedialog.askdirectory(
                title="Select SMBSeek Installation Directory",
                initialdir=initial,
            )
            if selected:
                self.smbseek_var.set(selected)
            return

        if field == "database" and self.database_var:
            initial = os.path.dirname(self.database_var.get()) or initial
            selected = filedialog.askopenfilename(
                title="Select Database File",
                initialdir=initial,
                filetypes=[("SQLite files", "*.db"), ("All files", "*.*")],
            )
            if selected:
                self.database_var.set(selected)
            return

        if field == "config" and self.config_var:
            initial = os.path.dirname(self.config_var.get()) or initial
            selected = filedialog.askopenfilename(
                title="Select SMBSeek Configuration File",
                initialdir=initial,
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if selected:
                self.config_var.set(selected)
            return

        if field == "quarantine" and self.quarantine_var:
            initial = os.path.dirname(self.quarantine_var.get()) or initial
            selected = filedialog.askdirectory(
                title="Select Quarantine Directory",
                initialdir=initial,
            )
            if selected:
                self.quarantine_var.set(selected)
            return

        if field == "wordlist" and self.wordlist_var:
            initial = os.path.dirname(self.wordlist_var.get()) or initial
            selected = filedialog.askopenfilename(
                title="Select Pry Wordlist File",
                initialdir=initial,
                filetypes=[
                    ("Text files", "*.txt *.lst *.list"),
                    ("All files", "*.*"),
                ],
            )
            if selected:
                self.wordlist_var.set(selected)

    def _validate_field(self, field: str) -> None:
        if field == "smbseek":
            result = self._validate_smbseek_path(self.smbseek_var.get())
            self.validation_results["smbseek"] = result
            self._update_status_label("smbseek", result)

            if result["valid"]:
                derived_config = str(Path(self.smbseek_var.get()) / "conf" / "config.json")
                if self.config_var and (not self.config_var.get() or "conf/config.json" in self.config_var.get()):
                    self.config_var.set(derived_config)
                derived_db = str(Path(self.smbseek_var.get()) / "smbseek.db")
                if self.database_var and (not self.database_var.get() or self.database_var.get().endswith("smbseek.db")):
                    self.database_var.set(derived_db)
            return

        if field == "database":
            result = self._validate_database_path(self.database_var.get())
            self.validation_results["database"] = result
            self._update_status_label("database", result)
            return

        if field == "config":
            result = self._validate_config_path(self.config_var.get())
            self.validation_results["config"] = result
            self._update_status_label("config", result)
            return

        if field == "api_key":
            result = self._validate_api_key(self.api_key_var.get())
            self.validation_results["api_key"] = result
            self._update_status_label("api_key", result)
            return

        if field == "smb_dork":
            result = self._validate_dork_query(self.smb_dork_var.get(), self.FIELD_LABELS["smb_dork"])
            self.validation_results["smb_dork"] = result
            self._update_status_label("smb_dork", result)
            return

        if field == "ftp_dork":
            result = self._validate_dork_query(self.ftp_dork_var.get(), self.FIELD_LABELS["ftp_dork"])
            self.validation_results["ftp_dork"] = result
            self._update_status_label("ftp_dork", result)
            return

        if field == "http_dork":
            result = self._validate_dork_query(self.http_dork_var.get(), self.FIELD_LABELS["http_dork"])
            self.validation_results["http_dork"] = result
            self._update_status_label("http_dork", result)
            return

        if field == "wordlist":
            result = self._validate_wordlist_path(self.wordlist_var.get())
            self.validation_results["wordlist"] = result
            self._update_status_label("wordlist", result)
            return

        result = self._validate_quarantine_path(self.quarantine_var.get())
        self.validation_results["quarantine"] = result
        self._update_status_label("quarantine", result)

    def _validate_smbseek_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"valid": False, "message": "Path is required."}

        path_obj = Path(path).expanduser()
        if not path_obj.exists():
            return {"valid": False, "message": "Path does not exist."}
        if not path_obj.is_dir():
            return {"valid": False, "message": "Path is not a directory."}

        smbseek_script = path_obj / "cli" / "smbseek.py"
        if not smbseek_script.exists():
            return {"valid": False, "message": "Missing cli/smbseek.py entrypoint."}

        try:
            result = subprocess.run(
                [str(smbseek_script), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return {"valid": False, "message": "smbseek executable failed version check."}
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            return {"valid": True, "message": "smbseek found; version check skipped."}

        return {"valid": True, "message": "Valid SMBSeek installation."}

    def _validate_database_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"valid": False, "message": "Database path is required."}

        path_obj = Path(path).expanduser()
        if not path_obj.exists():
            if not path_obj.parent.exists():
                return {"valid": False, "message": "Parent directory does not exist."}
            return {"valid": True, "message": "Database file will be created."}
        if not path_obj.is_file():
            return {"valid": False, "message": "Path is not a file."}

        try:
            import sqlite3

            with sqlite3.connect(str(path_obj)) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {row[0] for row in cursor.fetchall()}
                if not tables:
                    return {"valid": False, "message": "SQLite file has no tables."}
            return {"valid": True, "message": "SQLite database is readable."}
        except Exception as exc:
            return {"valid": False, "message": f"SQLite validation failed: {exc}"}

    def _validate_config_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"valid": False, "message": "Config path is required."}

        path_obj = Path(path).expanduser()
        if not path_obj.exists():
            if path_obj.parent.exists() and path_obj.suffix == ".json":
                return {"valid": True, "message": "Config file will be created."}
            return {"valid": False, "message": "Config file not found."}
        if not path_obj.is_file():
            return {"valid": False, "message": "Path is not a file."}

        try:
            data = json.loads(path_obj.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return {"valid": False, "message": f"Invalid JSON: {exc}"}
        except Exception as exc:
            return {"valid": False, "message": f"Read failure: {exc}"}

        if not isinstance(data, dict):
            return {"valid": False, "message": "Config root must be a JSON object."}
        return {"valid": True, "message": "Valid configuration file."}

    def _validate_api_key(self, value: str) -> Dict[str, Any]:
        api_key = str(value or "").strip()
        if not api_key:
            return {"valid": False, "message": "API key is empty; scans will fail."}
        if any(ch.isspace() for ch in api_key):
            return {"valid": False, "message": "API key should not contain whitespace."}
        return {"valid": True, "message": "API key set."}

    def _validate_quarantine_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"valid": False, "message": "Quarantine directory is required."}

        path_obj = Path(path).expanduser()
        if path_obj.exists() and not path_obj.is_dir():
            return {"valid": False, "message": "Quarantine path is not a directory."}
        if path_obj.exists():
            return {"valid": True, "message": "Quarantine directory is valid."}
        if not path_obj.parent.exists():
            return {"valid": False, "message": "Parent directory does not exist."}
        return {"valid": True, "message": "Quarantine directory will be created."}

    def _validate_wordlist_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            # Optional field: leaving this unset should not block Save.
            return {"valid": True, "message": "Wordlist not set."}

        path_obj = Path(path).expanduser()
        if not path_obj.exists():
            return {"valid": False, "message": "Wordlist file not found."}
        if not path_obj.is_file():
            return {"valid": False, "message": "Wordlist path is not a file."}
        return {"valid": True, "message": "Wordlist file is valid."}

    def _validate_dork_query(self, value: str, label: str) -> Dict[str, Any]:
        query = str(value or "").strip()
        if not query:
            return {"valid": False, "message": f"{label} cannot be blank."}
        return {"valid": True, "message": f"{label} is set."}

    def _update_status_label(self, field: str, result: Dict[str, Any]) -> None:
        label = self.status_labels.get(field)
        if not label:
            return
        if result.get("valid"):
            symbol = self.theme.get_icon_symbol("success")
            color = self.theme.colors["success"]
        else:
            symbol = self.theme.get_icon_symbol("error")
            color = self.theme.colors["error"]
        label.config(text=symbol, fg=color)

    def _validate_all_fields(self) -> None:
        for field in ("smbseek", "database", "config", "api_key", "quarantine", "wordlist", "smb_dork", "ftp_dork", "http_dork"):
            self._validate_field(field)

    # ------------------------------------------------------------------
    # Save behavior
    # ------------------------------------------------------------------

    def _on_ok(self) -> None:
        if self._validate_and_save():
            self.dialog.destroy()

    def _on_cancel(self) -> None:
        self.dialog.destroy()

    def _open_smbseek_config_editor(self) -> None:
        config_path = self.config_var.get().strip() if self.config_var else ""
        if not config_path:
            messagebox.showwarning("No Configuration File", "Please specify a configuration file path first.")
            return

        if not self.config_editor_callback:
            messagebox.showinfo("Configuration Editor", "Configuration editor callback not available.")
            return

        try:
            self.config_editor_callback(config_path)
        except Exception as exc:
            messagebox.showerror("Configuration Editor Error", f"Failed to open configuration editor:\n{exc}")

    def _validate_and_save(self) -> bool:
        self._validate_all_fields()

        invalid_required = [field for field in self.REQUIRED_FIELDS if not self.validation_results[field]["valid"]]
        if invalid_required:
            details = "\n".join(
                f"- {self.FIELD_LABELS[field]}: {self.validation_results[field]['message']}"
                for field in invalid_required
            )
            messagebox.showerror(
                "Configuration Validation Failed",
                f"Please fix the following issues before saving:\n\n{details}",
            )
            return False

        new_smbseek = self.smbseek_var.get().strip()
        new_database = self.database_var.get().strip()
        new_config_path = self.config_var.get().strip()
        new_api_key = self.api_key_var.get().strip()
        new_quarantine = self.quarantine_var.get().strip()
        new_wordlist = self.wordlist_var.get().strip()
        new_smb_dork = self.smb_dork_var.get().strip()
        new_ftp_dork = self.ftp_dork_var.get().strip()
        new_http_dork = self.http_dork_var.get().strip()

        old_smbseek = self.smbseek_path
        old_database = self.database_path
        old_config_path = self.config_path

        try:
            # Persist GUI-side path pointers.
            if self.settings_manager:
                self.settings_manager.set_backend_path(new_smbseek, validate=False)
                self.settings_manager.set_database_path(new_database, validate=False)
                self.settings_manager.set_setting("backend.config_path", new_config_path)
                # Keeps on-demand extract defaults aligned with shared quarantine.
                self.settings_manager.set_setting("extract.last_directory", new_quarantine)

            # Persist runtime config fields in conf/config.json.
            if self.main_config and hasattr(self.main_config, "set_config_path"):
                self.main_config.set_config_path(new_config_path)
                self.main_config.set_smbseek_path(new_smbseek)
                self.main_config.set_database_path(new_database)
                self._apply_runtime_settings(
                    self.main_config.config,
                    new_api_key,
                    new_quarantine,
                    new_wordlist,
                    new_smb_dork,
                    new_ftp_dork,
                    new_http_dork,
                )
                if not self.main_config.save_config():
                    raise RuntimeError("Failed to write config.json")
            else:
                config_data = self._load_runtime_config_json(
                    new_config_path,
                    fallback_from=old_config_path,
                )
                gui_app = _ensure_dict(config_data.get("gui_app"))
                gui_app["smbseek_path"] = new_smbseek
                gui_app["database_path"] = new_database
                config_data["gui_app"] = gui_app
                _set_nested(config_data, ("database", "path"), new_database)
                self._apply_runtime_settings(
                    config_data,
                    new_api_key,
                    new_quarantine,
                    new_wordlist,
                    new_smb_dork,
                    new_ftp_dork,
                    new_http_dork,
                )
                path_obj = Path(new_config_path).expanduser()
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                path_obj.write_text(json.dumps(config_data, indent=2), encoding="utf-8")

            self.smbseek_path = new_smbseek
            self.database_path = new_database
            self.config_path = new_config_path
            self.api_key = new_api_key
            self.quarantine_path = new_quarantine
            self.wordlist_path = new_wordlist
            self.smb_dork = new_smb_dork
            self.ftp_dork = new_ftp_dork
            self.http_dork = new_http_dork
            self._capture_open_dork_values()

            # Refresh downstream interfaces whenever runtime-critical values changed.
            runtime_changed = (
                old_smbseek != new_smbseek
                or old_database != new_database
                or old_config_path != new_config_path
            )
            if self.refresh_callback and runtime_changed:
                self.refresh_callback()

            if not self.validation_results["api_key"]["valid"]:
                messagebox.showwarning(
                    "Configuration Saved",
                    "Settings were saved, but Shodan API key is empty.\n"
                    "Discovery scans will fail until a valid key is set.",
                )
            if not self.validation_results["wordlist"]["valid"]:
                messagebox.showwarning(
                    "Configuration Saved",
                    "Settings were saved, but the Pry wordlist path is invalid.\n"
                    "Pry operations may fail until this path is corrected.",
                )

            return True
        except Exception as exc:
            messagebox.showerror(
                "Configuration Save Failed",
                f"Failed to save configuration:\n{exc}\n\nPlease check your settings and try again.",
            )
            return False

    def _load_runtime_config_json(self, config_path: str, fallback_from: Optional[str] = None) -> Dict[str, Any]:
        path_obj = Path(config_path).expanduser()
        candidates = [path_obj]

        if not path_obj.exists():
            example_path = path_obj.parent / f"{path_obj.name}.example"
            if example_path.exists():
                candidates.append(example_path)
            if fallback_from:
                fallback_path = Path(fallback_from).expanduser()
                if fallback_path != path_obj and fallback_path.exists():
                    candidates.append(fallback_path)

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                continue
        return {}

    def _apply_runtime_settings(
        self,
        config_data: Dict[str, Any],
        api_key: str,
        quarantine_path: str,
        wordlist_path: str,
        smb_dork: str,
        ftp_dork: str,
        http_dork: str,
    ) -> None:
        # Shodan API key drives scan processes.
        _set_nested(config_data, ("shodan", "api_key"), api_key)
        _set_nested(config_data, ("shodan", "query_components", "base_query"), smb_dork)
        _set_nested(config_data, ("ftp", "shodan", "query_components", "base_query"), ftp_dork)
        _set_nested(config_data, ("http", "shodan", "query_components", "base_query"), http_dork)

        # Keep quarantine path aligned across browser and extract-adjacent sections.
        _set_nested(config_data, ("file_browser", "quarantine_root"), quarantine_path)
        _set_nested(config_data, ("ftp_browser", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("http_browser", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("file_collection", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("pry", "wordlist_path"), wordlist_path)


def open_app_config_dialog(
    parent: tk.Widget,
    settings_manager=None,
    config_editor_callback: Optional[Callable[[str], None]] = None,
    main_config=None,
    refresh_callback: Optional[Callable[[], None]] = None,
) -> None:
    """Open application configuration dialog."""
    try:
        AppConfigDialog(parent, settings_manager, config_editor_callback, main_config, refresh_callback)
    except Exception as exc:
        messagebox.showerror("Configuration Dialog Error", f"Failed to open configuration dialog:\n{exc}")
