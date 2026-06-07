"""Provider sub-panel builders for UnifiedScanDialog.

Extracted from unified_scan_dialog.py to keep that module within the 1700-line
production-code limit while SearXNG and Reddit option panels are both present.
"""
from __future__ import annotations

import math
import tkinter as tk
from typing import Any, Callable, Dict

from experimental.redseek.models import DEFAULT_MAX_POSTS, MAX_POSTS
from experimental.se_dork.models import DEFAULT_MAX_RESULTS, MAX_RESULTS


SEARXNG_MAX_REMINDER = f"Maximum: {MAX_RESULTS:,} unique results per run."
SEARXNG_PACING_REMINDER = (
    "Large runs are automatically paced to protect upstream engines."
)
REDDIT_MAX_REMINDER = f"Maximum: {MAX_POSTS} posts per RSS snapshot."

# SearXNG tuning control ranges and defaults.
SEARXNG_TIMEOUT_DEFAULT, SEARXNG_TIMEOUT_MIN, SEARXNG_TIMEOUT_MAX = 15, 5, 60
SEARXNG_SHORT_RETRY_DEFAULT, SEARXNG_SHORT_RETRY_MIN, SEARXNG_SHORT_RETRY_MAX = 30, 5, 60
SEARXNG_LONG_RETRY_DEFAULT, SEARXNG_LONG_RETRY_MIN, SEARXNG_LONG_RETRY_MAX = 180, 60, 300


def coerce_searxng_tuning(
    value: Any, *, default: int, lo: int, hi: int, step: int
) -> int:
    """Return a step-snapped, bounds-clamped integer policy value.

    Snapping uses deterministic half-up rounding relative to lo, avoiding
    Python round()'s ties-to-even behaviour (e.g. round(75/30)*30 == 60,
    not 90). inf, -inf, and nan all return default.
    """
    try:
        v = float(str(value))
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(v):
        return default
    snapped = lo + int(math.floor((v - lo) / step + 0.5)) * step
    return max(lo, min(hi, snapped))


def _make_snap_callback(
    dbl_var: tk.DoubleVar,
    disp_var: tk.StringVar,
    lo: int,
    hi: int,
    step: int,
) -> Callable:
    """Return a recursion-safe trace callback that snaps dbl_var to the nearest step."""
    _guard = [False]

    def _snap(*_: object) -> None:
        if _guard[0]:
            return
        raw = dbl_var.get()
        snapped = coerce_searxng_tuning(raw, default=lo, lo=lo, hi=hi, step=step)
        if abs(raw - snapped) > 1e-9:
            _guard[0] = True
            try:
                dbl_var.set(float(snapped))
            finally:
                _guard[0] = False
        disp_var.set(f"{snapped}s")

    return _snap


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
    instance_entry.grid(row=0, column=1, sticky="ew", padx=(4, 12), pady=0)

    _grid_label(frame, "Query", 1, 0, theme)
    query_entry = ttk.Entry(frame, textvariable=vars_dict["query"])
    query_entry.grid(
        row=1,
        column=1,
        sticky="ew",
        padx=(4, 12),
        pady=0,
    )

    _grid_label(frame, "Results", 2, 0, theme)
    results_entry = ttk.Entry(frame, textvariable=vars_dict["max_results"], width=8)
    results_entry.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=0)

    scale_widgets: list = []
    value_labels: list = []
    scale_subframes: list = []
    _tuning_rows = (
        ("Request timeout", SEARXNG_TIMEOUT_MIN, SEARXNG_TIMEOUT_MAX, 1,
         vars_dict.get("request_timeout")),
        ("Short retry", SEARXNG_SHORT_RETRY_MIN, SEARXNG_SHORT_RETRY_MAX, 5,
         vars_dict.get("short_retry_delay")),
        ("Long retry", SEARXNG_LONG_RETRY_MIN, SEARXNG_LONG_RETRY_MAX, 30,
         vars_dict.get("long_retry_delay")),
    )
    for grid_row, (label_text, lo, hi, step, dbl_var) in enumerate(
        _tuning_rows, start=3
    ):
        if dbl_var is None:
            continue
        sub, sc, vl = _build_scale_row(frame, grid_row, label_text, lo, hi, step, dbl_var, theme)
        scale_widgets.append(sc)
        value_labels.append(vl)
        scale_subframes.append(sub)

    hint_row = 3 + len(scale_widgets)
    _hint_text = (
        f"Max {MAX_RESULTS:,} results. Long retry skipped after 5 productive pages "
        f"or 50 URLs. Retry-After and soft pacing are automatic."
        if scale_widgets
        else f"{SEARXNG_MAX_REMINDER} {SEARXNG_PACING_REMINDER}"
    )
    hint = _small_label(frame, _hint_text, theme)
    hint.grid(row=hint_row, column=0, columnspan=3, sticky="w", pady=0)
    frame._helper_label = hint  # type: ignore[attr-defined]
    frame._searxng_instance_entry = instance_entry  # type: ignore[attr-defined]
    frame._searxng_query_entry = query_entry  # type: ignore[attr-defined]
    frame._searxng_results_entry = results_entry  # type: ignore[attr-defined]
    frame._searxng_scale_widgets = scale_widgets  # type: ignore[attr-defined]
    frame._searxng_tuning_value_labels = value_labels  # type: ignore[attr-defined]
    frame._searxng_scale_subframes = scale_subframes  # type: ignore[attr-defined]
    return frame


