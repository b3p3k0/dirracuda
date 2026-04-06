"""
Explorer bridge for reddit targets.

Infers protocol from target data and opens in the system browser.
Falls back to a user-supplied protocol prompt when protocol cannot be
determined from stored data or port hints.

Inference order:
  1. target_normalized starts with a known scheme  -> open directly
  2. protocol field is set and not "unknown", host is non-empty -> construct URL
  3. host contains explicit port :80/:443/:21       -> infer http/https/ftp
  4. Still unresolved                               -> prompt user for protocol
"""

import webbrowser
from tkinter import messagebox, simpledialog
from typing import Optional

from experimental.redseek.models import RedditTarget

_SCHEMES = ("http://", "https://", "ftp://")
_PORT_PROTO = {80: "http", 443: "https", 21: "ftp"}


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


def open_target(target: RedditTarget, parent) -> None:
    """
    Open a reddit target in the system browser.

    Infers the URL from target data; prompts the user for protocol when
    inference fails. Returns silently if the user cancels the prompt.
    No network probing is performed.
    """
    url = _infer_url(target)

    if url is None:
        proto = _ask_protocol(parent, target.target_normalized or "")
        if proto is None:
            return
        host = target.host or target.target_normalized or ""
        url = f"{proto}://{host}"

    webbrowser.open(url)
