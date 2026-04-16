"""
Server Detail Popup Operations

Handles server detail popup windows and exploration functionality.
Self-contained UI components with passed data dependencies.
"""

import tkinter as tk
from tkinter import ttk, filedialog
from gui.utils import safe_messagebox as messagebox
import subprocess
import platform
import threading
import os
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Sequence, Tuple

from gui.utils import (
    probe_cache,
    probe_runner,
    probe_patterns,
    extract_runner,
)
from gui.utils.probe_cache_dispatch import (
    load_probe_result_for_host,
    get_probe_snapshot_path_for_host,
    dispatch_probe_run,
)
from gui.utils.probe_snapshot_summary import (
    summarize_probe_snapshot,
)
from gui.utils.database_access import DatabaseReader
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components.batch_extract_dialog import BatchExtractSettingsDialog
from shared.quarantine import create_quarantine_dir


def show_server_detail_popup(parent_window, server_data, theme, settings_manager=None,
                             probe_status_callback=None, indicator_patterns: Optional[Sequence[probe_patterns.IndicatorPattern]] = None,
                             probe_callback=None, extract_callback=None, browse_callback=None,
                             rce_status_callback=None):
    """
    Show server detail popup window.

    Args:
        parent_window: Parent window for transient behavior
        server_data: Server dictionary with all fields
        theme: Theme object for styling
        probe_callback/extract_callback/browse_callback: Optional external launchers to ensure consistent workflows.
    """
    # Create popup window
    detail_window = tk.Toplevel(parent_window)
    detail_window.title(f"Server Details - {server_data.get('ip_address', 'Unknown')}")
    detail_window.geometry("700x700")
    detail_window.transient(parent_window)

    theme.apply_to_widget(detail_window, "main_window")

    # Create scrollable text area
    text_frame = tk.Frame(detail_window)
    text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    text_widget = tk.Text(text_frame, wrap=tk.WORD, state=tk.DISABLED)
    if theme:
        theme.apply_to_widget(text_widget, "text_area")
    scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
    text_widget.configure(yscrollcommand=scrollbar.set)

    text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Add mousewheel scrolling support
    def on_mousewheel(event):
        text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")

    text_widget.bind("<MouseWheel>", on_mousewheel)  # Windows/MacOS
    text_widget.bind("<Button-4>", lambda e: text_widget.yview_scroll(-1, "units"))  # Linux scroll up
    text_widget.bind("<Button-5>", lambda e: text_widget.yview_scroll(1, "units"))   # Linux scroll down

    # Initial render (includes cached probe data if available)
    ip_address = server_data.get('ip_address', 'Unknown')
    host_type = server_data.get("host_type", "S")
    cached_probe = load_probe_result_for_host(
        ip_address,
        host_type,
        port=(server_data.get("port") if str(host_type).upper() == "H" else None),
    )
    if cached_probe and indicator_patterns:
        probe_patterns.attach_indicator_analysis(cached_probe, indicator_patterns)
    _render_server_details(text_widget, server_data, cached_probe)

    # Status label for probe feedback
    status_var = tk.StringVar(value="")
    status_label = theme.create_styled_label(
        detail_window,
        "",
        "small",
        fg=theme.colors["text_secondary"]
    )
    status_label.configure(textvariable=status_var)
    status_label.pack(pady=(0, 5))

    # Button frame for Explore and Close buttons
    button_frame = tk.Frame(detail_window)
    theme.apply_to_widget(button_frame, "main_window")

    probe_state = {
        "running": False,
        "latest": cached_probe,
        "indicator_patterns": indicator_patterns or []
    }

    extract_state = {
        "running": False
    }

    # Notes editor
    notes_frame = tk.Frame(detail_window)
    if theme:
        theme.apply_to_widget(notes_frame, "main_window")
    notes_frame.pack(fill=tk.X, padx=10, pady=(5, 5))
    if theme:
        notes_label = theme.create_styled_label(notes_frame, "Notes:", "body")
    else:
        notes_label = tk.Label(notes_frame, text="Notes:")
    notes_label.pack(anchor="w")
    notes_text = tk.Text(notes_frame, height=3, wrap="word")
    current_notes = server_data.get("notes", "") or ""
    notes_text.insert("1.0", current_notes)
    notes_text.pack(fill=tk.X, expand=True)
    if theme:
        theme.apply_to_widget(notes_text, "text_area")

    def _persist_notes() -> None:
        """Persist notes without user interaction."""
        new_notes = notes_text.get("1.0", tk.END).strip()
        if new_notes == server_data.get("notes", ""):
            return
        try:
            if settings_manager:
                try:
                    db_reader = DatabaseReader(settings_manager.get_database_path())
                    db_reader.upsert_user_flags_for_host(
                        server_data.get("ip_address", ""),
                        server_data.get("host_type", "S"),
                        notes=new_notes,
                        protocol_server_id=server_data.get("protocol_server_id"),
                        port=server_data.get("port"),
                    )
                except Exception:
                    pass
            server_data["notes"] = new_notes
        except Exception:
            # Silently ignore; we don't want to block dialog close
            pass

    # Pack button frame after notes section
    button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

    def _open_browse_window() -> None:
        if host_type == "H":
            _db = None
            if settings_manager:
                try:
                    _db = DatabaseReader(settings_manager.get_database_path())
                except Exception:
                    pass
            row_psid = server_data.get("protocol_server_id")
            row_port = server_data.get("port")
            detail = (
                _db.get_http_server_detail(
                    ip_address,
                    protocol_server_id=row_psid,
                    port=row_port,
                )
                if _db else None
            )
            try:
                port = int((detail or {}).get("port") or row_port or 80)
            except (TypeError, ValueError):
                port = 80
            scheme = (detail or {}).get("scheme") or ("https" if port == 443 else "http")
            config_path = None
            if settings_manager:
                config_path = settings_manager.get_setting('backend.config_path', None)
                if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
                    config_path = settings_manager.get_smbseek_config_path()
            from gui.components.unified_browser_window import open_ftp_http_browser
            open_ftp_http_browser(
                "H",
                parent=detail_window,
                ip_address=ip_address,
                port=port,
                scheme=scheme,
                db_reader=_db,
                config_path=config_path,
                theme=theme,
                settings_manager=settings_manager,
                # banner intentionally omitted: current details path does not pass banner
                # to HttpBrowserWindow; factory default (None) preserves that behavior.
            )
            return

        def _clean_share_name(name: str) -> str:
            return name.strip().strip("\\/").strip()

        raw_shares = _parse_accessible_shares(server_data.get('accessible_shares_list', ''))
        cleaned_shares = []
        seen = set()
        for name in raw_shares:
            cleaned = _clean_share_name(name)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            cleaned_shares.append(cleaned)
        accessible_shares = cleaned_shares

        config_path = None
        if settings_manager:
            config_path = settings_manager.get_setting('backend.config_path', None)
            if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
                config_path = settings_manager.get_smbseek_config_path()

        # Resolve a DatabaseReader for Shodan banner lookup (mirrors HTTP branch pattern).
        # _open_browse_window is the fallback path; primary route is browse_callback.
        _db = None
        if settings_manager:
            try:
                _db = DatabaseReader(settings_manager.get_database_path())
            except Exception:
                pass
        from gui.components.unified_browser_window import open_smb_browser
        open_smb_browser(
            parent=detail_window,
            ip_address=server_data.get("ip_address", ""),
            shares=accessible_shares,
            auth_method=server_data.get("auth_method", ""),
            config_path=config_path,
            db_reader=_db,
            theme=theme,
            settings_manager=settings_manager,
            on_extracted=(None if not settings_manager
                          else getattr(parent_window, "_handle_extracted_update", None)),
        )

    # Close button
    close_button = tk.Button(
        button_frame,
        text="Close",
        command=lambda: _persist_notes() or detail_window.destroy()
    )
    theme.apply_to_widget(close_button, "button_primary")
    close_button.pack(side=tk.RIGHT)

    # Ensure notes persist when the window is closed via the window manager
    def _on_close() -> None:
        _persist_notes()
        detail_window.destroy()

    detail_window.protocol("WM_DELETE_WINDOW", _on_close)

    # Ensure window is fully rendered before setting grab
    detail_window.update_idletasks()
    if theme:
        theme.apply_theme_to_application(detail_window)
    detail_window.grab_set()

    # Ensure dialog appears on top and gains focus (critical for VMs)
    ensure_dialog_focus(detail_window, parent_window)


