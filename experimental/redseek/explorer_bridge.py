"""
Explorer bridge for reddit targets.

Infers protocol from target data and attempts internal browser launch first.
Falls back to a 3-option prompt (system browser / copy address / cancel) when
internal launch is unavailable or fails.  When protocol cannot be determined
from stored data or port hints, prompts the user for a protocol first.

Inference order:
  1. target_normalized starts with a known scheme  -> attempt internal open
  2. protocol field is set and not "unknown", host is non-empty -> construct URL
  3. host contains explicit port :80/:443/:21       -> infer http/https/ftp
  4. Still unresolved                               -> prompt user for protocol
"""

import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog
from typing import Optional
from urllib.parse import urlparse

from experimental.redseek.models import RedditTarget

_SCHEMES = ("http://", "https://", "ftp://")
_PORT_PROTO = {80: "http", 443: "https", 21: "ftp"}
_DEFAULT_PORTS = {"ftp": 21, "http": 80, "https": 443}


def _infer_url(target: RedditTarget) -> Optional[str]:
    """
    Try to construct an openable URL from target data.

    Returns the URL string, or None when protocol cannot be determined.
    Never makes network calls; pure inference only.
    """
    norm = target.target_normalized or ""

    # Rule 1: full scheme present in normalized value
    if any(norm.startswith(s) for s in _SCHEMES):
        return norm

    # Rule 2: stored protocol field is concrete and host is non-empty
    if target.protocol and target.protocol != "unknown" and target.host:
        return f"{target.protocol}://{target.host}"

    # Rule 3: port-based inference from host
    host = target.host or ""
    # Skip IPv6 bracket addresses (contain ':' but start with '[')
    if host and not host.startswith("[") and ":" in host:
        try:
            port = int(host.rsplit(":", 1)[-1])
            proto = _PORT_PROTO.get(port)
            if proto:
                return f"{proto}://{host}"
        except ValueError:
            pass

    return None


def _parse_for_internal(url: str) -> Optional[tuple]:
    """Parse (scheme, host, port, start_path) for internal browser launch.

    Returns a (scheme, host, port, start_path) tuple for ftp/http/https URLs, or None
    when the scheme is not supported for internal launch.  Never makes
    network calls; pure parsing only.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = parsed.hostname or ""
    port = parsed.port or _DEFAULT_PORTS[scheme]
    path = (parsed.path or "").strip()
    start_path = f"/{path.lstrip('/')}" if path else "/"
    return scheme, host, port, start_path


def _show_fallback_dialog(parent, url: str, reason: str) -> str:
    """Blocking 3-option modal shown when internal open cannot proceed.

    Returns one of 'browser', 'copy', or 'cancel'.
    """
    result = ["cancel"]

    dlg = tk.Toplevel(parent)
    dlg.title("Cannot Open Internally")
    dlg.resizable(False, False)
    dlg.grab_set()

    tk.Label(
        dlg, text=reason, wraplength=380, justify=tk.LEFT,
    ).pack(fill=tk.X, padx=16, pady=(12, 2))
    tk.Label(
        dlg, text=url, wraplength=380, justify=tk.LEFT,
    ).pack(fill=tk.X, padx=16, pady=(0, 12))

    btn_frame = tk.Frame(dlg)
    btn_frame.pack(padx=16, pady=(0, 12))

    def _pick(val: str) -> None:
        result[0] = val
        dlg.destroy()

    tk.Button(
        btn_frame, text="Open in system browser", command=lambda: _pick("browser"),
    ).pack(side=tk.LEFT, padx=(0, 6))
    tk.Button(
        btn_frame, text="Copy address", command=lambda: _pick("copy"),
    ).pack(side=tk.LEFT, padx=(0, 6))
    tk.Button(
        btn_frame, text="Cancel", command=lambda: _pick("cancel"),
    ).pack(side=tk.LEFT)

    dlg.wait_window()
    return result[0]


def _ask_protocol(parent, target_str: str) -> Optional[str]:
    """
    Prompt the user to choose a protocol for an unresolved target.

    Returns one of "http", "https", "ftp" on valid input, or None on
    cancel / empty input / unrecognised value.
    """
    raw = simpledialog.askstring(
        "Select Protocol",
        f"Cannot infer protocol for:\n{target_str}\n\nEnter: http / https / ftp",
        parent=parent,
    )
    if not raw:
        return None

    cleaned = raw.strip().lower().rstrip(":/")
    if cleaned not in {"http", "https", "ftp"}:
        messagebox.showerror(
            "Unknown Protocol",
            f"'{raw.strip()}' is not a recognised protocol. Use http, https, or ftp.",
            parent=parent,
        )
        return None

    return cleaned


def resolve_target_url(target: RedditTarget, parent) -> Optional[str]:
    """
    Resolve a target to a URL, prompting for protocol when needed.

    Returns a URL string, or None when user cancels protocol prompt.
    """
    url = _infer_url(target)
    if url is not None:
        return url

    proto = _ask_protocol(parent, target.target_normalized or "")
    if proto is None:
        return None
    host = target.host or target.target_normalized or ""
    return f"{proto}://{host}"


def open_target_system_browser(target: RedditTarget, parent) -> None:
    """
    Open a target URL directly in the system browser.

    Uses the same inference/prompt flow as open_target, but never attempts
    internal FTP/HTTP browser launch.
    """
    url = resolve_target_url(target, parent)
    if url is None:
        return
    webbrowser.open(url)


def open_target(target: RedditTarget, parent, *, browser_factory=None) -> None:
    """
    Open a reddit target, attempting internal browser launch first.

    For FTP/HTTP/HTTPS targets:
    - If browser_factory is provided and succeeds: done.
    - If browser_factory is absent or the launch fails: show 3-option fallback
      prompt (Open in system browser / Copy address / Cancel).

    For other schemes: same fallback prompt with an "unsupported" reason.

    Returns silently if the user cancels any prompt.
    No network probing is performed.
    """
    url = resolve_target_url(target, parent)
    if url is None:
        return

    parsed = _parse_for_internal(url)

    if parsed is None:
        reason = "Internal browser supports FTP/HTTP/HTTPS only."
    else:
        scheme, host, port, start_path = parsed
        if browser_factory is not None:
            try:
                browser_factory(scheme, host, port, start_path=start_path)
                return
            except Exception as exc:
                reason = f"Internal browser failed: {exc}"
        else:
            reason = "Internal browser is not available in this context."

    choice = _show_fallback_dialog(parent, url, reason)
    if choice == "browser":
        webbrowser.open(url)
    elif choice == "copy":
        parent.clipboard_clear()
        parent.clipboard_append(url)
