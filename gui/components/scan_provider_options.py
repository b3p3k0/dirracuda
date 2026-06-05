"""Provider sub-panel builders for UnifiedScanDialog.

Extracted from unified_scan_dialog.py to keep that module within the 1700-line
production-code limit while SearXNG and Reddit option panels are both present.
"""
from __future__ import annotations

import tkinter as tk
from typing import Any, Dict

from experimental.redseek.models import DEFAULT_MAX_POSTS, MAX_POSTS
from experimental.se_dork.models import DEFAULT_MAX_RESULTS, MAX_RESULTS


SEARXNG_MAX_REMINDER = f"Maximum: {MAX_RESULTS:,} unique results per run."
REDDIT_MAX_REMINDER = f"Maximum: {MAX_POSTS} posts per RSS snapshot."


def build_searxng_sub_panel(
    container: tk.Widget,
    vars_dict: Dict[str, Any],
    theme: Any,
) -> tk.Frame:
    """Build compact SearXNG controls for the unified scan dialog."""
    from tkinter import ttk

    frame = tk.Frame(container)
    theme.apply_to_widget(frame, "main_window")
    frame.grid_columnconfigure(1, weight=1)
    frame.grid_columnconfigure(2, weight=1)

    _grid_label(frame, "Instance", 0, 0, theme)
    instance_entry = ttk.Entry(frame, textvariable=vars_dict["instance_url"])
    instance_entry.grid(row=0, column=1, sticky="ew", padx=(4, 12), pady=2)

    _grid_label(frame, "Query", 1, 0, theme)
    query_entry = ttk.Entry(frame, textvariable=vars_dict["query"])
    query_entry.grid(
        row=1,
        column=1,
        sticky="ew",
        padx=(4, 12),
        pady=2,
    )

    _grid_label(frame, "Results", 2, 0, theme)
    results_entry = ttk.Entry(frame, textvariable=vars_dict["max_results"], width=8)
    results_entry.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=2)

    hint = _small_label(frame, SEARXNG_MAX_REMINDER, theme)
    hint.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 2))
    frame._helper_label = hint  # type: ignore[attr-defined]
    frame._searxng_instance_entry = instance_entry  # type: ignore[attr-defined]
    frame._searxng_query_entry = query_entry  # type: ignore[attr-defined]
    frame._searxng_results_entry = results_entry  # type: ignore[attr-defined]
    return frame


def build_reddit_sub_panel(
    container: tk.Widget,
    vars_dict: Dict[str, Any],
    theme: Any,
    *,
    on_state_change=None,
) -> tk.Frame:
    """Build compact Reddit controls with feed/search conditional fields."""
    from tkinter import ttk

    frame = tk.Frame(container)
    theme.apply_to_widget(frame, "main_window")
    frame.grid_columnconfigure(7, weight=1)

    _grid_label(frame, "Mode", 0, 0, theme)
    mode_combo = ttk.Combobox(
        frame,
        textvariable=vars_dict["mode"],
        values=("feed", "search"),
        state="readonly",
        width=8,
    )
    mode_combo.grid(row=0, column=1, sticky="w", padx=(4, 12), pady=2)

    _grid_label(frame, "Sort", 0, 2, theme)
    sort_combo = ttk.Combobox(
        frame,
        textvariable=vars_dict["sort"],
        values=("new", "top"),
        state="readonly",
        width=7,
    )
    sort_combo.grid(row=0, column=3, sticky="w", padx=(4, 12), pady=2)

    _grid_label(frame, "Window", 0, 4, theme)
    top_window_combo = ttk.Combobox(
        frame,
        textvariable=vars_dict["top_window"],
        values=("hour", "day", "week", "month", "year", "all"),
        state="readonly",
        width=8,
    )
    top_window_combo.grid(row=0, column=5, sticky="w", padx=(4, 12), pady=2)

    _grid_label(frame, "Posts", 0, 6, theme)
    max_posts_entry = ttk.Entry(frame, textvariable=vars_dict["max_posts"], width=7)
    max_posts_entry.grid(row=0, column=7, sticky="w", padx=(4, 0), pady=2)

    query_label = _grid_label(frame, "Query", 1, 0, theme)
    query_entry = ttk.Entry(frame, textvariable=vars_dict["query"])
    query_entry.grid(
        row=1,
        column=1,
        columnspan=7,
        sticky="ew",
        padx=(4, 0),
        pady=2,
    )

    options = tk.Frame(frame)
    theme.apply_to_widget(options, "main_window")
    options.grid(row=2, column=0, columnspan=8, sticky="w", pady=(1, 2))
    parse_body = ttk.Checkbutton(
        options,
        text="Parse body",
        variable=vars_dict["parse_body"],
    )
    parse_body.pack(side=tk.LEFT)
    include_nsfw = ttk.Checkbutton(
        options,
        text="Include NSFW",
        variable=vars_dict["include_nsfw"],
    )
    include_nsfw.pack(side=tk.LEFT, padx=(12, 0))

    hint = _small_label(frame, REDDIT_MAX_REMINDER, theme)
    hint.grid(row=3, column=0, columnspan=8, sticky="w", pady=(0, 2))

    frame._reddit_mode_var = vars_dict["mode"]  # type: ignore[attr-defined]
    frame._reddit_sort_var = vars_dict["sort"]  # type: ignore[attr-defined]
    frame._reddit_query_widgets = (query_label, query_entry)  # type: ignore[attr-defined]
    frame._reddit_top_window_combo = top_window_combo  # type: ignore[attr-defined]
    frame._helper_label = hint  # type: ignore[attr-defined]

    if callable(on_state_change):
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: on_state_change())
        sort_combo.bind("<<ComboboxSelected>>", lambda _event: on_state_change())
        vars_dict["mode"].trace_add("write", lambda *_args: on_state_change())
        vars_dict["sort"].trace_add("write", lambda *_args: on_state_change())

    return frame