def _invoke_callback_or_warn(cb, server_data: Dict[str, Any], parent_window: tk.Toplevel, action_label: str) -> None:
    """
    Invoke provided callback with server_data; warn if callback is missing.

    Ensures detail popups always route actions through shared workflows
    (batch pop-out dialogs) instead of legacy in-dialog handlers.
    """
    if callable(cb):
        cb(server_data)
    else:
        messagebox.showwarning(
            f"{action_label} Unavailable",
            f"{action_label} workflow is not available in this view.",
            parent=parent_window
        )


def _format_server_details(server: Dict[str, Any], probe_section: Optional[str] = None) -> str:
    """Format server details for display. Renders protocol-specific sections for S vs F rows."""
    host_type = server.get('host_type', 'S')
    protocol_label = {"S": "SMB", "F": "FTP", "H": "HTTP"}.get(host_type, "Unknown")

    # ---- SMB share access section ----
    if host_type == "S":
        accessible_list = server.get('accessible_shares_list', '')
        accessible_count = server.get('accessible_shares', 0)
        total_shares = server.get('total_shares', accessible_count)

        denied_list = server.get('denied_shares_list', []) or []
        denied_count = server.get('denied_shares_count', 0)
        denied_display_limit = 20

        if accessible_list and accessible_list.strip():
            shares = [share.strip() for share in accessible_list.split(',') if share.strip()]
            share_list_text = '\n'.join([f'   • {share}' for share in shares]) if shares else '   • None accessible'
        else:
            share_list_text = '   • None accessible'

        friendly_status = {
            None: "Access denied",
            "NT_STATUS_ACCESS_DENIED": "Access denied",
            "NT_STATUS_LOGON_FAILURE": "Logon failed",
            "NT_STATUS_BAD_NETWORK_NAME": "Not found",
            "NT_STATUS_ACCOUNT_LOCKED_OUT": "Account locked",
            "NT_STATUS_CONNECTION_RESET": "Connection reset",
            "TIMEOUT": "Timeout",
            "ERROR": "Error"
        }

        denied_lines = []
        for idx, item in enumerate(denied_list):
            if idx >= denied_display_limit:
                break
            status = friendly_status.get(item.get('auth_status')) or "Error"
            share_name = item.get('share_name', 'Unknown')
            denied_lines.append(f"   • {share_name} — {status}")

        if not denied_lines:
            denied_text = '   • None'
        else:
            more = denied_count - len(denied_lines)
            denied_text = '\n'.join(denied_lines)
            if more > 0:
                denied_text += f"\n   … +{more} more not shown"

        access_section = f"""📁 Share Access:
   Total Shares Discovered: {total_shares}
   Accessible Shares: {accessible_count}

   Accessible Share List:
{share_list_text}

🚫 Denied Shares:
{denied_text}"""
    elif host_type == "H":
        # HTTP row — show connection/access info
        port = server.get('port') or "80"
        banner = server.get('banner') or "N/A"
        dirs = server.get('accessible_dirs_count') or server.get('accessible_shares', 0)
        files = server.get('accessible_files_count') or 0
        access_section = f"""🌐 HTTP Access:
   Port: {port}
   Title/Banner: {banner}
   Directories: {dirs}
   Files: {files}"""
    else:
        # FTP row — show connection/access info instead of share tables
        anon = server.get('anon_accessible')
        anon_label = "Yes" if anon else ("No" if anon is not None else "Unknown")
        port = server.get('port') or "21"
        banner = server.get('banner') or "N/A"
        access_section = f"""📁 FTP Access:
   Anonymous: {anon_label}
   Port: {port}
   Banner: {banner}"""

    details = f"""📋 Server Details

🖥 Basic Information:
   Protocol: {protocol_label}
   IP Address: {server.get('ip_address', 'Unknown')}
   Country: {server.get('country', 'Unknown')} ({server.get('country_code', 'Unknown')})
   Authentication: {server.get('auth_method', 'Unknown')}

📊 Scan Information:
   First Seen: {server.get('first_seen', 'Unknown')}
   Last Seen: {server.get('last_seen', 'Unknown')}
   Scan Count: {server.get('scan_count', 0)}
   Status: {server.get('status', 'Unknown')}

{access_section}

{probe_section or '🔍 Probe:\n   No probe has been run for this host yet.\n'}

📝 Additional Notes:
   This server was discovered during scanning. Protocol-specific access
   and authentication details are shown above.

   For detailed vulnerability information and remediation steps,
   use the Vulnerability Report window.

   For complete share enumeration data, check the backend database
   or export the detailed scan results.
    """

    return details


