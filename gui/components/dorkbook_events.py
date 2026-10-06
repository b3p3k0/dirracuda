"""UI-thread notifications for explicit Dorkbook application and safe field refresh."""
from pathlib import Path
import tkinter as tk

from experimental.dorkbook.defaults import read_defaults, reconcile_query
from shared.path_service import get_paths


def config_identity(config_path=None):
    return str(Path(config_path or get_paths().config_file).expanduser().resolve())


def broadcast_applied(widget, config_path, destination, query):
    """Notify live windows only after persistence; no scans or provider toggles."""
    payload = (config_identity(config_path), destination, query)
    root = widget._root()
    pending = [root]
    while pending:
        current = pending.pop()
        pending.extend(current.winfo_children())
        if isinstance(current, (tk.Tk, tk.Toplevel)):
            current._dorkbook_applied = payload
            current.event_generate("<<DorkbookApplied>>", when="now")


def refresh_self_hosted_query(dialog, *, applied=None):
    """Preserve run-local edits on refresh; explicit Apply replaces its destination."""
    path = getattr(dialog, "config_path", None)
    baseline = getattr(dialog, "_self_hosted_default", "")
    if applied is not None:
        identity, destination, query = applied
        if identity != config_identity(path) or destination != "self_hosted":
            return
        latest = query
        value = query
    else:
        latest = read_defaults(path)["self_hosted"]
        value = reconcile_query(dialog.searxng_query_var.get(), baseline, latest)
    dialog.searxng_query_var.set(value)
    dialog._self_hosted_default = latest


def bind_self_hosted_query(dialog):
    """Watch a scan dialog without a global binding or an event-handler leak."""
    def refresh(event):
        if event.widget is not dialog.dialog:
            return
        try:
            applied = getattr(event.widget, "_dorkbook_applied", None) if event.type == tk.EventType.VirtualEvent else None
            refresh_self_hosted_query(dialog, applied=applied)
        except (OSError, ValueError, RuntimeError):
            # Keep the visible run query on a transient config read failure.
            return
    dialog.dialog.bind("<<DorkbookApplied>>", refresh, add="+")
    dialog.dialog.bind("<FocusIn>", refresh, add="+")


# Per-protocol Shodan query wiring: (query var attr, baseline attr, Dorkbook destination).
_SHODAN_QUERY_PROTOCOLS = (
    ("smb_shodan_query_var", "_shodan_smb_default", "shodan:SMB"),
    ("ftp_shodan_query_var", "_shodan_ftp_default", "shodan:FTP"),
    ("http_shodan_query_var", "_shodan_http_default", "shodan:HTTP"),
)


def refresh_shodan_queries(dialog, *, applied=None):
    """Preserve run-local edits on refresh; explicit Apply replaces its destination."""
    path = getattr(dialog, "config_path", None)
    if applied is not None:
        identity, destination, query = applied
        if identity != config_identity(path):
            return
        for var_attr, baseline_attr, destination_key in _SHODAN_QUERY_PROTOCOLS:
            if destination == destination_key:
                getattr(dialog, var_attr).set(query)
                setattr(dialog, baseline_attr, query)
                return
        return
    defaults = read_defaults(path)
    for var_attr, baseline_attr, destination_key in _SHODAN_QUERY_PROTOCOLS:
        latest = defaults.get(destination_key, "")
        baseline = getattr(dialog, baseline_attr, "")
        var = getattr(dialog, var_attr)
        var.set(reconcile_query(var.get(), baseline, latest))
        setattr(dialog, baseline_attr, latest)


def bind_shodan_queries(dialog):
    """Watch a scan dialog without a global binding or an event-handler leak."""
    def refresh(event):
        if event.widget is not dialog.dialog:
            return
        try:
            applied = getattr(event.widget, "_dorkbook_applied", None) if event.type == tk.EventType.VirtualEvent else None
            refresh_shodan_queries(dialog, applied=applied)
        except (OSError, ValueError, RuntimeError):
            # Keep the visible run queries on a transient config read failure.
            return
    dialog.dialog.bind("<<DorkbookApplied>>", refresh, add="+")
    dialog.dialog.bind("<FocusIn>", refresh, add="+")


def open_provider_dorkbook(dialog, provider):
    """Open the same library from either provider, focusing only its group."""
    from gui.components.dorkbook_window import show_dorkbook_window
    show_dorkbook_window(dialog.dialog, settings_manager=dialog._settings_manager,
                         scan_query_config_path=str(dialog.config_path), focus_provider=provider)
