"""
Query budget helpers and dialog used by scan launch flows.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import tkinter as tk
from gui.utils import safe_messagebox as messagebox

from gui.utils.dialog_helpers import ensure_dialog_focus
from shared.config import load_config

_BUDGET_MIN = 1
_BUDGET_MAX = 1000
_TARGET_MIN = 1
_TARGET_MAX = 100000

_SETTING_BASE = "query_budget"
_SETTING_SMB = f"{_SETTING_BASE}.smb_max_query_credits_per_scan"
_SETTING_FTP = f"{_SETTING_BASE}.ftp_max_query_credits_per_scan"
_SETTING_HTTP = f"{_SETTING_BASE}.http_max_query_credits_per_scan"


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def resolve_config_path_from_settings(settings_manager: Any) -> Optional[str]:
    """Resolve runtime config path from settings manager when available."""
    if settings_manager is None:
        return None

    config_path = None
    try:
        config_path = settings_manager.get_setting("backend.config_path", None)
    except Exception:
        config_path = None

    if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
        try:
            config_path = settings_manager.get_smbseek_config_path()
        except Exception:
            config_path = None

    return config_path


def load_query_budget_state(settings_manager: Any = None, config_path: Optional[str] = None) -> Dict[str, int]:
    """
    Resolve effective query-budget values with settings override support.

    Precedence:
    1) GUI settings (persisted user choices)
    2) runtime config values
    3) hard defaults
    """
    shodan_cfg = load_config(config_path).get_shodan_config()
    q_limits = shodan_cfg.get("query_limits", {}) if isinstance(shodan_cfg, dict) else {}

    smb_cfg_default = _coerce_int(
        q_limits.get("smb_max_query_credits_per_scan", q_limits.get("max_query_credits_per_scan", 1)),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    ftp_cfg_default = _coerce_int(
        q_limits.get("ftp_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    http_cfg_default = _coerce_int(
        q_limits.get("http_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    usable_target = _coerce_int(
        q_limits.get("min_usable_hosts_target", 50),
        50,
        minimum=_TARGET_MIN,
        maximum=_TARGET_MAX,
    )

    smb_budget = smb_cfg_default
    ftp_budget = ftp_cfg_default
    http_budget = http_cfg_default

    if settings_manager is not None:
        try:
            smb_budget = _coerce_int(
                settings_manager.get_setting(_SETTING_SMB, smb_cfg_default),
                smb_cfg_default,
                minimum=_BUDGET_MIN,
                maximum=_BUDGET_MAX,
            )
            ftp_budget = _coerce_int(
                settings_manager.get_setting(_SETTING_FTP, ftp_cfg_default),
                ftp_cfg_default,
                minimum=_BUDGET_MIN,
                maximum=_BUDGET_MAX,
            )
            http_budget = _coerce_int(
                settings_manager.get_setting(_SETTING_HTTP, http_cfg_default),
                http_cfg_default,
                minimum=_BUDGET_MIN,
                maximum=_BUDGET_MAX,
            )
        except Exception:
            pass

    return {
        "smb_max_query_credits_per_scan": smb_budget,
        "ftp_max_query_credits_per_scan": ftp_budget,
        "http_max_query_credits_per_scan": http_budget,
        "min_usable_hosts_target": usable_target,
    }


def persist_query_budget_state(settings_manager: Any, budgets: Dict[str, Any]) -> None:
    """Persist query budgets to GUI settings storage."""
    if settings_manager is None:
        return

    smb_budget = _coerce_int(
        budgets.get("smb_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    ftp_budget = _coerce_int(
        budgets.get("ftp_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    http_budget = _coerce_int(
        budgets.get("http_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    try:
        settings_manager.set_setting(_SETTING_SMB, smb_budget)
        settings_manager.set_setting(_SETTING_FTP, ftp_budget)
        settings_manager.set_setting(_SETTING_HTTP, http_budget)
    except Exception:
        # Best effort only: scan launch flow should never fail on settings write.
        pass


class QueryBudgetDialog:
    """Small settings dialog to edit per-protocol query-credit budgets."""

    def __init__(
        self,
        parent: tk.Widget,
        theme: Any,
        settings_manager: Any = None,
        config_path: Optional[str] = None,
    ) -> None:
        self.parent = parent
        self.theme = theme
        self.settings_manager = settings_manager
        self.config_path = config_path
        self.dialog: Optional[tk.Toplevel] = None
        self.result: Optional[Dict[str, int]] = None

        state = load_query_budget_state(settings_manager=settings_manager, config_path=config_path)
        self.smb_var = tk.StringVar(value=str(state["smb_max_query_credits_per_scan"]))
        self.ftp_var = tk.StringVar(value=str(state["ftp_max_query_credits_per_scan"]))
        self.http_var = tk.StringVar(value=str(state["http_max_query_credits_per_scan"]))

    def show(self) -> Optional[Dict[str, int]]:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Query Budget")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        if self.theme:
            self.theme.apply_to_widget(self.dialog, "main_window")

        frame = tk.Frame(self.dialog)
        if self.theme:
            self.theme.apply_to_widget(frame, "main_window")
        frame.pack(padx=16, pady=16)

        heading = tk.Label(frame, text="Per-protocol Shodan Query Budget", font=("TkDefaultFont", 11, "bold"))
        if self.theme:
            self.theme.apply_to_widget(heading, "label")
        heading.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        note = tk.Label(
            frame,
            text="Credits per scan run (1 credit ~= 100 result-page records).",
            justify="left",
        )
        if self.theme:
            self.theme.apply_to_widget(note, "label")
        note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self._add_row(frame, "SMB:", self.smb_var, 2)
        self._add_row(frame, "FTP:", self.ftp_var, 3)
        self._add_row(frame, "HTTP:", self.http_var, 4)

        hint = tk.Label(
            frame,
            text=f"Allowed range: {_BUDGET_MIN}-{_BUDGET_MAX}",
            justify="left",
        )
        if self.theme:
            self.theme.apply_to_widget(hint, "label")
        hint.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 10))

        button_row = tk.Frame(frame)
        if self.theme:
            self.theme.apply_to_widget(button_row, "main_window")
        button_row.grid(row=6, column=0, columnspan=2, sticky="e")

        cancel_btn = tk.Button(button_row, text="Cancel", command=self._cancel)
        save_btn = tk.Button(button_row, text="Save", command=self._save)
        if self.theme:
            self.theme.apply_to_widget(cancel_btn, "button_secondary")
            self.theme.apply_to_widget(save_btn, "button_primary")
        cancel_btn.pack(side=tk.LEFT, padx=(0, 8))
        save_btn.pack(side=tk.LEFT)

        if self.theme:
            self.theme.apply_theme_to_application(self.dialog)

        ensure_dialog_focus(self.dialog, self.parent)
        self.dialog.protocol("WM_DELETE_WINDOW", self._cancel)
        self.parent.wait_window(self.dialog)
        return self.result

    def _add_row(self, parent: tk.Widget, label_text: str, variable: tk.StringVar, row: int) -> None:
        label = tk.Label(parent, text=label_text)
        entry = tk.Entry(parent, textvariable=variable, width=8)
        if self.theme:
            self.theme.apply_to_widget(label, "label")
            self.theme.apply_to_widget(entry, "entry")
        label.grid(row=row, column=0, sticky="w", pady=4)
        entry.grid(row=row, column=1, sticky="w", pady=4)

    def _save(self) -> None:
        assert self.dialog is not None
        try:
            smb_budget = _coerce_int(self.smb_var.get(), 1, minimum=_BUDGET_MIN, maximum=_BUDGET_MAX)
            ftp_budget = _coerce_int(self.ftp_var.get(), 1, minimum=_BUDGET_MIN, maximum=_BUDGET_MAX)
            http_budget = _coerce_int(self.http_var.get(), 1, minimum=_BUDGET_MIN, maximum=_BUDGET_MAX)
        except Exception:
            messagebox.showerror("Invalid Input", "Enter whole-number budgets.", parent=self.dialog)
            return

        self.result = {
            "smb_max_query_credits_per_scan": smb_budget,
            "ftp_max_query_credits_per_scan": ftp_budget,
            "http_max_query_credits_per_scan": http_budget,
        }
        persist_query_budget_state(self.settings_manager, self.result)
        self.dialog.destroy()

    def _cancel(self) -> None:
        assert self.dialog is not None
        self.result = None
        self.dialog.destroy()


def show_query_budget_dialog(
    parent: tk.Widget,
    theme: Any,
    settings_manager: Any = None,
    config_path: Optional[str] = None,
) -> Optional[Dict[str, int]]:
    """Show query budget dialog and return saved values when changed."""
    dialog = QueryBudgetDialog(
        parent=parent,
        theme=theme,
        settings_manager=settings_manager,
        config_path=config_path,
    )
    return dialog.show()