def _render_server_details(
    text_widget: tk.Text,
    server: Dict[str, Any],
    probe_result: Optional[Dict[str, Any]]
) -> None:
    """Render server details with probe section embedded."""
    probe_text = _format_probe_section(probe_result)
    full_text = _format_server_details(server, probe_text)

    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, full_text)
    text_widget.configure(state=tk.DISABLED)


def _format_probe_section(probe_result: Optional[Dict[str, Any]]) -> str:
    """Return formatted probe section text."""
    if not probe_result:
        return "🔍 Probe:\n   No probe has been run for this host yet.\n"

    limits = probe_result.get("limits", {})
    max_dirs = limits.get("max_directories")
    max_files = limits.get("max_files")
    timeout = limits.get("timeout_seconds")

    lines: List[str] = [
        "🔍 Probe Snapshot:",
        f"   Run: {probe_result.get('run_at', 'Unknown')}",
        f"   Limits: {max_dirs or '?'} dirs / {max_files or '?'} files per share | Timeout: {timeout or '?'}s"
    ]

    shares = probe_result.get("shares", [])
    if shares:
        for share in shares:
            share_name = share.get("share", "Unknown Share")
            lines.append(f"   Share: {share_name}")
            root_files_raw = share.get("root_files", [])
            root_files: List[str] = []
            for item in root_files_raw:
                if isinstance(item, str):
                    name = item.strip()
                elif isinstance(item, dict):
                    name = str(
                        item.get("name")
                        or item.get("file_name")
                        or item.get("file")
                        or ""
                    ).strip()
                else:
                    name = str(item).strip()
                if name:
                    root_files.append(name)

            if root_files:
                lines.append("      Root files:")
                for file_name in root_files:
                    lines.append(f"         • {file_name}")
                if share.get("root_files_truncated"):
                    lines.append("         … additional root files not shown")
            directories = share.get("directories", [])
            if not directories:
                if not root_files:
                    lines.append("      (no directories returned)")
            for directory in directories:
                dir_name = directory.get("name", "")
                lines.append(f"      📁 {dir_name}/")
                subdirs = directory.get("subdirectories", [])
                if subdirs:
                    for subdir_name in subdirs:
                        lines.append(f"         📁 {subdir_name}/")
                    if directory.get("subdirectories_truncated"):
                        lines.append("         … additional subdirectories not shown")
                files = directory.get("files", [])
                if files:
                    for file_name in files:
                        lines.append(f"         • {file_name}")
                    if directory.get("files_truncated"):
                        lines.append("         … additional files not shown")
                if not subdirs and not files:
                    lines.append("         (no files or subdirectories listed)")
            if share.get("directories_truncated"):
                lines.append("      … additional directories not shown")
    else:
        lines.append("   No shares were successfully probed.")

    rce_lines = _format_rce_summary(probe_result.get("rce_analysis"))
    if rce_lines:
        lines.append("")
        lines.extend(rce_lines)

    analysis = probe_result.get("indicator_analysis") if probe_result else None
    if analysis:
        matches = analysis.get("matches", [])
        if matches:
            lines.append("\n   ☠ Indicators Detected:")
            for match in matches[:5]:
                indicator = match.get("indicator", "Indicator")
                path = match.get("path", "(unknown path)")
                lines.append(f"      {indicator} → {path}")
            if len(matches) > 5:
                lines.append(f"      … {len(matches) - 5} additional hits")
        else:
            lines.append("\n   ✅ No ransomware indicators detected in sampled paths.")

    errors = probe_result.get("errors", [])
    if errors:
        lines.append("\n   ⚠ Probe Errors:")
        for err in errors:
            if isinstance(err, dict):
                share = err.get("share", "Unknown share")
                message = err.get("message", "Unknown error")
            else:
                share = "Unknown share"
                message = str(err)
            lines.append(f"      {share}: {message}")

    lines.append("")
    return "\n".join(lines)


