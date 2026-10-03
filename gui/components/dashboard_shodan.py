"""Shodan API key and query-credit helpers for DashboardWidget.

A satellite of ``gui.dashboard.widget`` following the dashboard convention:
each function takes the dashboard instance as ``dash``, calls back into other
methods through ``dash.method()`` so instance patches intercept, and resolves
module-level names through ``_mb()`` / ``_d()`` so the frozen
``gui.components.dashboard.*`` patch paths stay valid.
"""

import json
import sys
import threading
import tkinter as tk
import webbrowser
from typing import Any, Dict, Optional

from gui.components import dashboard_scan
from gui.utils import safe_messagebox as _fallback_msgbox
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.keybindings import (
    add_shortcut_hint,
    bind_close_shortcuts,
    bind_save_shortcuts,
    bind_submit_shortcuts,
)
from gui.utils.logging_config import get_logger
from shared.config import load_config
from shared.path_service import get_paths

_logger = get_logger("dashboard")
_PATHS = get_paths()

_SHODAN_STATUS_NO_KEY = "✖ Shodan API key configured <none>"
_SHODAN_STATUS_CHECKING = "✔ Shodan API key configured <checking balance...>"
_SHODAN_STATUS_UNAVAILABLE = "✔ Shodan API key configured <balance unavailable>"


def _format_shodan_status_with_credits(credits: str) -> str:
    value = str(credits or "").strip()
    if not value:
        return _SHODAN_STATUS_UNAVAILABLE
    return f"✔ Shodan API key configured <query credits: {value}>"


def _w(name: str):
    """Resolve a name from gui.dashboard.widget at call-time.

    Some tests patch gui.dashboard.widget.<name> rather than the shim. The
    widget imports this module, so the lookup goes through sys.modules rather
    than an import, which would be a cycle.
    """
    mod = sys.modules.get("gui.dashboard.widget")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return globals()[name]