def _build_scale_row(
    frame: tk.Widget,
    grid_row: int,
    label_text: str,
    lo: int,
    hi: int,
    step: int,
    dbl_var: tk.DoubleVar,
    theme: Any,
) -> tuple:
    """Build one slider row inside *frame* and return (scale_widget, value_label)."""
    from tkinter import ttk

    sub = tk.Frame(frame)
    theme.apply_to_widget(sub, "main_window")
    sub.grid_columnconfigure(2, weight=1)
    sub.grid(row=grid_row, column=0, columnspan=3, sticky="ew", pady=0)

    lbl = theme.create_styled_label(sub, label_text, "small")
    lbl.grid(row=0, column=0, sticky="w", padx=(0, 6))
    lbl.configure(width=16, anchor="w")

    min_lbl = theme.create_styled_label(sub, f"{lo}s", "small")
    min_lbl.grid(row=0, column=1, sticky="e", padx=(0, 4))

    scale = ttk.Scale(sub, from_=lo, to=hi, orient=tk.HORIZONTAL, variable=dbl_var)
    scale.grid(row=0, column=2, sticky="ew")

    max_lbl = theme.create_styled_label(sub, f"{hi}s", "small")
    max_lbl.grid(row=0, column=3, sticky="w", padx=(4, 6))

    initial = coerce_searxng_tuning(dbl_var.get(), default=lo, lo=lo, hi=hi, step=step)
    disp_var = tk.StringVar(value=f"{initial}s")
    val_lbl = theme.create_styled_label(sub, "", "small")
    val_lbl.configure(textvariable=disp_var, width=5, anchor="w")
    val_lbl.grid(row=0, column=4, sticky="w")

    cb = _make_snap_callback(dbl_var, disp_var, lo, hi, step)
    dbl_var.trace_add("write", cb)

    return sub, scale, val_lbl


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
        pady=0,
    )

    options = tk.Frame(frame)
    theme.apply_to_widget(options, "main_window")
    options.grid(row=2, column=0, columnspan=8, sticky="w", pady=0)
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
    hint.grid(row=3, column=0, columnspan=8, sticky="w", pady=0)

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