def _format_rce_summary(rce_report: Optional[Dict[str, Any]]) -> List[str]:
    """Generate formatted RCE analysis summary lines with verdict information."""
    prefix = "   RCE Vuln scan:"

    if rce_report is None:
        return [f"{prefix} not requested"]

    if not isinstance(rce_report, dict):
        return [f"{prefix} unavailable"]

    status = (rce_report.get("status") or "").lower()
    error_message = rce_report.get("error")

    if status in {"scanner-unavailable", "analysis-failed"}:
        reason = error_message or ("scanner unavailable" if status == "scanner-unavailable" else "analysis failed")
        return [f"{prefix} unavailable – {reason}"]

    # Get verdict (new primary field) with fallback to status
    verdict = rce_report.get("verdict", "")
    not_assessable_reason = rce_report.get("not_assessable_reason")

    if verdict == "insufficient_data" or status == "insufficient-data":
        return [f"{prefix} INSUFFICIENT DATA; no verdict"]

    if verdict == "not_assessable":
        reason = not_assessable_reason or "Assessment not possible"
        return [f"{prefix} NOT ASSESSABLE – {reason}"]

    if verdict == "not_vulnerable":
        return [f"{prefix} NOT VULNERABLE"]

    if verdict == "error":
        return [f"{prefix} ERROR – {error_message or 'analysis failed'}"]

    # Handle CONFIRMED or LIKELY verdicts
    score = rce_report.get("score", 0)
    verdict_display = verdict.upper().replace("_", " ") if verdict else "UNKNOWN"

    # Use findings (new format) or fall back to matched_rules
    findings = rce_report.get("findings") or []
    matched_rules = rce_report.get("matched_rules") or []

    if findings:
        # New verdict-aware format
        lines = [f"{prefix} {verdict_display} (score {score}/100)"]
        for finding in findings[:3]:
            cve_ids = finding.get("cve_ids", ["?"])
            cve = cve_ids[0] if cve_ids else "?"
            name = finding.get("name", "Unknown")
            finding_verdict = finding.get("verdict", "unknown").upper()
            severity = (finding.get("severity") or "unknown").lower()
            lines.append(f"      • {cve} ({name}): {finding_verdict} ({severity})")
            if finding.get("not_assessable_reason"):
                lines.append(f"        Reason: {finding['not_assessable_reason']}")

        if len(findings) > 3:
            lines.append(f"      … {len(findings) - 3} additional findings")

        return lines

    elif matched_rules:
        # Fall back to legacy matched_rules format
        sorted_rules = sorted(matched_rules, key=lambda rule: rule.get("score", 0), reverse=True)
        primary_rule = sorted_rules[0]
        primary_label = _format_rce_rule_label(primary_rule)
        rule_verdict = primary_rule.get("verdict", verdict_display).upper()

        lines = [f"{prefix} {rule_verdict} – {primary_label} (score {score}/100)"]
        for rule in sorted_rules[:3]:
            label = _format_rce_rule_label(rule)
            severity = (rule.get("severity") or "unknown").lower()
            rule_score = rule.get("score", 0)
            rv = rule.get("verdict", "unknown").upper()
            lines.append(f"      • {label}: {rv} ({severity} severity, +{rule_score})")

        if len(sorted_rules) > 3:
            lines.append(f"      … {len(sorted_rules) - 3} additional signatures")

        return lines

    # No findings or rules matched
    if verdict in ("confirmed", "likely"):
        return [f"{prefix} {verdict_display} (score {score}/100)"]

    return [f"{prefix} none found (score {score}/100)"]


def _format_rce_rule_label(rule: Dict[str, Any]) -> str:
    """Return friendly label combining rule name and primary CVE."""
    name = rule.get("name") or "Unknown"
    cve_ids = rule.get("cve_ids") or []
    if cve_ids:
        return f"{name} ({cve_ids[0]})"
    return name


