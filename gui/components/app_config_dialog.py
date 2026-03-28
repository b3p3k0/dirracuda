"""
Dirracuda Application Configuration Dialog

Compact configuration dialog for managing Dirracuda integration settings.
This includes backend paths plus runtime settings that should propagate
to scan, browse, and extract workflows.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, Optional

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme
from gui.utils.wordlist_path import normalize_wordlist_path
from shared.db_path_resolution import (
    auto_detect_database_path,
    normalize_database_path,
    resolve_database_path,
)


_CLAMAV_TRUE = frozenset(("true", "yes", "1"))
_CLAMAV_BACKENDS = frozenset(("auto", "clamdscan", "clamscan"))
_TMPFS_SIZE_MIN_MB = 64
_TMPFS_SIZE_MAX_MB = 4096
_TMPFS_SIZE_DEFAULT_MB = 512


def _coerce_bool_cfg(value: Any, default: bool) -> bool:
    """Coerce a JSON bool-like value to bool, returning default for None/missing."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _CLAMAV_TRUE


def _coerce_int_cfg(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


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
    """

    REQUIRED_FIELDS = ("smbseek", "database", "config", "quarantine")
    FIELD_LABELS = {
        "smbseek": "Dirracuda Root",
        "database": "Database File",
        "config": "Dirracuda Config",
        "api_key": "Shodan API Key",
        "quarantine": "Quarantine Directory",
        "wordlist": "Pry Wordlist Path",
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
        self.quarantine_path = "~/.dirracuda/quarantine"
        self.wordlist_path = ""
        self.quarantine_tmpfs_enabled = False
        self.quarantine_tmpfs_size_mb = _TMPFS_SIZE_DEFAULT_MB
        self._tmpfs_supported_platform = sys.platform.startswith("linux")

        self.validation_results: Dict[str, Dict[str, Any]] = {
            "smbseek": {"valid": False, "message": ""},
            "database": {"valid": False, "message": ""},
            "config": {"valid": False, "message": ""},
            "api_key": {"valid": False, "message": ""},
            "quarantine": {"valid": False, "message": ""},
            "wordlist": {"valid": False, "message": ""},
        }

        self.dialog: Optional[tk.Toplevel] = None
        self.status_labels: Dict[str, tk.Label] = {}

        self.smbseek_var: Optional[tk.StringVar] = None
        self.database_var: Optional[tk.StringVar] = None
        self.config_var: Optional[tk.StringVar] = None
        self.api_key_var: Optional[tk.StringVar] = None
        self.quarantine_var: Optional[tk.StringVar] = None
        self.wordlist_var: Optional[tk.StringVar] = None
        self.quarantine_entry_widget: Optional[tk.Entry] = None
        self.quarantine_browse_button: Optional[tk.Button] = None
        self.api_key_entry: Optional[tk.Entry] = None
        self.api_key_toggle_btn: Optional[tk.Button] = None
        self.api_key_masked = True

        self.clamav_enabled: bool = False
        self.clamav_backend: str = "auto"
        self.clamav_timeout: int = 60
        self.clamav_extracted_root: str = "~/.dirracuda/extracted"
        self.clamav_known_bad_subdir: str = "known_bad"
        self.clamav_show_results: bool = True
        self.clamav_auto_promote_clean: bool = False

        self.clamav_enabled_var: Optional[tk.BooleanVar] = None
        self.clamav_backend_var: Optional[tk.StringVar] = None
        self.clamav_timeout_var: Optional[tk.StringVar] = None
        self.clamav_extracted_root_var: Optional[tk.StringVar] = None
        self.clamav_known_bad_subdir_var: Optional[tk.StringVar] = None
        self.clamav_show_results_var: Optional[tk.BooleanVar] = None
        self.clamav_auto_promote_clean_var: Optional[tk.BooleanVar] = None
        self.quarantine_tmpfs_enabled_var: Optional[tk.BooleanVar] = None
        self.quarantine_tmpfs_size_var: Optional[tk.StringVar] = None
        self.quarantine_tmpfs_size_entry: Optional[tk.Entry] = None
        self.quarantine_tmpfs_note_label: Optional[tk.Label] = None

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
            self.database_path = str(resolve_database_path(
                backend_path=self.smbseek_path,
                cli_database_path=None,
                persisted_paths=[
                    self.settings_manager.get_setting("backend.last_database_path", ""),
                    self.settings_manager.get_setting("backend.database_path", ""),
                ],
            ))
        else:
            self.smbseek_path = str(Path.cwd())
            self.config_path = str(Path.cwd() / "conf" / "config.json")
            self.database_path = str(auto_detect_database_path(Path.cwd()))

        self._load_runtime_settings_from_config(self.config_path)

    def _load_runtime_settings_from_config(self, config_path: str) -> None:
        """Load API key, pry wordlist, and quarantine path from config.json."""
        path_obj = Path(config_path).expanduser()
        if not path_obj.exists():
            return

        try:
            config_data = json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception:
            return

        self.api_key = str(_get_nested(config_data, ("shodan", "api_key"), "") or "")
        raw_wordlist = str(_get_nested(config_data, ("pry", "wordlist_path"), "") or "")
        self.wordlist_path = normalize_wordlist_path(raw_wordlist, config_path=path_obj)

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

        quarantine_cfg = config_data.get("quarantine")
        if isinstance(quarantine_cfg, dict):
            self.quarantine_tmpfs_enabled = _coerce_bool_cfg(quarantine_cfg.get("use_tmpfs"), False)
            self.quarantine_tmpfs_size_mb = _coerce_int_cfg(
                quarantine_cfg.get("tmpfs_size_mb"),
                _TMPFS_SIZE_DEFAULT_MB,
                minimum=_TMPFS_SIZE_MIN_MB,
                maximum=_TMPFS_SIZE_MAX_MB,
            )
        else:
            self.quarantine_tmpfs_enabled = False
            self.quarantine_tmpfs_size_mb = _TMPFS_SIZE_DEFAULT_MB

        clamav_raw = config_data.get("clamav")
        if isinstance(clamav_raw, dict):
            self.clamav_enabled = _coerce_bool_cfg(clamav_raw.get("enabled"), False)
            raw_backend = str(clamav_raw.get("backend", "auto")).strip().lower()
            self.clamav_backend = raw_backend if raw_backend in _CLAMAV_BACKENDS else "auto"
            try:
                self.clamav_timeout = max(1, int(clamav_raw.get("timeout_seconds", 60)))
            except (TypeError, ValueError):
                self.clamav_timeout = 60
            self.clamav_extracted_root = str(
                clamav_raw.get("extracted_root", "~/.dirracuda/extracted")
            )
            self.clamav_known_bad_subdir = str(clamav_raw.get("known_bad_subdir", "known_bad"))
            self.clamav_show_results = _coerce_bool_cfg(clamav_raw.get("show_results"), True)
            self.clamav_auto_promote_clean = _coerce_bool_cfg(
                clamav_raw.get("auto_promote_clean_files"),
                False,
            )

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _create_dialog(self) -> None:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Dirracuda - Application Configuration")
        self.dialog.geometry("760x900")
        self.dialog.minsize(720, 680)
        self.theme.apply_to_widget(self.dialog, "main_window")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        # Default to masked every time the dialog is opened.
        self.api_key_masked = True

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
        self._create_tmpfs_card(container)
        self._create_clamav_card(container)
        self._sync_quarantine_controls_for_tmpfs()

        action_row = tk.Frame(container)
        self.theme.apply_to_widget(action_row, "main_window")
        action_row.pack(fill=tk.X, padx=8, pady=(4, 0))

        edit_button = tk.Button(
            action_row,
            text="Edit Dirracuda Config...",
            command=self._open_smbseek_config_editor,
        )
        self.theme.apply_to_widget(edit_button, "button_secondary")
        edit_button.pack(anchor=tk.W)

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

    def _create_tmpfs_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(parent, highlightthickness=1, bd=0)
        self.theme.apply_to_widget(card, "card")
        try:
            card.configure(
                highlightbackground=self.theme.colors["border"],
                highlightcolor=self.theme.colors["border"],
            )
        except tk.TclError:
            pass
        card.pack(fill=tk.X, pady=(0, 10))

        heading = self.theme.create_styled_label(card, "In-Memory Quarantine (tmpfs)", "body")
        heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

        row1 = tk.Frame(card)
        self.theme.apply_to_widget(row1, "card")
        row1.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.quarantine_tmpfs_enabled_var = tk.BooleanVar(value=self.quarantine_tmpfs_enabled)
        cb_tmpfs = tk.Checkbutton(
            row1,
            text="Use memory (tmpfs) for quarantine",
            variable=self.quarantine_tmpfs_enabled_var,
            command=self._sync_quarantine_controls_for_tmpfs,
        )
        self.theme.apply_to_widget(cb_tmpfs, "checkbox")
        cb_tmpfs.pack(anchor=tk.W)

        row2 = tk.Frame(card)
        self.theme.apply_to_widget(row2, "card")
        row2.pack(fill=tk.X, padx=10, pady=(0, 6))
        lbl_size = self.theme.create_styled_label(
            row2,
            "Max size (MB):",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        lbl_size.pack(side=tk.LEFT, padx=(0, 8))
        self.quarantine_tmpfs_size_var = tk.StringVar(value=str(self.quarantine_tmpfs_size_mb))
        self.quarantine_tmpfs_size_entry = tk.Entry(
            row2,
            textvariable=self.quarantine_tmpfs_size_var,
            font=("Arial", 10),
            width=8,
        )
        self.theme.apply_to_widget(self.quarantine_tmpfs_size_entry, "entry")
        self.quarantine_tmpfs_size_entry.pack(side=tk.LEFT)
        size_hint = self.theme.create_styled_label(
            row2,
            f"Range {_TMPFS_SIZE_MIN_MB}-{_TMPFS_SIZE_MAX_MB}",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        size_hint.pack(side=tk.LEFT, padx=(6, 0))

        self.quarantine_tmpfs_note_label = self.theme.create_styled_label(
            card,
            "",
            "small",
            fg=self.theme.colors["text_secondary"],
        )
        self.quarantine_tmpfs_note_label.pack(anchor=tk.W, padx=12, pady=(0, 10))

        if not self._tmpfs_supported_platform:
            if self.quarantine_tmpfs_enabled_var:
                self.quarantine_tmpfs_enabled_var.set(False)
            try:
                cb_tmpfs.configure(state=tk.DISABLED)
            except tk.TclError:
                pass
            try:
                self.quarantine_tmpfs_size_entry.configure(state=tk.DISABLED)
            except tk.TclError:
                pass

    def _sync_quarantine_controls_for_tmpfs(self) -> None:
        tmpfs_enabled = bool(self.quarantine_tmpfs_enabled_var.get()) if self.quarantine_tmpfs_enabled_var else False
        if not self._tmpfs_supported_platform:
            tmpfs_enabled = False

        quarantine_state = tk.DISABLED if tmpfs_enabled else tk.NORMAL
        for widget in (self.quarantine_entry_widget, self.quarantine_browse_button):
            if widget is None:
                continue
            try:
                widget.configure(state=quarantine_state)
            except tk.TclError:
                pass

        if self.quarantine_tmpfs_size_entry is not None:
            size_state = tk.NORMAL if (tmpfs_enabled and self._tmpfs_supported_platform) else tk.DISABLED
            try:
                self.quarantine_tmpfs_size_entry.configure(state=size_state)
            except tk.TclError:
                pass

        if self.quarantine_tmpfs_note_label is not None:
            if not self._tmpfs_supported_platform:
                note = "tmpfs quarantine is available on Linux only; this setting is disabled here."
            elif tmpfs_enabled:
                note = "Quarantine directory selection is disabled while tmpfs mode is enabled."
            else:
                note = "When enabled, quarantine writes route to ~/.dirracuda/quarantine_tmpfs."
            self.quarantine_tmpfs_note_label.configure(text=note)

    def _create_clamav_card(self, parent: tk.Widget) -> None:
        card = tk.Frame(parent, highlightthickness=1, bd=0)
        self.theme.apply_to_widget(card, "card")
        try:
            card.configure(
                highlightbackground=self.theme.colors["border"],
                highlightcolor=self.theme.colors["border"],
            )
        except tk.TclError:
            pass
        card.pack(fill=tk.X, pady=(0, 10))

        heading = self.theme.create_styled_label(card, "ClamAV Settings", "body")
        heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

        # Row 1 — Enable + clean-promotion checkboxes
        row1 = tk.Frame(card)
        self.theme.apply_to_widget(row1, "card")
        row1.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.clamav_enabled_var = tk.BooleanVar(value=self.clamav_enabled)
        cb_enable = tk.Checkbutton(row1, text="Enable ClamAV scanning", variable=self.clamav_enabled_var)
        self.theme.apply_to_widget(cb_enable, "checkbox")
        cb_enable.pack(side=tk.LEFT)

        self.clamav_auto_promote_clean_var = tk.BooleanVar(value=self.clamav_auto_promote_clean)
        cb_promote_clean = tk.Checkbutton(
            row1,
            text="Automatically promote clean files",
            variable=self.clamav_auto_promote_clean_var,
        )
        self.theme.apply_to_widget(cb_promote_clean, "checkbox")
        cb_promote_clean.pack(side=tk.LEFT, padx=(14, 0))

        # Row 2 — Backend selector
        row2 = tk.Frame(card)
        self.theme.apply_to_widget(row2, "card")
        row2.pack(fill=tk.X, padx=10, pady=(0, 6))
        lbl_backend = self.theme.create_styled_label(
            row2, "Backend:", "small", fg=self.theme.colors["text_secondary"]
        )
        lbl_backend.pack(side=tk.LEFT, padx=(0, 8))
        self.clamav_backend_var = tk.StringVar(value=self.clamav_backend)
        opt_backend = tk.OptionMenu(row2, self.clamav_backend_var, "auto", "clamdscan", "clamscan")
        self.theme.apply_to_widget(opt_backend, "button_secondary")
        opt_backend.pack(side=tk.LEFT)

        # Row 3 — Timeout
        row3 = tk.Frame(card)
        self.theme.apply_to_widget(row3, "card")
        row3.pack(fill=tk.X, padx=10, pady=(0, 6))
        lbl_timeout = self.theme.create_styled_label(
            row3, "Timeout:", "small", fg=self.theme.colors["text_secondary"]
        )
        lbl_timeout.pack(side=tk.LEFT, padx=(0, 8))
        self.clamav_timeout_var = tk.StringVar(value=str(self.clamav_timeout))
        entry_timeout = tk.Entry(row3, textvariable=self.clamav_timeout_var, font=("Arial", 10), width=6)
        self.theme.apply_to_widget(entry_timeout, "entry")
        entry_timeout.pack(side=tk.LEFT)
        lbl_sec = self.theme.create_styled_label(
            row3, "seconds", "small", fg=self.theme.colors["text_secondary"]
        )
        lbl_sec.pack(side=tk.LEFT, padx=(6, 0))

        # Row 4 — Extracted root path with browse
        row4 = tk.Frame(card)
        self.theme.apply_to_widget(row4, "card")
        row4.pack(fill=tk.X, padx=10, pady=(0, 6))
        lbl_root = self.theme.create_styled_label(
            row4, "Extracted root:", "small", fg=self.theme.colors["text_secondary"]
        )
        lbl_root.pack(side=tk.LEFT, padx=(0, 8))
        self.clamav_extracted_root_var = tk.StringVar(value=self.clamav_extracted_root)
        entry_root = tk.Entry(
            row4, textvariable=self.clamav_extracted_root_var, font=("Arial", 10)
        )
        self.theme.apply_to_widget(entry_root, "entry")
        entry_root.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        browse_root = tk.Button(
            row4,
            text="Browse...",
            command=lambda: self._browse_path("clamav_extracted_root"),
        )
        self.theme.apply_to_widget(browse_root, "button_secondary")
        browse_root.pack(side=tk.LEFT)

        # Row 5 — Known-bad subfolder name
        row5 = tk.Frame(card)
        self.theme.apply_to_widget(row5, "card")
        row5.pack(fill=tk.X, padx=10, pady=(0, 6))
        lbl_kb = self.theme.create_styled_label(
            row5, "Known-bad subfolder:", "small", fg=self.theme.colors["text_secondary"]
        )
        lbl_kb.pack(side=tk.LEFT, padx=(0, 8))
        self.clamav_known_bad_subdir_var = tk.StringVar(value=self.clamav_known_bad_subdir)
        entry_kb = tk.Entry(
            row5, textvariable=self.clamav_known_bad_subdir_var, font=("Arial", 10)
        )
        self.theme.apply_to_widget(entry_kb, "entry")
        entry_kb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Row 6 — Show results dialog checkbox
        row6 = tk.Frame(card)
        self.theme.apply_to_widget(row6, "card")
        row6.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.clamav_show_results_var = tk.BooleanVar(value=self.clamav_show_results)
        cb_show = tk.Checkbutton(
            row6, text="Show results dialog after extract", variable=self.clamav_show_results_var
        )
        self.theme.apply_to_widget(cb_show, "checkbox")
        cb_show.pack(anchor=tk.W)

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
        if field == "quarantine":
            self.quarantine_entry_widget = entry
        if field == "api_key":
            self.api_key_entry = entry
            toggle_button = tk.Button(
                row,
                text="👁️",
                width=3,
                command=self._toggle_api_key_mask,
            )
            self.theme.apply_to_widget(toggle_button, "button_secondary")
            toggle_button.pack(side=tk.LEFT, padx=(0, 8))
            self.api_key_toggle_btn = toggle_button
            self._update_api_key_mask_ui()

        browse_needed = field in {"smbseek", "database", "config", "quarantine", "wordlist"}
        if browse_needed:
            browse_button = tk.Button(
                row,
                text="Browse...",
                command=lambda ft=field: self._browse_path(ft),
            )
            self.theme.apply_to_widget(browse_button, "button_secondary")
            browse_button.pack(side=tk.LEFT, padx=(0, 8))
            if field == "quarantine":
                self.quarantine_browse_button = browse_button

        status_label = tk.Label(row, text="", font=("Arial", 11, "bold"), width=2)
        self.theme.apply_to_widget(status_label, "text")
        status_label.pack(side=tk.RIGHT)
        self.status_labels[field] = status_label

        variable.trace_add("write", lambda *_args, ft=field: self._validate_field(ft))

    def _update_api_key_mask_ui(self) -> None:
        if self.api_key_entry:
            self.api_key_entry.configure(show="*" if self.api_key_masked else "")
        if self.api_key_toggle_btn:
            self.api_key_toggle_btn.configure(text="👁️" if self.api_key_masked else "🕶️")

    def _toggle_api_key_mask(self) -> None:
        self.api_key_masked = not self.api_key_masked
        self._update_api_key_mask_ui()

    def _field_var(self, field: str) -> tk.StringVar:
        if field == "smbseek":
            self.smbseek_var = tk.StringVar(value=self.smbseek_path)
            return self.smbseek_var
        if field == "database":
            self.database_var = tk.StringVar(value=self.database_path)
            return self.database_var
        if field == "config":
            self.config_var = tk.StringVar(value=self.config_path)
            return self.config_var
        if field == "api_key":
            self.api_key_var = tk.StringVar(value=self.api_key)
            return self.api_key_var
        if field == "wordlist":
            self.wordlist_var = tk.StringVar(value=self.wordlist_path)
            return self.wordlist_var
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
                title="Select Dirracuda Installation Directory",
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
                title="Select Dirracuda Configuration File",
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
            return

        if field == "clamav_extracted_root" and self.clamav_extracted_root_var:
            initial = str(
                Path(self.clamav_extracted_root_var.get()).expanduser().parent
            ) or initial
            selected = filedialog.askdirectory(
                title="Select Extracted Files Root",
                initialdir=initial,
            )
            if selected:
                self.clamav_extracted_root_var.set(selected)

    def _validate_field(self, field: str) -> None:
        if field == "smbseek":
            result = self._validate_smbseek_path(self.smbseek_var.get())
            self.validation_results["smbseek"] = result
            self._update_status_label("smbseek", result)

            if result["valid"]:
                derived_config = str(Path(self.smbseek_var.get()) / "conf" / "config.json")
                if self.config_var and (not self.config_var.get() or "conf/config.json" in self.config_var.get()):
                    self.config_var.set(derived_config)
                derived_db = str(auto_detect_database_path(Path(self.smbseek_var.get())))
                if self.database_var and (
                    not self.database_var.get()
                    or self.database_var.get().endswith("smbseek.db")
                    or self.database_var.get().endswith("dirracuda.db")
                ):
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
            return {"valid": False, "message": "Missing Dirracuda executable."}

        try:
            result = subprocess.run(
                [str(smbseek_script), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return {"valid": False, "message": "Dirracuda executable failed version check."}
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
            return {"valid": True, "message": "Dirracuda found; version check skipped."}

        return {"valid": True, "message": "Valid Dirracuda installation."}

    def _validate_database_path(self, path: str) -> Dict[str, Any]:
        path = str(path or "").strip()
        if not path:
            return {"valid": False, "message": "Database path is required."}

        backend_path = (
            self.smbseek_var.get().strip()
            if self.smbseek_var is not None
            else self.smbseek_path
        )
        path_obj = normalize_database_path(path, backend_path or Path.cwd())
        if path_obj is None:
            return {"valid": False, "message": "Database path is invalid."}

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
        for field in ("smbseek", "database", "config", "api_key", "quarantine", "wordlist"):
            self._validate_field(field)

    def _messagebox_parent(self) -> tk.Widget:
        dialog = self.dialog
        try:
            if dialog is not None and int(dialog.winfo_exists()) == 1:
                return dialog
        except Exception:
            pass
        return self.parent

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
            messagebox.showwarning(
                "No Configuration File",
                "Please specify a configuration file path first.",
                parent=self._messagebox_parent(),
            )
            return

        if not self.config_editor_callback:
            messagebox.showinfo(
                "Configuration Editor",
                "Configuration editor callback not available.",
                parent=self._messagebox_parent(),
            )
            return

        try:
            self.config_editor_callback(config_path)
        except Exception as exc:
            messagebox.showerror(
                "Configuration Editor Error",
                f"Failed to open configuration editor:\n{exc}",
                parent=self._messagebox_parent(),
            )

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
                parent=self._messagebox_parent(),
            )
            return False

        new_smbseek = self.smbseek_var.get().strip()
        new_database = self.database_var.get().strip()
        new_config_path = self.config_var.get().strip()
        new_api_key = self.api_key_var.get().strip()
        new_quarantine = self.quarantine_var.get().strip()
        new_wordlist = self.wordlist_var.get().strip()

        _timeout_var = getattr(self, "clamav_timeout_var", None)
        try:
            _clamav_timeout = max(1, int(_timeout_var.get())) if _timeout_var else 60
        except (TypeError, ValueError):
            _clamav_timeout = 60
        _backend_var = getattr(self, "clamav_backend_var", None)
        _raw_backend = _backend_var.get().strip().lower() if _backend_var else "auto"
        _enabled_var = getattr(self, "clamav_enabled_var", None)
        _root_var = getattr(self, "clamav_extracted_root_var", None)
        _kb_var = getattr(self, "clamav_known_bad_subdir_var", None)
        _show_var = getattr(self, "clamav_show_results_var", None)
        _promote_clean_var = getattr(self, "clamav_auto_promote_clean_var", None)
        new_clamav = {
            "enabled": bool(_enabled_var.get()) if _enabled_var else False,
            "backend": _raw_backend if _raw_backend in _CLAMAV_BACKENDS else "auto",
            "timeout_seconds": _clamav_timeout,
            "extracted_root": (_root_var.get().strip() if _root_var else "") or "~/.dirracuda/extracted",
            "known_bad_subdir": (_kb_var.get().strip() if _kb_var else "") or "known_bad",
            "show_results": bool(_show_var.get()) if _show_var else True,
            "auto_promote_clean_files": bool(_promote_clean_var.get()) if _promote_clean_var else False,
        }

        tmpfs_enabled_var = getattr(self, "quarantine_tmpfs_enabled_var", None)
        tmpfs_size_var = getattr(self, "quarantine_tmpfs_size_var", None)
        tmpfs_supported_platform = getattr(self, "_tmpfs_supported_platform", sys.platform.startswith("linux"))
        tmpfs_enabled = bool(tmpfs_enabled_var.get()) if tmpfs_enabled_var else False
        if not tmpfs_supported_platform:
            tmpfs_enabled = False
        try:
            tmpfs_size_mb = int(tmpfs_size_var.get()) if tmpfs_size_var else _TMPFS_SIZE_DEFAULT_MB
        except (TypeError, ValueError):
            messagebox.showerror(
                "Configuration Validation Failed",
                f"tmpfs size must be an integer between {_TMPFS_SIZE_MIN_MB} and {_TMPFS_SIZE_MAX_MB} MB.",
                parent=self._messagebox_parent(),
            )
            return False
        if tmpfs_size_mb < _TMPFS_SIZE_MIN_MB or tmpfs_size_mb > _TMPFS_SIZE_MAX_MB:
            messagebox.showerror(
                "Configuration Validation Failed",
                f"tmpfs size must be between {_TMPFS_SIZE_MIN_MB} and {_TMPFS_SIZE_MAX_MB} MB.",
                parent=self._messagebox_parent(),
            )
            return False
        new_quarantine_tmpfs = {
            "use_tmpfs": tmpfs_enabled,
            "tmpfs_size_mb": tmpfs_size_mb,
        }

        old_smbseek = self.smbseek_path
        old_database = self.database_path
        old_config_path = self.config_path
        old_clamav_enabled = bool(getattr(self, "clamav_enabled", False))
        old_clamav_backend = str(getattr(self, "clamav_backend", "auto")).strip().lower()
        if old_clamav_backend not in _CLAMAV_BACKENDS:
            old_clamav_backend = "auto"
        old_clamav_auto_promote_clean = bool(getattr(self, "clamav_auto_promote_clean", False))
        old_tmpfs_enabled = bool(getattr(self, "quarantine_tmpfs_enabled", False))
        try:
            old_tmpfs_size_mb = int(getattr(self, "quarantine_tmpfs_size_mb", _TMPFS_SIZE_DEFAULT_MB))
        except (TypeError, ValueError):
            old_tmpfs_size_mb = _TMPFS_SIZE_DEFAULT_MB

        try:
            normalized_database = normalize_database_path(new_database, new_smbseek)
            if normalized_database is None:
                raise ValueError("Invalid database path.")
            normalized_database_str = str(normalized_database)

            # Persist GUI-side path pointers.
            if self.settings_manager:
                self.settings_manager.set_backend_path(new_smbseek, validate=False)
                self.settings_manager.set_database_path(normalized_database_str, validate=False)
                self.settings_manager.set_setting("backend.config_path", new_config_path)
                # Keeps on-demand extract defaults aligned with shared quarantine.
                self.settings_manager.set_setting("extract.last_directory", new_quarantine)

            # Persist runtime config fields in conf/config.json.
            if self.main_config and hasattr(self.main_config, "set_config_path"):
                self.main_config.set_config_path(new_config_path)
                self.main_config.set_smbseek_path(new_smbseek)
                self.main_config.set_database_path(normalized_database_str)
                self._apply_runtime_settings(
                    self.main_config.config,
                    new_api_key,
                    new_quarantine,
                    new_wordlist,
                    clamav_settings=new_clamav,
                    quarantine_tmpfs_settings=new_quarantine_tmpfs,
                )
                if not self.main_config.save_config():
                    raise RuntimeError("Failed to write config.json")
            else:
                config_data = self._load_runtime_config_json(
                    new_config_path,
                    fallback_from=old_config_path,
                )
                gui_app = _ensure_dict(config_data.get("gui_app"))
                gui_app["backend_path"] = new_smbseek
                gui_app.pop("smbseek_path", None)
                gui_app["database_path"] = normalized_database_str
                config_data["gui_app"] = gui_app
                _set_nested(config_data, ("database", "path"), normalized_database_str)
                self._apply_runtime_settings(
                    config_data,
                    new_api_key,
                    new_quarantine,
                    new_wordlist,
                    clamav_settings=new_clamav,
                    quarantine_tmpfs_settings=new_quarantine_tmpfs,
                )
                path_obj = Path(new_config_path).expanduser()
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                path_obj.write_text(json.dumps(config_data, indent=2), encoding="utf-8")

            self.smbseek_path = new_smbseek
            self.database_path = normalized_database_str
            self.config_path = new_config_path
            self.api_key = new_api_key
            self.quarantine_path = new_quarantine
            self.wordlist_path = new_wordlist
            self.clamav_auto_promote_clean = new_clamav["auto_promote_clean_files"]
            self.quarantine_tmpfs_enabled = tmpfs_enabled
            self.quarantine_tmpfs_size_mb = tmpfs_size_mb

            # Refresh downstream interfaces whenever runtime-critical values changed.
            runtime_changed = (
                old_smbseek != new_smbseek
                or old_database != normalized_database_str
                or old_config_path != new_config_path
            )
            status_changed = (
                old_clamav_enabled != new_clamav["enabled"]
                or old_clamav_backend != new_clamav["backend"]
                or old_clamav_auto_promote_clean != new_clamav["auto_promote_clean_files"]
                or old_tmpfs_enabled != tmpfs_enabled
                or old_tmpfs_size_mb != tmpfs_size_mb
            )
            if self.refresh_callback and (runtime_changed or status_changed):
                self.refresh_callback()

            if not self.validation_results["api_key"]["valid"]:
                messagebox.showwarning(
                    "Configuration Saved",
                    "Settings were saved, but Shodan API key is empty.\n"
                    "Discovery scans will fail until a valid key is set.",
                    parent=self._messagebox_parent(),
                )
            if not self.validation_results["wordlist"]["valid"]:
                messagebox.showwarning(
                    "Configuration Saved",
                    "Settings were saved, but the Pry wordlist path is invalid.\n"
                    "Pry operations may fail until this path is corrected.",
                    parent=self._messagebox_parent(),
                )

            return True
        except Exception as exc:
            messagebox.showerror(
                "Configuration Save Failed",
                f"Failed to save configuration:\n{exc}\n\nPlease check your settings and try again.",
                parent=self._messagebox_parent(),
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
        clamav_settings: Optional[Dict[str, Any]] = None,
        quarantine_tmpfs_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Shodan API key drives scan processes.
        _set_nested(config_data, ("shodan", "api_key"), api_key)

        # Keep quarantine path aligned across browser and extract-adjacent sections.
        _set_nested(config_data, ("file_browser", "quarantine_root"), quarantine_path)
        _set_nested(config_data, ("ftp_browser", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("http_browser", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("file_collection", "quarantine_base"), quarantine_path)
        _set_nested(config_data, ("pry", "wordlist_path"), wordlist_path)

        if clamav_settings is not None:
            _set_nested(config_data, ("clamav", "enabled"), clamav_settings["enabled"])
            _set_nested(config_data, ("clamav", "backend"), clamav_settings["backend"])
            _set_nested(config_data, ("clamav", "timeout_seconds"), clamav_settings["timeout_seconds"])
            _set_nested(config_data, ("clamav", "extracted_root"), clamav_settings["extracted_root"])
            _set_nested(config_data, ("clamav", "known_bad_subdir"), clamav_settings["known_bad_subdir"])
            _set_nested(config_data, ("clamav", "show_results"), clamav_settings["show_results"])
            _set_nested(
                config_data,
                ("clamav", "auto_promote_clean_files"),
                clamav_settings["auto_promote_clean_files"],
            )

        if quarantine_tmpfs_settings is not None:
            _set_nested(config_data, ("quarantine", "use_tmpfs"), quarantine_tmpfs_settings["use_tmpfs"])
            _set_nested(config_data, ("quarantine", "tmpfs_size_mb"), quarantine_tmpfs_settings["tmpfs_size_mb"])


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
        messagebox.showerror(
            "Configuration Dialog Error",
            f"Failed to open configuration dialog:\n{exc}",
            parent=parent,
        )
