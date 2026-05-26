"""Provider sub-panel builders for UnifiedScanDialog.

Extracted from unified_scan_dialog.py to keep that module within the 1700-line
production-code limit while SearXNG and Reddit option panels are both present.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Dict


def build_searxng_sub_panel(
    container: tk.Widget,
    vars_dict: Dict[str, Any],
    theme: Any,
) -> tk.Frame:
    """Build the SearXNG options inline frame.

    vars_dict keys: ``instance_url``, ``query``, ``max_results`` — each a tk.StringVar.
    Returns the frame so the caller can store it for enable/disable syncing.
    """
    frame = tk.Frame(container)
    theme.apply_to_widget(frame, "card")

    for _label, _var, _width in (
        ("Instance URL", vars_dict["instance_url"], 36),
        ("Query", vars_dict["query"], 36),
        ("Max Results", vars_dict["max_results"], 8),
    ):
        row = tk.Frame(frame)
        theme.apply_to_widget(row, "card")
        row.pack(fill=tk.X, pady=1)
        lbl = theme.create_styled_label(row, _label, "small")
        lbl.pack(side=tk.LEFT, padx=(6, 4))
        ent = tk.Entry(row, textvariable=_var, width=_width, font=theme.fonts["small"])
        theme.apply_to_widget(ent, "entry")
        ent.pack(side=tk.LEFT, padx=(0, 6))

    return frame


def build_reddit_sub_panel(
    container: tk.Widget,
    vars_dict: Dict[str, Any],
    theme: Any,
) -> tk.Frame:
    """Build the Reddit options inline frame.

    vars_dict keys: ``mode``, ``sort``, ``top_window``, ``max_posts``, ``query``,
    ``username``, ``parse_body``, ``include_nsfw``.

    All fields are always visible; mode-conditional validation happens at submit time.
    Returns the frame so the caller can store it for enable/disable syncing.
    """
    frame = tk.Frame(container)
    theme.apply_to_widget(frame, "card")

    # Mode selector
    _add_option_row(frame, "Mode", vars_dict["mode"], ["feed", "search", "user"], theme, width=10)

    # Sort + top-window
    _add_option_row(frame, "Sort", vars_dict["sort"], ["new", "top"], theme, width=6)
    _add_option_row(
        frame, "Top Window", vars_dict["top_window"],
        ["week", "day", "hour", "month", "year", "all"], theme, width=8,
    )

    # Max posts
    _add_entry_row(frame, "Max Posts", vars_dict["max_posts"], theme, width=6)

    # Query (validated when mode=search)
    _add_entry_row(frame, "Query", vars_dict["query"], theme, width=30)

    # Username (validated when mode=user)
    _add_entry_row(frame, "Username", vars_dict["username"], theme, width=20)

    # Boolean options
    _add_checkbutton_row(frame, "Parse body", vars_dict["parse_body"], theme)
    _add_checkbutton_row(frame, "Include NSFW", vars_dict["include_nsfw"], theme)

    return frame


def sync_option_entries(frame: tk.Widget | None, enabled: bool) -> None:
    """Enable or disable all stateful child widgets in *frame*.

    Handles tk.Entry, tk.Checkbutton, and the menubutton inside tk.OptionMenu.
    Safe to call with frame=None.
    """
    if frame is None:
        return
    new_state = tk.NORMAL if enabled else tk.DISABLED
    _apply_state_recursive(frame, new_state)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _add_option_row(
    parent: tk.Widget,
    label: str,
    var: tk.StringVar,
    choices: list[str],
    theme: Any,
    *,
    width: int = 10,
) -> None:
    row = tk.Frame(parent)
    theme.apply_to_widget(row, "card")
    row.pack(fill=tk.X, pady=1)
    lbl = theme.create_styled_label(row, label, "small")
    lbl.pack(side=tk.LEFT, padx=(6, 4))
    om = tk.OptionMenu(row, var, *choices)
    om.config(font=theme.fonts["small"], width=width)
    theme.apply_to_widget(om, "button_secondary")
    om.pack(side=tk.LEFT, padx=(0, 6))


def _add_entry_row(
    parent: tk.Widget,
    label: str,
    var: tk.StringVar,
    theme: Any,
    *,
    width: int = 20,
) -> None:
    row = tk.Frame(parent)
    theme.apply_to_widget(row, "card")
    row.pack(fill=tk.X, pady=1)
    lbl = theme.create_styled_label(row, label, "small")
    lbl.pack(side=tk.LEFT, padx=(6, 4))
    ent = tk.Entry(row, textvariable=var, width=width, font=theme.fonts["small"])
    theme.apply_to_widget(ent, "entry")
    ent.pack(side=tk.LEFT, padx=(0, 6))


def _add_checkbutton_row(
    parent: tk.Widget,
    label: str,
    var: tk.BooleanVar,
    theme: Any,
) -> None:
    cb = tk.Checkbutton(parent, text=label, variable=var, font=theme.fonts["small"])
    theme.apply_to_widget(cb, "checkbox")
    cb.pack(anchor="w", padx=10, pady=1)


def _apply_state_recursive(widget: tk.Widget, state: str) -> None:
    """Recursively set *state* on Entry, Checkbutton, and OptionMenu menubuttons."""
    if isinstance(widget, (tk.Entry, tk.Checkbutton)):
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
    elif isinstance(widget, tk.Menubutton):
        # OptionMenu is a Menubutton; configure it directly.
        try:
            widget.configure(state=state)
        except tk.TclError:
            pass
    try:
        for child in widget.winfo_children():
            _apply_state_recursive(child, state)
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Reddit settings persistence helpers (called from UnifiedScanDialog)
# ---------------------------------------------------------------------------

def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def load_reddit_settings(dialog: Any, sm: Any) -> None:
    """Load persisted Reddit option vars onto *dialog* from settings manager *sm*."""
    g = sm.get_setting
    dialog.reddit_mode_var.set(str(g("unified_scan_dialog.reddit_mode", "feed") or "feed"))
    dialog.reddit_sort_var.set(str(g("unified_scan_dialog.reddit_sort", "new") or "new"))
    dialog.reddit_top_window_var.set(str(g("unified_scan_dialog.reddit_top_window", "week") or "week"))
    dialog.reddit_max_posts_var.set(str(g("unified_scan_dialog.reddit_max_posts", "50") or "50"))
    dialog.reddit_query_var.set(str(g("unified_scan_dialog.reddit_query", "") or ""))
    dialog.reddit_username_var.set(str(g("unified_scan_dialog.reddit_username", "") or ""))
    dialog.reddit_parse_body_var.set(_coerce_bool(g("unified_scan_dialog.reddit_parse_body", True), True))
    dialog.reddit_include_nsfw_var.set(_coerce_bool(g("unified_scan_dialog.reddit_include_nsfw", False), False))


def persist_reddit_settings(dialog: Any, sm: Any) -> None:
    """Persist current Reddit option vars from *dialog* into settings manager *sm*."""
    sm.set_setting("unified_scan_dialog.reddit_mode", dialog.reddit_mode_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_sort", dialog.reddit_sort_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_top_window", dialog.reddit_top_window_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_max_posts", dialog.reddit_max_posts_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_query", dialog.reddit_query_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_username", dialog.reddit_username_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_parse_body", bool(dialog.reddit_parse_body_var.get()))
    sm.set_setting("unified_scan_dialog.reddit_include_nsfw", bool(dialog.reddit_include_nsfw_var.get()))


def validate_reddit_scan_options(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate raw Reddit option values (already resolved from tk vars).

    Raises ``ValueError`` with a user-facing message on invalid input.
    Returns a flat dict ready for merging into the scan request.
    """
    mode = str(raw.get("mode") or "feed").strip() or "feed"
    if mode not in {"feed", "search", "user"}:
        raise ValueError(f"Invalid Reddit mode: {mode!r}. Select feed, search, or user.")
    query = str(raw.get("query") or "").strip()
    if mode == "search" and not query:
        raise ValueError("Reddit search mode requires a query.")
    username = str(raw.get("username") or "").strip()
    if mode == "user" and not username:
        raise ValueError("Reddit user mode requires a username.")
    sort = str(raw.get("sort") or "new").strip() or "new"
    top_window = str(raw.get("top_window") or "week").strip() or "week"
    try:
        max_posts = max(1, min(200, int(str(raw.get("max_posts") or "50"))))
    except (ValueError, TypeError):
        max_posts = 50
    parse_body = bool(raw.get("parse_body", True))
    include_nsfw = bool(raw.get("include_nsfw", False))
    return {
        "reddit_mode": mode,
        "reddit_sort": sort,
        "reddit_top_window": top_window,
        "reddit_max_posts": max_posts,
        "reddit_query": query,
        "reddit_username": username,
        "reddit_parse_body": parse_body,
        "reddit_include_nsfw": include_nsfw,
    }


def apply_reddit_form_state(dialog: Any, opts: Dict[str, Any]) -> None:
    """Restore Reddit option vars on *dialog* from a saved form-state dict."""
    _s = lambda attr, val: getattr(dialog, attr, None) and getattr(dialog, attr).set(val)
    _s("reddit_mode_var", str(opts.get("mode", "feed") or "feed"))
    _s("reddit_sort_var", str(opts.get("sort", "new") or "new"))
    _s("reddit_top_window_var", str(opts.get("top_window", "week") or "week"))
    _s("reddit_max_posts_var", str(opts.get("max_posts", "50") or "50"))
    _s("reddit_query_var", str(opts.get("query", "") or ""))
    _s("reddit_username_var", str(opts.get("username", "") or ""))
    _s("reddit_parse_body_var", bool(opts.get("parse_body", True)))
    _s("reddit_include_nsfw_var", bool(opts.get("include_nsfw", False)))