def _parse_accessible_shares(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    return [share.strip() for share in raw_value.split(',') if share.strip()]


def _load_probe_config(settings_manager) -> Dict[str, int]:
    """Load probe limits from settings (fall back to defaults)."""
    defaults = {
        "max_directories": 3,
        "max_files": 5,
        "timeout_seconds": 10
    }
    if not settings_manager:
        return defaults

    try:
        max_dirs = int(settings_manager.get_setting('probe.max_directories_per_share', defaults["max_directories"]))
        max_files = int(settings_manager.get_setting('probe.max_files_per_directory', defaults["max_files"]))
        timeout = int(settings_manager.get_setting('probe.share_timeout_seconds', defaults["timeout_seconds"]))
    except Exception:
        return defaults

    return {
        "max_directories": max(1, max_dirs),
        "max_files": max(1, max_files),
        "timeout_seconds": max(1, timeout)
    }


def _start_probe(
    detail_window: tk.Toplevel,
    server_data: Dict[str, Any],
    text_widget: tk.Text,
    status_var: tk.StringVar,
    probe_state: Dict[str, Any],
    settings_manager,
    probe_button: Optional[tk.Button],
    config_override: Optional[Dict[str, int]] = None,
    probe_status_callback=None,
    rce_status_callback=None,
    enable_rce_override: Optional[bool] = None
) -> None:
    """Trigger background probe run."""
    if probe_state.get("running"):
        return

    host_type = str(server_data.get("host_type") or "S").strip().upper()
    ip_address = server_data.get('ip_address')
    if not ip_address:
        messagebox.showwarning("Probe Unavailable", "Server IP address is missing.", parent=detail_window)
        return

    accessible_shares: List[str] = []
    if host_type == "S":
        accessible_shares = _parse_accessible_shares(server_data.get('accessible_shares_list', ''))
        if not accessible_shares:
            messagebox.showinfo("Probe", "No accessible shares to probe for this host.")
            return

    config = config_override or _load_probe_config(settings_manager)
    indicator_patterns = probe_state.get("indicator_patterns") or []

    # Check if RCE analysis is enabled
    if enable_rce_override is not None:
        enable_rce = enable_rce_override
    elif settings_manager:
        probe_pref = settings_manager.get_setting('probe_dialog.rce_enabled', None)
        enable_rce = probe_pref if probe_pref is not None else settings_manager.get_setting('scan_dialog.rce_enabled', False)
    else:
        enable_rce = False
    status_var.set("Probing accessible shares…")
    probe_state["running"] = True
    cancel_event = threading.Event()
    probe_state["cancel_event"] = cancel_event
    if probe_button:
        probe_button.configure(state=tk.DISABLED)

    def worker():
        try:
            db_accessor = None
            if settings_manager:
                try:
                    db_accessor = DatabaseReader(settings_manager.get_database_path())
                except Exception:
                    db_accessor = None

            _ftp_port = 21
            if host_type == "F":
                try:
                    _ftp_port = int(server_data.get("port") or 21)
                except Exception:
                    _ftp_port = 21
            _http_port = None
            _http_scheme = None
            _http_request_host = None
            _http_start_path = None
            _protocol_server_id = server_data.get("protocol_server_id")
            if host_type == "H":
                try:
                    _http_port = int(server_data.get("port")) if server_data.get("port") is not None else None
                except (TypeError, ValueError):
                    _http_port = None
                _detail = (
                    db_accessor.get_http_server_detail(
                        ip_address,
                        protocol_server_id=_protocol_server_id,
                        port=_http_port,
                    )
                    if db_accessor else None
                )
                if _http_port is None:
                    _http_port = int((_detail or {}).get("port") or 80)
                _http_scheme = (_detail or {}).get("scheme") or ("https" if _http_port == 443 else "http")
                _http_request_host = (server_data.get("probe_host") or (_detail or {}).get("probe_host") or None)
                _http_start_path = (server_data.get("probe_path") or (_detail or {}).get("probe_path") or "/")
                _http_start_path = str(_http_start_path).split("?", 1)[0].split("#", 1)[0].strip() or "/"
                if not _http_start_path.startswith("/"):
                    _http_start_path = "/" + _http_start_path.lstrip("/")
            result = dispatch_probe_run(
                ip_address, host_type,
                max_directories=int(config["max_directories"]),
                max_files=int(config["max_files"]),
                timeout_seconds=int(config["timeout_seconds"]),
                cancel_event=cancel_event,
                port=_ftp_port if host_type == "F" else _http_port,
                scheme=_http_scheme,
                request_host=_http_request_host,
                start_path=_http_start_path,
                protocol_server_id=_protocol_server_id,
                db_reader=db_accessor,
                shares=accessible_shares,
                enable_rce=enable_rce,
            )
            if cancel_event.is_set():
                # Treat as cancelled; skip success callbacks
                detail_window.after(0, lambda: status_var.set("Probe cancelled."))
                return
            analysis = probe_patterns.attach_indicator_analysis(result, indicator_patterns)
            if host_type == "F":
                probe_summary = summarize_probe_snapshot(result)
                display_entries = probe_summary["display_entries"]
                server_data["total_shares"] = len(display_entries)
                server_data["accessible_shares"] = len(display_entries)
                server_data["accessible_shares_list"] = ",".join(display_entries)
                if db_accessor:
                    try:
                        try:
                            snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=_ftp_port)
                        except TypeError:
                            snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)
                        db_accessor.upsert_probe_cache_for_host(
                            ip_address,
                            "F",
                            status='issue' if analysis.get("is_suspicious") else 'clean',
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
                            accessible_dirs_count=len(display_entries),
                            accessible_dirs_list=",".join(display_entries),
                        )
                    except Exception:
                        pass
            elif host_type == "H":
                probe_summary = summarize_probe_snapshot(result)
                dir_names = probe_summary["directory_names"]
                display_entries = probe_summary["display_entries"]
                total_files = int(probe_summary["total_file_count"])
                total = len(dir_names) + total_files
                server_data["total_shares"] = total
                server_data["accessible_shares"] = total
                server_data["accessible_shares_list"] = ",".join(display_entries)
                if db_accessor:
                    try:
                        try:
                            snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type, port=_http_port)
                        except TypeError:
                            snapshot_path = get_probe_snapshot_path_for_host(ip_address, host_type)
                        db_accessor.upsert_probe_cache_for_host(
                            ip_address,
                            "H",
                            status='issue' if analysis.get("is_suspicious") else 'clean',
                            indicator_matches=len(analysis.get("matches", [])),
                            snapshot_path=snapshot_path,
                            accessible_dirs_count=len(dir_names),
                            accessible_dirs_list=",".join(display_entries),
                            accessible_files_count=total_files,
                            protocol_server_id=_protocol_server_id,
                            port=_http_port,
                        )
                    except Exception:
                        pass
            else:
                probe_cache.save_probe_result(ip_address, result)
            issue_detected = bool(analysis.get("is_suspicious"))
            rce_status = None
            if host_type == "S" and enable_rce:
                rce_analysis = result.get("rce_analysis") or {}
                rce_status = rce_analysis.get("rce_status")

            def on_success():
                probe_state["running"] = False
                probe_state["cancel_event"] = None
                probe_state["latest"] = result
                if issue_detected:
                    status_var.set(
                        f"Probe flagged ransomware indicators at {result.get('run_at', 'unknown')}"
                    )
                else:
                    status_var.set(f"Probe completed at {result.get('run_at', 'unknown')}")
                if probe_button:
                    probe_button.configure(state=tk.NORMAL)
                _render_server_details(text_widget, server_data, result)
                if probe_status_callback:
                    try:
                        probe_status_callback(
                            ip_address,
                            'issue' if issue_detected else 'clean',
                            row_key=server_data.get("row_key"),
                        )
                    except TypeError:
                        probe_status_callback(ip_address, 'issue' if issue_detected else 'clean')
                if rce_status_callback and rce_status:
                    try:
                        rce_status_callback(
                            ip_address,
                            rce_status,
                            row_key=server_data.get("row_key"),
                        )
                    except TypeError:
                        rce_status_callback(ip_address, rce_status)

            detail_window.after(0, on_success)
        except Exception as exc:
            error_message = str(exc)

            def on_error():
                probe_state["running"] = False
                probe_state["cancel_event"] = None
                if probe_button:
                    probe_button.configure(state=tk.NORMAL)
                status_var.set("Probe failed.")
                messagebox.showerror("Probe Failed", error_message, parent=detail_window)

            detail_window.after(0, on_error)

    threading.Thread(target=worker, daemon=True).start()


