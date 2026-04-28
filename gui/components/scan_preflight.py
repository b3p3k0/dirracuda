"""
Scan pre-flight controller and configuration dialogs.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from gui.utils import safe_messagebox as messagebox
from pathlib import Path
from typing import Optional, Dict, Any, List
import sys
import os

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components.batch_extract_dialog import BatchExtractSettingsDialog
from gui.components.query_budget_dialog import (
    load_query_budget_state,
    resolve_config_path_from_settings,
)
from shared.config import load_config


def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
    """Coerce a value to an integer with minimum/default guards."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def _resolve_protocols(scan_options: Dict[str, Any]) -> List[str]:
    """Return normalized protocol list for preflight estimation."""
    raw_protocols = scan_options.get("protocols")
    if isinstance(raw_protocols, list) and raw_protocols:
        protocols = [str(item).strip().lower() for item in raw_protocols if str(item).strip()]
        if protocols:
            return protocols
    # Legacy SMB dialog path does not populate explicit protocol list.
    return ["smb"]


def _estimate_query_cost_details(scan_options: Dict[str, Any], budget_state: Dict[str, int]) -> Dict[str, Any]:
    """
    Build preflight estimate lines for Shodan query-credit usage.

    Estimates are intentionally approximate and settings-based so users get
    clear budget visibility before launch.
    """
    max_results = _coerce_int(scan_options.get("max_shodan_results"), 0)
    has_explicit_max = max_results > 0
    protocols = _resolve_protocols(scan_options)

    smb_credit_budget = _coerce_int(
        scan_options.get("smb_max_query_credits_per_scan", budget_state.get("smb_max_query_credits_per_scan")),
        1,
    )
    ftp_credit_budget = _coerce_int(
        scan_options.get("ftp_max_query_credits_per_scan", budget_state.get("ftp_max_query_credits_per_scan")),
        1,
    )
    http_credit_budget = _coerce_int(
        scan_options.get("http_max_query_credits_per_scan", budget_state.get("http_max_query_credits_per_scan")),
        1,
    )
    total_min = 0
    total_max = 0

    for protocol in protocols:
        if protocol == "smb":
            effective_limit = (
                min(max_results, smb_credit_budget * 100)
                if has_explicit_max
                else smb_credit_budget * 100
            )
            smb_credit_cap = max(1, (effective_limit + 99) // 100)
            if smb_credit_budget > 1:
                total_min += 1
                total_max += smb_credit_cap
            else:
                total_min += smb_credit_cap
                total_max += smb_credit_cap
        elif protocol in {"ftp", "http"}:
            budget = ftp_credit_budget if protocol == "ftp" else http_credit_budget
            effective_limit = min(max_results, budget * 100) if has_explicit_max else budget * 100
            proto_credits = max(1, (effective_limit + 99) // 100)
            total_min += proto_credits
            total_max += proto_credits

    if total_min == total_max:
        total_line = f"Estimated total query cost: ~{total_max} API query credit(s)"
    else:
        total_line = f"Estimated total query cost: ~{total_min}..{total_max} API query credits"

    return {
        "total_line": total_line,
        "total_min": total_min,
        "total_max": total_max,
    }


def _resolve_shodan_api_key(scan_options: Dict[str, Any], shodan_cfg: Dict[str, Any]) -> Optional[str]:
    """Resolve API key with override precedence for live-balance checks."""
    override = str(scan_options.get("api_key_override") or "").strip()
    if override:
        return override
    configured = ""
    if isinstance(shodan_cfg, dict):
        configured = str(shodan_cfg.get("api_key") or "").strip()
    return configured or None


def _fetch_shodan_query_credits(api_key: str) -> Optional[int]:
    """Return live Shodan query-credit balance, or None when unavailable."""
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
        return credits
    if isinstance(credits, float):
        return int(credits)
    if isinstance(credits, str):
        text = credits.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


class ProbeConfigDialog:
    """Collect probe batch settings before a scan begins."""

    def __init__(self, parent: tk.Toplevel, theme, settings_manager) -> None:
        self.parent = parent
        self.theme = theme
        self.settings = settings_manager
        self.dialog: Optional[tk.Toplevel] = None
        self.result: Optional[Dict[str, Any]] = None

        defaults = {
            "workers": 3,
            "max_dirs": 3,
            "max_files": 5,
            "timeout": 10,
            "max_depth": 1,
        }
        if self.settings:
            try:
                defaults["workers"] = int(self.settings.get_setting('probe.batch_max_workers', defaults['workers']))
                defaults["max_dirs"] = int(self.settings.get_setting('probe.max_directories_per_share', defaults['max_dirs']))
                defaults["max_files"] = int(self.settings.get_setting('probe.max_files_per_directory', defaults['max_files']))
                defaults["timeout"] = int(self.settings.get_setting('probe.share_timeout_seconds', defaults['timeout']))
                defaults["max_depth"] = int(self.settings.get_setting('probe.max_depth_levels', defaults['max_depth']))
            except Exception:
                pass

        self.worker_var = tk.IntVar(value=defaults['workers'])
        self.max_dirs_var = tk.IntVar(value=defaults['max_dirs'])
        self.max_files_var = tk.IntVar(value=defaults['max_files'])
        self.timeout_var = tk.IntVar(value=defaults['timeout'])
        self.max_depth_var = tk.IntVar(value=min(3, max(1, defaults['max_depth'])))

    def show(self) -> Dict[str, Any]:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Configure Bulk Probe")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        if self.theme:
            self.theme.apply_to_widget(self.dialog, "main_window")

        frame = tk.Frame(self.dialog)
        if self.theme:
            self.theme.apply_to_widget(frame, "main_window")
        frame.pack(padx=20, pady=20)

        self._add_entry(frame, "Worker threads (max 8):", self.worker_var, 0)
        self._add_entry(frame, "Max directories per share:", self.max_dirs_var, 1)
        self._add_entry(frame, "Max files per directory:", self.max_files_var, 2)
        self._add_entry(frame, "Share timeout (seconds):", self.timeout_var, 3)
        self._add_entry(frame, "Max probe depth (1-3):", self.max_depth_var, 4)

        btn_frame = tk.Frame(frame)
        if self.theme:
            self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.grid(row=5, column=0, columnspan=2, pady=(15, 0))
        save_btn = tk.Button(btn_frame, text="Save & Continue", command=self._save)
        disable_btn = tk.Button(btn_frame, text="Disable Probe", command=self._disable)
        abort_btn = tk.Button(btn_frame, text="Abort Scan", command=self._abort)
        for btn in (save_btn, disable_btn, abort_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=5)

        if self.theme:
            self.theme.apply_theme_to_application(self.dialog)

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

        self.dialog.protocol("WM_DELETE_WINDOW", self._abort)
        self.parent.wait_window(self.dialog)
        return self.result or {"status": "abort"}

    def _add_entry(self, parent, label, var, row):
        label_widget = tk.Label(parent, text=label)
        entry_widget = tk.Entry(parent, textvariable=var, width=10)
        if self.theme:
            self.theme.apply_to_widget(label_widget, "label")
            self.theme.apply_to_widget(entry_widget, "entry")
        label_widget.grid(row=row, column=0, sticky="w", pady=5)
        entry_widget.grid(row=row, column=1, sticky="w", pady=5)

    def _save(self):
        try:
            data = {
                "status": "ok",
                "workers": max(1, min(8, int(self.worker_var.get()))),
                "max_dirs": max(1, int(self.max_dirs_var.get())),
                "max_files": max(1, int(self.max_files_var.get())),
                "timeout": max(1, int(self.timeout_var.get())),
                "max_depth": min(3, max(1, int(self.max_depth_var.get()))),
            }
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid Input", "Please enter numeric values for probe limits.", parent=self.dialog)
            return

        if self.settings:
            try:
                self.settings.set_setting('probe.batch_max_workers', data['workers'])
                self.settings.set_setting('probe.max_directories_per_share', data['max_dirs'])
                self.settings.set_setting('probe.max_files_per_directory', data['max_files'])
                self.settings.set_setting('probe.share_timeout_seconds', data['timeout'])
                self.settings.set_setting('probe.max_depth_levels', data['max_depth'])
            except Exception:
                pass

        self.result = data
        self.dialog.destroy()

    def _disable(self):
        self.result = {"status": "disable"}
        self.dialog.destroy()

    def _abort(self):
        self.result = {"status": "abort"}
        self.dialog.destroy()


# ExtractConfigDialog removed - replaced by BatchExtractSettingsDialog


class SummaryDialog:
    def __init__(self, parent: tk.Toplevel, theme, lines: List[str], base_line: str) -> None:
        self.parent = parent
        self.theme = theme
        self.lines = lines
        self.base_line = base_line
        self.dialog: Optional[tk.Toplevel] = None
        self.result: Optional[bool] = None

    def show(self) -> bool:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Review Scan Options")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        if self.theme:
            self.theme.apply_to_widget(self.dialog, "main_window")

        frame = tk.Frame(self.dialog)
        if self.theme:
            self.theme.apply_to_widget(frame, "main_window")
        frame.pack(padx=20, pady=20)

        heading_label = tk.Label(frame, text="Confirm settings before launching the scan", font=("TkDefaultFont", 12, "bold"))
        if self.theme:
            self.theme.apply_to_widget(heading_label, "label")
        heading_label.pack(anchor="w")

        summary_text = tk.Text(frame, width=80, height=12, state="disabled")
        if self.theme:
            self.theme.apply_to_widget(summary_text, "text_area")
        summary_text.pack(pady=(10, 0))
        summary_text.config(state="normal")
        summary_text.insert("end", f"Base scan: {self.base_line}\n")
        for line in self.lines:
            summary_text.insert("end", f"- {line}\n")
        summary_text.config(state="disabled")

        btn_frame = tk.Frame(frame)
        if self.theme:
            self.theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(pady=(15, 0))
        back_btn = tk.Button(btn_frame, text="Back to Scan", command=self._back)
        start_btn = tk.Button(btn_frame, text="Start Scan", command=self._start)
        if self.theme:
            self.theme.apply_to_widget(back_btn, "button_secondary")
            self.theme.apply_to_widget(start_btn, "button_primary")
        back_btn.pack(side=tk.LEFT, padx=5)
        start_btn.pack(side=tk.LEFT, padx=5)

        if self.theme:
            self.theme.apply_theme_to_application(self.dialog)

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

        self.dialog.protocol("WM_DELETE_WINDOW", self._back)
        self.parent.wait_window(self.dialog)
        return bool(self.result)

    def _back(self):
        self.result = False
        self.dialog.destroy()

    def _start(self):
        self.result = True
        self.dialog.destroy()


class ScanPreflightController:
    def __init__(self, parent: tk.Toplevel, theme, settings_manager, scan_options: Dict[str, Any], scan_description: str) -> None:
        self.parent = parent
        self.theme = theme
        self.settings = settings_manager
        self.scan_options = scan_options
        self.scan_description = scan_description
        self.summary_lines: List[str] = []

    def run(self) -> Optional[Dict[str, Any]]:
        probe_enabled = self.scan_options.get('bulk_probe_enabled', False)
        extract_enabled = self.scan_options.get('bulk_extract_enabled', False)
        self.skip_indicator_extract = bool(self.scan_options.get('bulk_extract_skip_indicators', True))
        rce_enabled = bool(self.scan_options.get('rce_enabled', False))

        config_path = resolve_config_path_from_settings(self.settings)
        shodan_cfg = load_config(config_path).get_shodan_config()
        budget_state = load_query_budget_state(settings_manager=self.settings, config_path=config_path)
        cost_details = _estimate_query_cost_details(self.scan_options, budget_state)

        api_key = _resolve_shodan_api_key(self.scan_options, shodan_cfg)
        credits = _fetch_shodan_query_credits(api_key) if api_key else None

        if credits is None:
            self.summary_lines.append("Shodan balance: not available at this time")
            self.summary_lines.append("Check balance: https://developer.shodan.io/dashboard")
        else:
            self.summary_lines.append(f"Shodan balance: {credits} credits")
            self.summary_lines.append(cost_details["total_line"])

        if probe_enabled:
            outcome = ProbeConfigDialog(self.parent, self.theme, self.settings).show()
            status = outcome.get('status')
            if status == 'abort':
                return None
            if status == 'disable':
                self.scan_options['bulk_probe_enabled'] = False
                self.summary_lines.append('Probe disabled for this scan')
            else:
                rce_enabled_for_probe = bool(self.scan_options.get('rce_enabled', False))
                self.summary_lines.append(
                    f"Probe enabled • workers {outcome['workers']} • dirs {outcome['max_dirs']} • files {outcome['max_files']} • timeout {outcome['timeout']}s • depth {outcome['max_depth']} • RCE {'On' if rce_enabled_for_probe else 'Off'}"
                )
                self.scan_options['bulk_probe_enabled'] = True
        if extract_enabled:
            # Get config path from settings manager
            config_path = None
            if self.settings:
                config_path = self.settings.get_setting('backend.config_path', None)
                if not config_path and hasattr(self.settings, "get_smbseek_config_path"):
                    config_path = self.settings.get_smbseek_config_path()

            outcome = BatchExtractSettingsDialog(
                parent=self.parent,
                theme=self.theme,
                settings_manager=self.settings,
                config_path=config_path,
                mode="preflight"
            ).show()
            status = outcome.get('status') if outcome else 'abort'
            if status == 'abort':
                return None
            if status == 'disable':
                self.scan_options['bulk_extract_enabled'] = False
                self.summary_lines.append('Extract disabled for this scan')
            else:
                self.summary_lines.append(
                    f"Extract enabled • workers {outcome['workers']} • path {outcome['path']} • file {outcome['max_file']}MB • total {outcome['max_total']}MB • time {outcome['max_time']}s • files {outcome['max_files']}"
                )
                self.scan_options['bulk_extract_enabled'] = True

        if self.scan_options.get('rce_enabled') and not self.scan_options.get('bulk_probe_enabled'):
            self.scan_options['rce_enabled'] = False
            self.summary_lines.append('RCE disabled (requires probe)')
        elif self.scan_options.get('rce_enabled'):
            self.summary_lines.append('RCE analysis will run with probe results')

        if not any((probe_enabled, extract_enabled, rce_enabled)):
            self.summary_lines.append('No optional post-scan actions selected')

        ok = SummaryDialog(self.parent, self.theme, self.summary_lines, self.scan_description).show()
        if not ok:
            return None
        return self.scan_options


def run_preflight(parent: tk.Toplevel, theme, settings_manager, scan_options: Dict[str, Any], scan_description: str) -> Optional[Dict[str, Any]]:
    controller = ScanPreflightController(parent, theme, settings_manager, scan_options, scan_description)
    return controller.run()
