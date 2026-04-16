"""
gui/browsers/factory.py — Browser factory functions (extracted in Card C5).

open_ftp_http_browser and open_smb_browser were previously defined in
gui.components.unified_browser_window. Re-exported there for backward compat.

INVARIANT: zero module-scope imports from gui.components.unified_browser_window.

open_ftp_http_browser imports FtpBrowserWindow/HttpBrowserWindow from
gui.components.unified_browser_window at call time (not from gui.browsers.*)
so that patch("gui.components.unified_browser_window.FtpBrowserWindow", ...)
in tests is observed correctly.
"""
from __future__ import annotations

from typing import List, Optional


def _normalize_share_name(name: str) -> str:
    return name.strip().strip("\\/").strip()


def open_ftp_http_browser(
    host_type: str,
    parent,
    ip_address: str,
    port: int,
    *,
    initial_path=None,
    banner=None,
    scheme=None,
    config_path=None,
    db_reader=None,
    theme=None,
    settings_manager=None,
) -> None:
    """Launch FtpBrowserWindow (host_type='F') or HttpBrowserWindow (host_type='H').

    SMB (host_type='S') is not handled here; callers must route it separately.

    FtpBrowserWindow and HttpBrowserWindow are imported from
    gui.components.unified_browser_window at call time so that monkeypatches
    on those names in ubw are observed by tests (§2c of BASELINE_CONTRACTS).
    """
    # Import from ubw, not gui.browsers.*, so monkeypatches on ubw names are observed.
    from gui.components.unified_browser_window import FtpBrowserWindow, HttpBrowserWindow
    host_type = (host_type or "").upper()
    if host_type == "F":
        FtpBrowserWindow(
            parent=parent,
            ip_address=ip_address,
            port=port,
            banner=banner,
            config_path=config_path,
            db_reader=db_reader,
            theme=theme,
            settings_manager=settings_manager,
        )
    elif host_type == "H":
        HttpBrowserWindow(
            parent=parent,
            ip_address=ip_address,
            port=port,
            scheme=scheme,
            initial_path=initial_path,
            banner=banner,
            config_path=config_path,
            db_reader=db_reader,
            theme=theme,
            settings_manager=settings_manager,
        )
    else:
        raise ValueError(f"open_ftp_http_browser: unsupported host_type {host_type!r}")


def open_smb_browser(
    parent,
    ip_address: str,
    shares: list,
    auth_method: str = "",
    *,
    config_path=None,
    db_reader=None,
    theme=None,
    settings_manager=None,
    share_credentials=None,
    on_extracted=None,
) -> None:
    """Launch SmbBrowserWindow for SMB (host_type='S') with Shodan banner.

    Resolves banner from db_reader.get_smb_shodan_data() when available;
    falls back to empty string so SmbBrowserWindow shows its placeholder.
    Re-queries accessible shares from DB when available (freshest source of
    truth); falls back to caller-provided shares only on exception.
    """
    from gui.browsers.smb_browser import SmbBrowserWindow, _extract_smb_banner

    if db_reader:
        try:
            rows = db_reader.get_accessible_shares(ip_address)
            shares = [
                n for r in rows
                if (n := _normalize_share_name(r.get("share_name") or ""))
            ]
        except Exception:
            pass  # exception only — keep caller-provided shares as fallback

    # Normalize all sources uniformly — handles DB-exception and no-db_reader paths
    shares = [n for s in shares if (n := _normalize_share_name(s))]

    shodan_raw = None
    if db_reader:
        try:
            shodan_raw = db_reader.get_smb_shodan_data(ip_address)
        except Exception:
            pass
    banner = _extract_smb_banner(shodan_raw)

    SmbBrowserWindow(
        parent=parent,
        ip_address=ip_address,
        shares=shares,
        auth_method=auth_method,
        config_path=config_path,
        db_reader=db_reader,
        theme=theme,
        settings_manager=settings_manager,
        share_credentials=share_credentials or {},
        on_extracted=on_extracted,
        banner=banner,
    )