def _open_probe_dialog(
    parent_window: tk.Toplevel,
    server_data: Dict[str, Any],
    text_widget: tk.Text,
    status_var: tk.StringVar,
    probe_state: Dict[str, Any],
    settings_manager,
    theme,
    probe_button: Optional[tk.Button],
    probe_status_callback=None,
    rce_status_callback=None
) -> None:
    """Show settings + launch dialog for probes."""
    running = probe_state.get("running")

    config = _load_probe_config(settings_manager)

    dialog = tk.Toplevel(parent_window)
    dialog.title("Probe Accessible Shares")
    dialog.transient(parent_window)
    dialog.grab_set()

    if theme:
        theme.apply_to_widget(dialog, "main_window")

    # Ensure dialog appears on top and gains focus (critical for VMs)
    ensure_dialog_focus(dialog, parent_window)

    tk.Label(dialog, text="Max directories per share:").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 5))
    dirs_var = tk.IntVar(value=config["max_directories"])
    tk.Entry(dialog, textvariable=dirs_var, width=10).grid(row=0, column=1, padx=10, pady=(10, 5))

    tk.Label(dialog, text="Max files per directory:").grid(row=1, column=0, sticky="w", padx=10, pady=5)
    files_var = tk.IntVar(value=config["max_files"])
    tk.Entry(dialog, textvariable=files_var, width=10).grid(row=1, column=1, padx=10, pady=5)

    tk.Label(dialog, text="Per-share timeout (seconds):").grid(row=2, column=0, sticky="w", padx=10, pady=5)
    timeout_var = tk.IntVar(value=config["timeout_seconds"])
    tk.Entry(dialog, textvariable=timeout_var, width=10).grid(row=2, column=1, padx=10, pady=5)

    if settings_manager:
        stored_rce_pref = settings_manager.get_setting('probe_dialog.rce_enabled', None)
        if stored_rce_pref is None:
            stored_rce_pref = settings_manager.get_setting('scan_dialog.rce_enabled', False)
    else:
        stored_rce_pref = False

    rce_var = tk.BooleanVar(value=bool(stored_rce_pref))
    rce_frame = tk.Frame(dialog)
    rce_frame.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=5)

    rce_checkbox = tk.Checkbutton(
        rce_frame,
        text="Include RCE vulnerability scan",
        variable=rce_var
    )
    rce_checkbox.pack(anchor="w")

    rce_hint = tk.Label(
        rce_frame,
        text="Adds heuristic RCE detection with summary output.",
        fg="#666666"
    )
    rce_hint.pack(anchor="w", padx=(24, 0))

    def start_probe_from_dialog():
        try:
            new_config = {
                "max_directories": max(1, int(dirs_var.get())),
                "max_files": max(1, int(files_var.get())),
                "timeout_seconds": max(1, int(timeout_var.get()))
            }
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid integers for all fields.", parent=dialog)
            return

        if settings_manager:
            settings_manager.set_setting('probe.max_directories_per_share', new_config["max_directories"])
            settings_manager.set_setting('probe.max_files_per_directory', new_config["max_files"])
            settings_manager.set_setting('probe.share_timeout_seconds', new_config["timeout_seconds"])
            settings_manager.set_setting('probe_dialog.rce_enabled', bool(rce_var.get()))

        dialog.destroy()
        _start_probe(
            parent_window,
            server_data,
            text_widget,
            status_var,
            probe_state,
            settings_manager,
            probe_button,
            config_override=new_config,
            probe_status_callback=probe_status_callback,
            rce_status_callback=rce_status_callback,
            enable_rce_override=bool(rce_var.get())
        )

    button_frame = tk.Frame(dialog)
    button_frame.grid(row=4, column=0, columnspan=2, pady=10)

    if running:
        def cancel_running():
            cancel_evt = probe_state.get("cancel_event")
            if cancel_evt:
                cancel_evt.set()
                status_var.set("Cancelling probe…")
            dialog.destroy()

        tk.Button(button_frame, text="Cancel Running Probe", command=cancel_running).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(button_frame, text="Close", command=dialog.destroy).pack(side=tk.LEFT)
    else:
        tk.Button(button_frame, text="Start Probe", command=start_probe_from_dialog).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side=tk.LEFT)


