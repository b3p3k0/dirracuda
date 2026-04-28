"""
Discovery-dork editor launched from Start Scan.

This is a modeless, single-instance dialog that edits only SMB/FTP/HTTP
base queries in config.json.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from gui.components.discovery_dork_config import (
    DORK_DEFAULTS as SHARED_DORK_DEFAULTS,
    DORK_FIELDS as SHARED_DORK_FIELDS,
    DORK_LABELS as SHARED_DORK_LABELS,
    apply_discovery_dorks,
    read_discovery_dorks,
    validate_discovery_dork,
)
from gui.utils import safe_messagebox as messagebox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme

_PROTOCOL_TO_DORK_FIELD = {
    "SMB": "smb_dork",
    "FTP": "ftp_dork",
    "HTTP": "http_dork",
}


def _normalize_config_path(config_path: str) -> Path:
    raw = str(config_path or "").strip()
    if not raw:
        raise ValueError("Config path is required.")
    return Path(raw).expanduser()


def _resolve_field_for_protocol(protocol: str) -> str:
    normalized = str(protocol or "").strip().upper()
    field = _PROTOCOL_TO_DORK_FIELD.get(normalized)
    if field is None:
        raise ValueError(f"Unsupported protocol for discovery dorks: {protocol!r}")
    return field


class ScanDorkEditorDialog:
    """Single-instance, non-blocking discovery-dork editor."""

    DORK_DEFAULTS = dict(SHARED_DORK_DEFAULTS)
    DORK_FIELDS = SHARED_DORK_FIELDS
    FIELD_LABELS = dict(SHARED_DORK_LABELS)

    def __init__(
        self,
        parent: tk.Widget,
        config_path: str,
        settings_manager: Optional[Any] = None,
        on_close_callback: Optional[Callable[["ScanDorkEditorDialog"], None]] = None,
    ) -> None:
        self.parent = parent
        self.config_path = _normalize_config_path(config_path)
        self.settings_manager = settings_manager
        self.theme = get_theme()
        self._on_close_callback = on_close_callback
        self._closed = False

        self.smb_dork = self.DORK_DEFAULTS["smb_dork"]
        self.ftp_dork = self.DORK_DEFAULTS["ftp_dork"]
        self.http_dork = self.DORK_DEFAULTS["http_dork"]
        self._open_dork_values = self.DORK_DEFAULTS.copy()

        self.smb_dork_var: Optional[tk.StringVar] = None
        self.ftp_dork_var: Optional[tk.StringVar] = None
        self.http_dork_var: Optional[tk.StringVar] = None
        self.status_labels: Dict[str, tk.Label] = {}
        self.validation_results: Dict[str, Dict[str, Any]] = {
            "smb_dork": {"valid": False, "message": ""},
            "ftp_dork": {"valid": False, "message": ""},
            "http_dork": {"valid": False, "message": ""},
        }

        self.dialog = tk.Toplevel(parent)
        self._load_dorks_from_config()
        self._create_dialog()

    def _load_dorks_from_config(self) -> None:
        config_data = self._load_runtime_config_json(self.config_path)
        dorks = read_discovery_dorks(config_data)
        self.smb_dork = dorks["smb_dork"]
        self.ftp_dork = dorks["ftp_dork"]
        self.http_dork = dorks["http_dork"]
        self._capture_open_dork_values()

    def _capture_open_dork_values(self) -> None:
        self._open_dork_values = {
            "smb_dork": self.smb_dork,
            "ftp_dork": self.ftp_dork,
            "http_dork": self.http_dork,
        }

    def _create_dialog(self) -> None:
        self.dialog.title("Discovery Dorks")
        self.dialog.geometry("900x310")
        self.dialog.minsize(740, 260)
        self.dialog.transient(self.parent)
        self.theme.apply_to_widget(self.dialog, "main_window")

        self._center_window()
        self._create_header()
        self._create_rows()
        self._create_button_panel()
        self._validate_all_fields()

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.dialog.bind("<Escape>", lambda _e: self._on_cancel())
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
        header.pack(fill=tk.X, padx=16, pady=(14, 10))

        self.theme.create_styled_label(header, "Discovery Dorks", "heading").pack(anchor=tk.W)
        self.theme.create_styled_label(
            header,
            "Edit base queries used by SMB/FTP/HTTP discovery scans.",
            "small",
            fg=self.theme.colors["text_secondary"],
        ).pack(anchor=tk.W, pady=(4, 0))

    def _create_rows(self) -> None:
        card = tk.Frame(self.dialog, highlightthickness=1, bd=0)
        self.theme.apply_to_widget(card, "card")
        try:
            card.configure(
                highlightbackground=self.theme.colors["border"],
                highlightcolor=self.theme.colors["border"],
            )
        except tk.TclError:
            pass
        card.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        for field in self.DORK_FIELDS:
            self._create_dork_row(card, field)

    def _create_dork_row(self, parent: tk.Widget, field: str) -> None:
        row = tk.Frame(parent)
        self.theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, padx=10, pady=(8, 0))

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

        reset_button = tk.Button(row, text="Reset", command=lambda ft=field: self._reset_dork_to_open(ft))
        self.theme.apply_to_widget(reset_button, "button_secondary")
        reset_button.pack(side=tk.LEFT, padx=(0, 6))

        default_button = tk.Button(row, text="Default", command=lambda ft=field: self._set_dork_default(ft))
        self.theme.apply_to_widget(default_button, "button_secondary")
        default_button.pack(side=tk.LEFT, padx=(0, 8))

        status_label = tk.Label(row, text="", font=("Arial", 11, "bold"), width=2)
        self.theme.apply_to_widget(status_label, "text")
        status_label.pack(side=tk.RIGHT)
        self.status_labels[field] = status_label

        variable.trace_add("write", lambda *_args, ft=field: self._validate_field(ft))

    def _create_button_panel(self) -> None:
        frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(frame, "main_window")
        frame.pack(fill=tk.X, padx=16, pady=(0, 14))

        left_btns = tk.Frame(frame)
        self.theme.apply_to_widget(left_btns, "main_window")
        left_btns.pack(side=tk.LEFT)

        open_dorkbook_btn = tk.Button(left_btns, text="Open Dorkbook", command=self._open_dorkbook)
        self.theme.apply_to_widget(open_dorkbook_btn, "button_secondary")
        open_dorkbook_btn.pack(side=tk.LEFT)

        btns = tk.Frame(frame)
        self.theme.apply_to_widget(btns, "main_window")
        btns.pack(side=tk.RIGHT)

        cancel_btn = tk.Button(btns, text="Cancel", command=self._on_cancel)
        self.theme.apply_to_widget(cancel_btn, "button_secondary")
        cancel_btn.pack(side=tk.LEFT, padx=(0, 10))

        save_btn = tk.Button(btns, text="Save", command=self._on_save)
        self.theme.apply_to_widget(save_btn, "button_primary")
        save_btn.pack(side=tk.LEFT)

    def _open_dorkbook(self) -> None:
        try:
            from gui.components.dorkbook_window import show_dorkbook_window

            show_dorkbook_window(
                parent=self.dialog,
                settings_manager=self.settings_manager,
                scan_query_config_path=str(self.config_path),
            )
        except Exception as exc:
            messagebox.showerror(
                "Dorkbook Unavailable",
                f"Could not open Dorkbook:\n{exc}",
                parent=self._messagebox_parent(),
            )

    def _field_var(self, field: str) -> tk.StringVar:
        if field == "smb_dork":
            if self.smb_dork_var is None:
                self.smb_dork_var = tk.StringVar(value=self.smb_dork)
            return self.smb_dork_var
        if field == "ftp_dork":
            if self.ftp_dork_var is None:
                self.ftp_dork_var = tk.StringVar(value=self.ftp_dork)
            return self.ftp_dork_var
        if self.http_dork_var is None:
            self.http_dork_var = tk.StringVar(value=self.http_dork)
        return self.http_dork_var

    def _reset_dork_to_open(self, field: str) -> None:
        variable = self._field_var(field)
        variable.set(self._open_dork_values.get(field, self.DORK_DEFAULTS[field]))

    def _set_dork_default(self, field: str) -> None:
        variable = self._field_var(field)
        variable.set(self.DORK_DEFAULTS[field])

    def _validate_field(self, field: str) -> None:
        result = validate_discovery_dork(self._field_var(field).get(), self.FIELD_LABELS[field])
        self.validation_results[field] = result
        self._update_status_label(field, result)

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
        for field in self.DORK_FIELDS:
            self._validate_field(field)

    def _current_dork_settings(self) -> Dict[str, str]:
        return {
            "smb_dork": self._field_var("smb_dork").get().strip(),
            "ftp_dork": self._field_var("ftp_dork").get().strip(),
            "http_dork": self._field_var("http_dork").get().strip(),
        }

    def _messagebox_parent(self) -> tk.Widget:
        try:
            if self.dialog is not None and int(self.dialog.winfo_exists()) == 1:
                return self.dialog
        except Exception:
            pass
        return self.parent

    def _load_runtime_config_json(self, config_path: Path, *, strict: bool = False) -> Dict[str, Any]:
        if not config_path.exists():
            return {}
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if strict:
                raise ValueError(f"Config file is not valid JSON: {config_path}") from exc
            return {}
        if isinstance(loaded, dict):
            return loaded
        if strict:
            raise ValueError(f"Config file root must be a JSON object: {config_path}")
        return {}

    def _validate_and_save(self) -> bool:
        self._validate_all_fields()
        invalid_required = [field for field in self.DORK_FIELDS if not self.validation_results[field]["valid"]]
        if invalid_required:
            details = "\n".join(
                f"- {self.FIELD_LABELS[field]}: {self.validation_results[field]['message']}"
                for field in invalid_required
            )
            messagebox.showerror(
                "Discovery Dorks Validation Failed",
                f"Please fix the following issues before saving:\n\n{details}",
                parent=self._messagebox_parent(),
            )
            return False

        try:
            config_data = self._load_runtime_config_json(self.config_path, strict=True)
            apply_discovery_dorks(config_data, self._current_dork_settings())
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")

            self.smb_dork = self._field_var("smb_dork").get().strip()
            self.ftp_dork = self._field_var("ftp_dork").get().strip()
            self.http_dork = self._field_var("http_dork").get().strip()
            self._capture_open_dork_values()
            return True
        except Exception as exc:
            messagebox.showerror(
                "Discovery Dorks Save Failed",
                f"Failed to save discovery dorks:\n{exc}",
                parent=self._messagebox_parent(),
            )
            return False

    def _close_dialog(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.dialog is not None and int(self.dialog.winfo_exists()) == 1:
                self.dialog.destroy()
        except Exception:
            pass
        callback = self._on_close_callback
        if callback is not None:
            try:
                callback(self)
            except Exception:
                pass

    def _on_save(self) -> None:
        if self._validate_and_save():
            self._close_dialog()

    def _on_cancel(self) -> None:
        self._close_dialog()

    def focus_dialog(self) -> None:
        """Bring the existing dialog instance to front."""
        try:
            self.dialog.deiconify()
            ensure_dialog_focus(self.dialog, self.parent)
        except Exception:
            pass

    def update_context(self, config_path: str, settings_manager: Optional[Any] = None) -> None:
        """Refresh config/settings context for future saves and handoffs."""
        self.config_path = _normalize_config_path(config_path)
        if settings_manager is not None:
            self.settings_manager = settings_manager

    def populate_from_dorkbook(self, *, protocol: str, query: str) -> None:
        """
        Populate one protocol field from Dorkbook without saving.

        This marks the editor dirty in-memory and keeps explicit Save/Cancel behavior.
        """
        field = _resolve_field_for_protocol(protocol)
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("Selected dork query is blank.")
        self._field_var(field).set(normalized_query)
        self.focus_dialog()


_ACTIVE_SCAN_DORK_EDITOR_DIALOG: Optional[ScanDorkEditorDialog] = None


def _dialog_instance_is_live(instance: Optional[ScanDorkEditorDialog]) -> bool:
    if instance is None:
        return False
    try:
        return bool(instance.dialog.winfo_exists())
    except Exception:
        return False


def _clear_active_dialog(instance: ScanDorkEditorDialog) -> None:
    global _ACTIVE_SCAN_DORK_EDITOR_DIALOG
    if _ACTIVE_SCAN_DORK_EDITOR_DIALOG is instance:
        _ACTIVE_SCAN_DORK_EDITOR_DIALOG = None


def _get_or_open_scan_dork_editor_dialog(
    parent: tk.Widget,
    config_path: str,
    settings_manager: Optional[Any] = None,
) -> ScanDorkEditorDialog:
    """Return a live Discovery Dorks editor, opening one when needed."""
    global _ACTIVE_SCAN_DORK_EDITOR_DIALOG
    if _dialog_instance_is_live(_ACTIVE_SCAN_DORK_EDITOR_DIALOG):
        try:
            _ACTIVE_SCAN_DORK_EDITOR_DIALOG.update_context(config_path, settings_manager=settings_manager)
        except Exception:
            # Keep existing editor alive even if the context update fails.
            pass
        _ACTIVE_SCAN_DORK_EDITOR_DIALOG.focus_dialog()
        return _ACTIVE_SCAN_DORK_EDITOR_DIALOG

    dialog = ScanDorkEditorDialog(
        parent=parent,
        config_path=config_path,
        settings_manager=settings_manager,
        on_close_callback=_clear_active_dialog,
    )
    _ACTIVE_SCAN_DORK_EDITOR_DIALOG = dialog
    return dialog


def show_scan_dork_editor_dialog(
    parent: tk.Widget,
    config_path: str,
    settings_manager: Optional[Any] = None,
) -> None:
    """Show Discovery Dorks editor as a single-instance, non-blocking dialog."""
    _get_or_open_scan_dork_editor_dialog(
        parent=parent,
        config_path=config_path,
        settings_manager=settings_manager,
    )


def populate_discovery_dork_from_dorkbook(
    parent: tk.Widget,
    *,
    config_path: str,
    protocol: str,
    query: str,
    settings_manager: Optional[Any] = None,
) -> None:
    """
    Open/focus the Discovery Dorks editor and populate one protocol query.

    Population is intentionally unsaved until the user clicks Save.
    """
    dialog = _get_or_open_scan_dork_editor_dialog(
        parent=parent,
        config_path=config_path,
        settings_manager=settings_manager,
    )
    dialog.populate_from_dorkbook(protocol=protocol, query=query)