def sync_option_entries(frame: tk.Widget | None, enabled: bool) -> None:
    """Enable or disable stateful controls in a provider panel."""
    if frame is None:
        return
    new_state = tk.NORMAL if enabled else tk.DISABLED
    _apply_state_recursive(frame, new_state)


def sync_reddit_option_state(frame: tk.Widget | None, enabled: bool) -> None:
    """Apply provider, mode, and sort state to the Reddit controls."""
    if frame is None:
        return
    sync_option_entries(frame, enabled)

    mode_var = getattr(frame, "_reddit_mode_var", None)
    sort_var = getattr(frame, "_reddit_sort_var", None)
    query_widgets = getattr(frame, "_reddit_query_widgets", ())
    mode = str(mode_var.get() if mode_var is not None else "feed").strip().lower()
    sort = str(sort_var.get() if sort_var is not None else "new").strip().lower()

    for widget in query_widgets:
        if mode == "search":
            widget.grid()
        else:
            widget.grid_remove()

    top_window_combo = getattr(frame, "_reddit_top_window_combo", None)
    if top_window_combo is not None:
        top_window_combo.configure(
            state="readonly" if enabled and sort == "top" else tk.DISABLED
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _grid_label(
    parent: tk.Widget,
    text: str,
    row: int,
    column: int,
    theme: Any,
) -> tk.Label:
    label = theme.create_styled_label(parent, text, "small")
    label.grid(row=row, column=column, sticky="w", pady=2)
    return label


def _small_label(parent: tk.Widget, text: str, theme: Any) -> tk.Label:
    return theme.create_styled_label(
        parent,
        text,
        "small",
        fg=theme.colors["text_secondary"],
    )


def _apply_state_recursive(widget: tk.Widget, state: str) -> None:
    """Recursively set state on Tk and ttk input controls."""
    from tkinter import ttk

    if isinstance(widget, ttk.Combobox):
        try:
            widget.configure(state="readonly" if state == tk.NORMAL else tk.DISABLED)
        except tk.TclError:
            pass
    elif isinstance(
        widget,
        (
            tk.Entry,
            tk.Checkbutton,
            tk.Radiobutton,
            tk.Button,
            tk.Menubutton,
            ttk.Entry,
            ttk.Checkbutton,
            ttk.Radiobutton,
            ttk.Button,
        ),
    ):
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
# Provider settings persistence helpers (called from UnifiedScanDialog)
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


def load_searxng_settings(dialog: Any, sm: Any) -> None:
    """Load persisted SearXNG options with current cap coercion."""
    g = sm.get_setting
    dialog.searxng_instance_url_var.set(
        str(g("unified_scan_dialog.searxng_instance_url", "") or "")
    )
    dialog.searxng_query_var.set(
        str(g("unified_scan_dialog.searxng_query", "") or "")
    )
    dialog.searxng_max_results_var.set(str(validate_searxng_max_results(
        g("unified_scan_dialog.searxng_max_results", DEFAULT_MAX_RESULTS)
    )))


def persist_searxng_settings(dialog: Any, sm: Any) -> None:
    """Persist normalized SearXNG options."""
    sm.set_setting(
        "unified_scan_dialog.searxng_instance_url",
        dialog.searxng_instance_url_var.get().strip(),
    )
    sm.set_setting(
        "unified_scan_dialog.searxng_query",
        dialog.searxng_query_var.get().strip(),
    )
    sm.set_setting(
        "unified_scan_dialog.searxng_max_results",
        str(validate_searxng_max_results(dialog.searxng_max_results_var.get())),
    )


def load_reddit_settings(dialog: Any, sm: Any) -> None:
    """Load persisted Reddit option vars onto *dialog* from settings manager *sm*."""
    g = sm.get_setting
    mode = str(g("unified_scan_dialog.reddit_mode", "feed") or "feed")
    dialog.reddit_mode_var.set(mode if mode in {"feed", "search"} else "feed")
    dialog.reddit_sort_var.set(str(g("unified_scan_dialog.reddit_sort", "new") or "new"))
    dialog.reddit_top_window_var.set(str(g("unified_scan_dialog.reddit_top_window", "week") or "week"))
    raw_max = g("unified_scan_dialog.reddit_max_posts", DEFAULT_MAX_POSTS)
    dialog.reddit_max_posts_var.set(str(_coerce_bounded_int(
        raw_max, DEFAULT_MAX_POSTS, maximum=MAX_POSTS
    )))
    dialog.reddit_query_var.set(str(g("unified_scan_dialog.reddit_query", "") or ""))
    dialog.reddit_username_var.set("")
    dialog.reddit_parse_body_var.set(_coerce_bool(g("unified_scan_dialog.reddit_parse_body", True), True))
    dialog.reddit_include_nsfw_var.set(_coerce_bool(g("unified_scan_dialog.reddit_include_nsfw", False), False))


def persist_reddit_settings(dialog: Any, sm: Any) -> None:
    """Persist current Reddit option vars from *dialog* into settings manager *sm*."""
    sm.set_setting("unified_scan_dialog.reddit_mode", dialog.reddit_mode_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_sort", dialog.reddit_sort_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_top_window", dialog.reddit_top_window_var.get().strip())
    sm.set_setting(
        "unified_scan_dialog.reddit_max_posts",
        str(_coerce_bounded_int(
            dialog.reddit_max_posts_var.get(), DEFAULT_MAX_POSTS, maximum=MAX_POSTS
        )),
    )
    sm.set_setting("unified_scan_dialog.reddit_query", dialog.reddit_query_var.get().strip())
    sm.set_setting("unified_scan_dialog.reddit_parse_body", bool(dialog.reddit_parse_body_var.get()))
    sm.set_setting("unified_scan_dialog.reddit_include_nsfw", bool(dialog.reddit_include_nsfw_var.get()))


def validate_reddit_scan_options(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate raw Reddit option values (already resolved from tk vars).

    Raises ``ValueError`` with a user-facing message on invalid input.
    Returns a flat dict ready for merging into the scan request.
    """
    mode = str(raw.get("mode") or "feed").strip() or "feed"
    if mode not in {"feed", "search"}:
        raise ValueError(f"Invalid Reddit mode: {mode!r}. Select feed or search.")
    query = str(raw.get("query") or "").strip()
    if mode == "search" and not query:
        raise ValueError("Reddit search mode requires a query.")
    sort = str(raw.get("sort") or "new").strip() or "new"
    top_window = str(raw.get("top_window") or "week").strip() or "week"
    max_posts = _coerce_bounded_int(
        raw.get("max_posts"), DEFAULT_MAX_POSTS, maximum=MAX_POSTS
    )
    parse_body = bool(raw.get("parse_body", True))
    include_nsfw = bool(raw.get("include_nsfw", False))
    return {
        "reddit_mode": mode,
        "reddit_sort": sort,
        "reddit_top_window": top_window,
        "reddit_max_posts": max_posts,
        "reddit_query": query,
        "reddit_username": "",
        "reddit_parse_body": parse_body,
        "reddit_include_nsfw": include_nsfw,
    }


def apply_reddit_form_state(dialog: Any, opts: Dict[str, Any]) -> None:
    """Restore Reddit option vars on *dialog* from a saved form-state dict."""
    _s = lambda attr, val: getattr(dialog, attr, None) and getattr(dialog, attr).set(val)
    mode = str(opts.get("mode", "feed") or "feed")
    _s("reddit_mode_var", mode if mode in {"feed", "search"} else "feed")
    _s("reddit_sort_var", str(opts.get("sort", "new") or "new"))
    _s("reddit_top_window_var", str(opts.get("top_window", "week") or "week"))
    _s("reddit_max_posts_var", str(_coerce_bounded_int(
        opts.get("max_posts"), DEFAULT_MAX_POSTS, maximum=MAX_POSTS
    )))
    _s("reddit_query_var", str(opts.get("query", "") or ""))
    _s("reddit_username_var", "")
    _s("reddit_parse_body_var", bool(opts.get("parse_body", True)))
    _s("reddit_include_nsfw_var", bool(opts.get("include_nsfw", False)))


def apply_searxng_form_state(dialog: Any, opts: Dict[str, Any]) -> None:
    """Restore SearXNG form state while enforcing the current result ceiling."""
    _s = lambda attr, val: getattr(dialog, attr, None) and getattr(dialog, attr).set(val)
    _s("searxng_instance_url_var", str(opts.get("instance_url", "") or ""))
    _s("searxng_query_var", str(opts.get("query", "") or ""))
    _s(
        "searxng_max_results_var",
        str(validate_searxng_max_results(opts.get("max_results"))),
    )


def validate_searxng_max_results(value: Any) -> int:
    """Return a bounded SearXNG result cap with the current default fallback."""
    return _coerce_bounded_int(value, DEFAULT_MAX_RESULTS, maximum=MAX_RESULTS)


def _coerce_bounded_int(value: Any, default: int, *, maximum: int) -> int:
    candidate = default if value is None or str(value).strip() == "" else value
    try:
        return max(1, min(maximum, int(str(candidate))))
    except (TypeError, ValueError):
        return default