def _open_extract_dialog(
    parent_window: tk.Toplevel,
    server_data: Dict[str, Any],
    status_var: tk.StringVar,
    extract_state: Dict[str, Any],
    settings_manager,
    theme,
    extract_button: Optional[tk.Button],
) -> None:
    """Show configuration dialog for file extraction using the shared batch dialog."""
    if extract_state.get("running"):
        messagebox.showinfo("Extraction Running", "An extraction task is already in progress.")
        return

    config_path = None
    if settings_manager:
        config_path = settings_manager.get_setting('backend.config_path', None)
        if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
            config_path = settings_manager.get_smbseek_config_path()

    dialog_config = BatchExtractSettingsDialog(
        parent=parent_window,
        theme=theme,
        settings_manager=settings_manager,
        config_path=config_path,
        config_editor_callback=(lambda path: _open_config_editor(parent_window, path)),
        mode="on-demand",
        target_count=1
    ).show()

    if not dialog_config:
        return

    _start_extract(
        parent_window,
        server_data,
        status_var,
        extract_state,
        extract_button,
        dialog_config,
        settings_manager,
    )


def _start_extract(
    detail_window: tk.Toplevel,
    server_data: Dict[str, Any],
    status_var: tk.StringVar,
    extract_state: Dict[str, Any],
    extract_button: Optional[tk.Button],
    extract_config: Dict[str, Any],
    settings_manager=None,
) -> None:
    if extract_state.get("running"):
        return

    ip_address = server_data.get('ip_address')
    if not ip_address:
        messagebox.showwarning("Extraction Unavailable", "Server IP address is missing.")
        return

    accessible_shares = _parse_accessible_shares(server_data.get('accessible_shares_list', ''))
    if not accessible_shares:
        messagebox.showinfo("Extraction", "No accessible shares available for this host.")
        return

    quarantine_root = Path(os.path.expanduser(extract_config["download_path"]))
    try:
        quarantine_dir = create_quarantine_dir(
            ip_address,
            purpose="extract",
            base_path=quarantine_root
        )
    except Exception as exc:
        messagebox.showerror("Quarantine Error", f"Unable to prepare quarantine directory:\n{exc}", parent=detail_window)
        return

    username, password = _derive_credentials(server_data.get('auth_method', ''))

    extract_state["running"] = True
    status_var.set("Preparing extraction…")
    if extract_button:
        extract_button.configure(state=tk.DISABLED)

    def thread_progress(rel_path: str, index: int, limit: Optional[int]) -> None:
        suffix = f"{index}/{limit}" if limit else f"{index}"

        def update():
            status_var.set(f"Downloading {rel_path} ({suffix})")

        detail_window.after(0, update)

    def worker():
        try:
            summary = extract_runner.run_extract(
                ip_address,
                accessible_shares,
                download_dir=quarantine_dir,
                username=username,
                password=password,
                max_total_bytes=extract_config["max_total_size_mb"] * 1024 * 1024,
                max_file_bytes=extract_config["max_file_size_mb"] * 1024 * 1024,
                max_file_count=extract_config["max_files_per_target"],
                max_seconds=extract_config["max_time_seconds"],
                max_depth=extract_config["max_directory_depth"],
                allowed_extensions=extract_config["included_extensions"],
                denied_extensions=extract_config["excluded_extensions"],
                delay_seconds=extract_config["download_delay_seconds"],
                connection_timeout=extract_config["connection_timeout"],
                extension_mode=extract_config.get("extension_mode"),
                progress_callback=thread_progress
            )
            log_path = extract_runner.write_extract_log(summary)

            def on_success():
                extract_state["running"] = False
                if extract_button:
                    extract_button.configure(state=tk.NORMAL)
                files = summary["totals"]["files_downloaded"]
                bytes_downloaded = summary["totals"]["bytes_downloaded"]
                size_mb = bytes_downloaded / (1024 * 1024) if bytes_downloaded else 0
                # Persist extracted flag (best effort)
                try:
                    if settings_manager:
                        db_path = settings_manager.get_database_path() if hasattr(settings_manager, "get_database_path") else None
                        if not db_path:
                            db_path = settings_manager.get_setting('backend.database_path', None)
                        if db_path:
                            db_reader = DatabaseReader(db_path)
                            db_reader.upsert_extracted_flag_for_host(
                                ip_address,
                                server_data.get("host_type", "S"),
                                extracted=True,
                                protocol_server_id=server_data.get("protocol_server_id"),
                                port=server_data.get("port"),
                            )
                    server_data["extracted"] = 1
                    server_data["extract_status_emoji"] = "✔"
                except Exception:
                    pass
                note_parts = []
                if summary.get("timed_out"):
                    note_parts.append("timed out")
                if summary.get("stop_reason"):
                    note_parts.append(summary["stop_reason"].replace("_", " "))
                notes = f" ({', '.join(note_parts)})" if note_parts else ""
                status_var.set(f"Quarantined {files} file(s) ({size_mb:.1f} MB) → {quarantine_dir}{notes}")
                messagebox.showinfo(
                    "Extraction Complete",
                    f"Downloaded {files} file(s) into quarantine:\n{quarantine_dir}\n\n"
                    f"Log saved to:\n{log_path}\n\n"
                    "Inspect and promote files from this quarantine path before moving them elsewhere."
                )

            detail_window.after(0, on_success)

        except extract_runner.ExtractError as exc:
            def on_error():
                extract_state["running"] = False
                if extract_button:
                    extract_button.configure(state=tk.NORMAL)
                status_var.set("Extraction failed.")
                messagebox.showerror("Extraction Failed", str(exc))

            detail_window.after(0, on_error)
        except Exception as exc:  # pragma: no cover - defensive
            error_text = f"Unexpected error: {exc}"

            def on_unexpected():
                extract_state["running"] = False
                if extract_button:
                    extract_button.configure(state=tk.NORMAL)
                status_var.set("Extraction failed.")
                messagebox.showerror("Extraction Error", error_text)

            detail_window.after(0, on_unexpected)

    threading.Thread(target=worker, daemon=True).start()