def sync_searxng_option_state(frame: tk.Widget | None, enabled: bool) -> None:
    """Apply enabled state to the SearXNG panel, including tuning value labels.

    Scale subframes remain visible at all times; only their state changes.
    This follows the same visible-but-disabled pattern as Instance/Query/Results.
    """
    sync_option_entries(frame, enabled)
    val_labels = getattr(frame, "_searxng_tuning_value_labels", [])
    state = tk.NORMAL if enabled else tk.DISABLED
    for lbl in val_labels:
        try:
            lbl.configure(state=state)
        except tk.TclError:
            pass


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
            ttk.Scale,
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
    _t = getattr(dialog, "searxng_request_timeout_var", None)
    if _t is not None:
        _t.set(float(coerce_searxng_tuning(
            g("unified_scan_dialog.searxng_request_timeout", SEARXNG_TIMEOUT_DEFAULT),
            default=SEARXNG_TIMEOUT_DEFAULT, lo=SEARXNG_TIMEOUT_MIN,
            hi=SEARXNG_TIMEOUT_MAX, step=1,
        )))
    _s = getattr(dialog, "searxng_short_retry_delay_var", None)
    if _s is not None:
        _s.set(float(coerce_searxng_tuning(
            g("unified_scan_dialog.searxng_short_retry_delay", SEARXNG_SHORT_RETRY_DEFAULT),
            default=SEARXNG_SHORT_RETRY_DEFAULT, lo=SEARXNG_SHORT_RETRY_MIN,
            hi=SEARXNG_SHORT_RETRY_MAX, step=5,
        )))
    _l = getattr(dialog, "searxng_long_retry_delay_var", None)
    if _l is not None:
        _l.set(float(coerce_searxng_tuning(
            g("unified_scan_dialog.searxng_long_retry_delay", SEARXNG_LONG_RETRY_DEFAULT),
            default=SEARXNG_LONG_RETRY_DEFAULT, lo=SEARXNG_LONG_RETRY_MIN,
            hi=SEARXNG_LONG_RETRY_MAX, step=30,
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
    _t = getattr(dialog, "searxng_request_timeout_var", None)
    if _t is not None:
        sm.set_setting(
            "unified_scan_dialog.searxng_request_timeout",
            coerce_searxng_tuning(_t.get(), default=SEARXNG_TIMEOUT_DEFAULT,
                                   lo=SEARXNG_TIMEOUT_MIN, hi=SEARXNG_TIMEOUT_MAX, step=1),
        )
    _s = getattr(dialog, "searxng_short_retry_delay_var", None)
    if _s is not None:
        sm.set_setting(
            "unified_scan_dialog.searxng_short_retry_delay",
            coerce_searxng_tuning(_s.get(), default=SEARXNG_SHORT_RETRY_DEFAULT,
                                   lo=SEARXNG_SHORT_RETRY_MIN, hi=SEARXNG_SHORT_RETRY_MAX, step=5),
        )
    _l = getattr(dialog, "searxng_long_retry_delay_var", None)
    if _l is not None:
        sm.set_setting(
            "unified_scan_dialog.searxng_long_retry_delay",
            coerce_searxng_tuning(_l.get(), default=SEARXNG_LONG_RETRY_DEFAULT,
                                   lo=SEARXNG_LONG_RETRY_MIN, hi=SEARXNG_LONG_RETRY_MAX, step=30),
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
    _t = getattr(dialog, "searxng_request_timeout_var", None)
    if _t is not None:
        _t.set(float(coerce_searxng_tuning(
            opts.get("request_timeout", SEARXNG_TIMEOUT_DEFAULT),
            default=SEARXNG_TIMEOUT_DEFAULT, lo=SEARXNG_TIMEOUT_MIN,
            hi=SEARXNG_TIMEOUT_MAX, step=1,
        )))
    _sr = getattr(dialog, "searxng_short_retry_delay_var", None)
    if _sr is not None:
        _sr.set(float(coerce_searxng_tuning(
            opts.get("short_retry_delay", SEARXNG_SHORT_RETRY_DEFAULT),
            default=SEARXNG_SHORT_RETRY_DEFAULT, lo=SEARXNG_SHORT_RETRY_MIN,
            hi=SEARXNG_SHORT_RETRY_MAX, step=5,
        )))
    _lr = getattr(dialog, "searxng_long_retry_delay_var", None)
    if _lr is not None:
        _lr.set(float(coerce_searxng_tuning(
            opts.get("long_retry_delay", SEARXNG_LONG_RETRY_DEFAULT),
            default=SEARXNG_LONG_RETRY_DEFAULT, lo=SEARXNG_LONG_RETRY_MIN,
            hi=SEARXNG_LONG_RETRY_MAX, step=30,
        )))


def validate_searxng_max_results(value: Any) -> int:
    """Return a bounded SearXNG result cap with the current default fallback."""
    return _coerce_bounded_int(value, DEFAULT_MAX_RESULTS, maximum=MAX_RESULTS)


def _coerce_bounded_int(value: Any, default: int, *, maximum: int) -> int:
    candidate = default if value is None or str(value).strip() == "" else value
    try:
        return max(1, min(maximum, int(str(candidate))))
    except (TypeError, ValueError):
        return default