def _mb():
    """Return messagebox from gui.components.dashboard's namespace.

    Tests patch gui.components.dashboard.messagebox. Calling through this
    helper means the patched object is used at call-time, preserving all
    frozen patch paths.
    Falls back to the real safe_messagebox if dashboard is not yet loaded.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _d(name: str) -> Any:
    """Resolve a name from gui.components.dashboard at call-time.

    Tests patch gui.components.dashboard.<name>. Using this helper ensures
    the patched binding is used rather than a cached import-time reference.
    """
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None:
        return getattr(mod, name)
    raise RuntimeError(
        f"gui.components.dashboard not yet loaded (looking for {name!r})"
    )


def ensure_shodan_api_key_for_scan(dash, scan_options: Dict[str, Any]) -> bool:
    """
    Ensure scans have a persisted Shodan API key before launch.

    If config key is missing:
    - Use api_key_override when provided (persist and continue), or
    - Prompt user for key (persist; abort when cancelled/failed).
    """
    return dashboard_scan.ensure_shodan_api_key_for_scan(dash, scan_options)

def prompt_for_shodan_api_key(dash) -> Optional[str]:
    """
    Prompt the user to enter a Shodan API key.

    Returns:
        Trimmed API key string when saved, or None when cancelled.
    """
    dialog = tk.Toplevel(dash.parent)
    dialog.title("Shodan API Key Required")
    dialog.geometry("540x220")
    dialog.resizable(False, False)
    dialog.transient(dash.parent)
    dialog.grab_set()
    dash.theme.apply_to_widget(dialog, "main_window")

    container = tk.Frame(dialog)
    dash.theme.apply_to_widget(container, "main_window")
    container.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)

    title_label = tk.Label(container, text="Shodan API Key Required", font=("TkDefaultFont", 11, "bold"))
    dash.theme.apply_to_widget(title_label, "label")
    title_label.pack(anchor="w")

    helper = tk.Label(
        container,
        text="A Shodan API key is required to start discovery scans. Enter your key to continue.",
        justify="left",
        wraplength=500,
    )
    dash.theme.apply_to_widget(helper, "label")
    helper.pack(anchor="w", pady=(8, 10))

    key_row = tk.Frame(container)
    dash.theme.apply_to_widget(key_row, "main_window")
    key_row.pack(fill=tk.X)

    key_label = tk.Label(key_row, text="API Key:")
    dash.theme.apply_to_widget(key_label, "label")
    key_label.pack(side=tk.LEFT, padx=(0, 8))

    key_var = tk.StringVar()
    key_entry = tk.Entry(key_row, textvariable=key_var, width=54)
    dash.theme.apply_to_widget(key_entry, "entry")
    key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    link_row = tk.Frame(container)
    dash.theme.apply_to_widget(link_row, "main_window")
    link_row.pack(fill=tk.X, pady=(10, 0))

    need_label = tk.Label(link_row, text="Need a key?")
    dash.theme.apply_to_widget(need_label, "label")
    need_label.pack(side=tk.LEFT, padx=(0, 6))

    link_label = tk.Label(
        link_row,
        text="https://account.shodan.io/register",
        cursor="hand2",
        font=("TkDefaultFont", 9, "underline"),
        fg=dash.theme.colors.get("accent", "#4da3ff"),
    )
    dash.theme.apply_to_widget(link_label, "label")
    link_label.configure(fg=dash.theme.colors.get("accent", "#4da3ff"))
    link_label.pack(side=tk.LEFT)
    link_label.bind("<Button-1>", lambda _e: webbrowser.open("https://account.shodan.io/register"))

    result: Dict[str, Optional[str]] = {"api_key": None}

    def _cancel() -> None:
        result["api_key"] = None
        dialog.destroy()

    def _save() -> None:
        key_value = key_var.get().strip()
        if not key_value:
            _mb().showerror("Missing API Key", "Please enter a Shodan API key.", parent=dialog)
            return
        result["api_key"] = key_value
        dialog.destroy()

    btn_row = tk.Frame(container)
    dash.theme.apply_to_widget(btn_row, "main_window")
    btn_row.pack(fill=tk.X, pady=(14, 0))

    cancel_btn = tk.Button(btn_row, text="Cancel", command=_cancel)
    dash.theme.apply_to_widget(cancel_btn, "button_secondary")
    cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

    save_btn = tk.Button(btn_row, text="Save & Continue", command=_save)
    dash.theme.apply_to_widget(save_btn, "button_primary")
    save_btn.pack(side=tk.RIGHT)
    shortcut_hint = add_shortcut_hint(
        container,
        dash.theme,
        "Enter save and continue  •  Esc cancel  •  Ctrl/Cmd+S save  •  Esc/Ctrl+W/Cmd+W close",
    )
    shortcut_hint.configure(wraplength=480)
    bind_submit_shortcuts(dialog, _save)
    bind_save_shortcuts(dialog, _save)
    bind_close_shortcuts(dialog, _cancel)
    key_entry.focus_set()

    dialog.update_idletasks()
    dialog.geometry(f"540x{max(220, dialog.winfo_reqheight())}")
    ensure_dialog_focus(dialog, dash.parent)
    dialog.protocol("WM_DELETE_WINDOW", _cancel)
    dash.parent.wait_window(dialog)
    return result["api_key"]

def read_shodan_api_key_from_config(dash) -> str:
    """Return shodan.api_key from runtime config, or empty string when absent/unreadable."""
    config_path = dash._resolve_active_config_path()
    try:
        if (
            config_path is not None
            and config_path.resolve(strict=False) != _PATHS.config_file.resolve(strict=False)
        ):
            if not config_path.exists():
                return ""
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config_data, dict):
                return ""
            shodan_cfg = config_data.get("shodan", {})
            if not isinstance(shodan_cfg, dict):
                return ""
            return str(shodan_cfg.get("api_key", "") or "").strip()

        cfg = load_config()
        shodan_cfg = cfg.get("shodan", default={}) or {}
        if not isinstance(shodan_cfg, dict):
            return ""
        return str(shodan_cfg.get("api_key", "") or "").strip()
    except Exception as exc:
        _logger.warning("Could not read Shodan API key from config: %s", exc)
        return ""

def persist_shodan_api_key_to_config(dash, api_key: str) -> bool:
    """Write shodan.api_key through owner-scoped runtime config persistence."""
    key = str(api_key or "").strip()
    if not key:
        return False

    config_path = dash._resolve_active_config_path()
    try:
        if (
            config_path is not None
            and config_path.resolve(strict=False) != _PATHS.config_file.resolve(strict=False)
        ):
            config_data: Dict[str, Any] = {}
            if config_path.exists():
                config_data = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(config_data, dict):
                    config_data = {}

            shodan_cfg = config_data.get("shodan")
            if not isinstance(shodan_cfg, dict):
                shodan_cfg = {}
                config_data["shodan"] = shodan_cfg
            shodan_cfg["api_key"] = key

            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            return True

        cfg = load_config()
        shodan_cfg = cfg.get("shodan", default={}) or {}
        if not isinstance(shodan_cfg, dict):
            shodan_cfg = {}
        shodan_cfg["api_key"] = key
        return cfg.set_section("shodan", shodan_cfg)
    except Exception as exc:
        _logger.error("Failed to persist Shodan API key to config: %s", exc)
        return False

def fetch_shodan_query_credits(dash, api_key: str) -> Optional[str]:
    """Return query credits display value for one API key, or None on failure."""
    try:
        import shodan
    except Exception:
        return None

    try:
        info = shodan.Shodan(api_key).info()
    except Exception:
        return None

    if not isinstance(info, dict):
        return None

    credits = info.get("query_credits")
    if isinstance(credits, bool):
        return None
    if isinstance(credits, int):
        return str(credits)
    if isinstance(credits, float):
        return str(int(credits))
    if isinstance(credits, str):
        value = credits.strip()
        return value or None
    return None

def start_shodan_balance_refresh(dash, refresh_id: int, api_key: str) -> None:
    """Launch background Shodan query-credit fetch."""
    threading.Thread(
        target=dash._run_shodan_balance_refresh_worker,
        args=(refresh_id, api_key),
        name="dashboard-shodan-balance",
        daemon=True,
    ).start()

def run_shodan_balance_refresh_worker(dash, refresh_id: int, api_key: str) -> None:
    """Resolve query credits in worker thread and hand off UI update to Tk thread."""
    credits = dash._fetch_shodan_query_credits(api_key)
    try:
        dash.parent.after(
            0,
            lambda: dash._finish_shodan_balance_refresh(refresh_id, credits),
        )
    except Exception:
        # Parent likely torn down while worker finished; safe to drop.
        pass

def finish_shodan_balance_refresh(dash, refresh_id: int, credits: Optional[str]) -> None:
    """Apply worker result if it matches latest refresh generation."""
    if refresh_id != dash._shodan_balance_refresh_generation:
        return
    if credits is None:
        dash.shodan_status_text.set(_SHODAN_STATUS_UNAVAILABLE)
        return
    dash.shodan_status_text.set(_format_shodan_status_with_credits(credits))

def refresh_shodan_status_display(dash) -> None:
    """Set immediate Shodan key state and start async balance lookup when configured."""
    dash._shodan_balance_refresh_generation += 1
    refresh_id = dash._shodan_balance_refresh_generation

    api_key = dash._read_shodan_api_key_from_config()
    if not api_key:
        dash.shodan_status_text.set(_SHODAN_STATUS_NO_KEY)
        return

    dash.shodan_status_text.set(_SHODAN_STATUS_CHECKING)
    dash._start_shodan_balance_refresh(refresh_id, api_key)