def _load_file_collection_config(settings_manager) -> Dict[str, Any]:
    """Load file collection defaults from SMBSeek config."""
    defaults = {
        "max_files_per_target": 10,
        "max_total_size_mb": 200,
        "max_file_size_mb": 50,
        "max_time_seconds": 300,
        "max_directory_depth": 3,
        "download_delay_seconds": 0.5,
        "included_extensions": [],
        "excluded_extensions": [],
        "connection_timeout": 30,
        "extension_mode": (settings_manager.get_setting('extract.extension_mode', 'allow_only') if settings_manager else 'allow_only')
    }

    config_path = None
    if settings_manager:
        config_path = settings_manager.get_setting('backend.config_path', None)
        if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
            config_path = settings_manager.get_smbseek_config_path()

    if config_path and Path(config_path).exists():
        try:
            config_data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            file_cfg = config_data.get("file_collection", {})
            defaults["max_files_per_target"] = int(file_cfg.get("max_files_per_target", defaults["max_files_per_target"]))
            defaults["max_total_size_mb"] = int(file_cfg.get("max_total_size_mb", defaults["max_total_size_mb"]))
            defaults["download_delay_seconds"] = float(file_cfg.get("download_delay_seconds", defaults["download_delay_seconds"]))
            defaults["max_directory_depth"] = int(file_cfg.get("max_directory_depth", defaults["max_directory_depth"]))
            defaults["connection_timeout"] = int(file_cfg.get("enumeration_timeout_seconds", defaults["connection_timeout"]))
            defaults["included_extensions"] = file_cfg.get("included_extensions", defaults["included_extensions"])
            defaults["excluded_extensions"] = file_cfg.get("excluded_extensions", defaults["excluded_extensions"])
        except Exception:
            pass

    defaults["max_file_size_mb"] = min(defaults["max_file_size_mb"], defaults["max_total_size_mb"])
    return defaults


def _default_extract_path(ip_address: Optional[str]) -> str:
    base_dir = Path.home() / ".dirracuda" / "quarantine"
    return str(base_dir)


def _derive_credentials(auth_method: Optional[str]) -> Tuple[str, str]:
    method = (auth_method or "").lower()
    if "anonymous" in method:
        return "", ""
    if "guest/blank" in method or method.endswith("guest/"):
        return "guest", ""
    if "guest/guest" in method:
        return "guest", "guest"
    return "guest", ""


def _open_config_editor(parent_window: tk.Toplevel, config_path: str) -> None:
    """Open configuration editor window from details context."""
    try:
        from gui.components.config_editor_window import open_config_editor_window
    except ImportError:
        try:
            from components.config_editor_window import open_config_editor_window
        except Exception as exc:
            messagebox.showerror("Configuration Editor Error", f"Unable to load config editor: {exc}", parent=parent_window)
            return
    try:
        open_config_editor_window(parent_window, config_path)
    except Exception as exc:
        messagebox.showerror("Configuration Editor Error", f"Failed to open configuration editor:\n{exc}", parent=parent_window)
