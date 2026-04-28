"""
SearXNG Dorking tab for the Experimental Features dialog.

C2: Test button wired to run_preflight (threaded). Instance URL persisted
via settings_manager (key: se_dork.instance_url).
C3: Run button wired to run_dork_search (threaded). Query and max_results
persisted. Run summary shown in status area.
"""

from __future__ import annotations

import threading
import tkinter as tk
from types import SimpleNamespace
from typing import Any, Optional

from gui.utils.style import get_theme

_DEFAULT_INSTANCE_URL = "http://your.searxng.server:port"
_DEFAULT_QUERY = 'site:* intitle:"index of /"'
_DEFAULT_MAX_RESULTS = "50"
_DEFAULT_BULK_PROBE_ENABLED = False
_DEFAULT_PROBE_WORKERS = 3
_SETTINGS_KEY_URL = "se_dork.instance_url"
_SETTINGS_KEY_QUERY = "se_dork.query"
_SETTINGS_KEY_MAX_RESULTS = "se_dork.max_results"
_SETTINGS_KEY_BULK_PROBE_ENABLED = "se_dork.bulk_probe_enabled"


def _resolve_initial_url(settings_manager: Any, default: str) -> str:
    """
    Return the persisted instance URL from settings_manager, or default.

    Pure helper — no Tk dependency; directly testable.
    """
    if settings_manager is None:
        return default
    try:
        return settings_manager.get_setting(_SETTINGS_KEY_URL, default) or default
    except Exception:
        return default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Best-effort bool coercion for persisted settings values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off", ""):
            return False
        return default
    return default


def _resolve_probe_worker_count(settings_manager: Any, default: int = _DEFAULT_PROBE_WORKERS) -> int:
    """Resolve probe worker count from settings, clamped to [1, 8]."""
    if settings_manager is None:
        return default
    try:
        raw = settings_manager.get_setting("probe.batch_max_workers", default)
        return max(1, min(8, int(raw)))
    except Exception:
        return default


class SeDorkTab:
    """Content widget for the SearXNG Dorking experimental feature tab."""

    def __init__(self, parent: tk.Widget, context: dict) -> None:
        self._context = context
        self._theme = get_theme()
        self.frame = tk.Frame(parent)
        self._theme.apply_to_widget(self.frame, "main_window")
        self._build(self.frame)

    def _build(self, frame: tk.Frame) -> None:
        desc_label = tk.Label(
            frame,
            text=(
                "SearXNG-driven dork search.\n"
                "Run open-directory queries against a configured SearXNG instance."
            ),
            justify="left",
            anchor="w",
            wraplength=480,
        )
        self._theme.apply_to_widget(desc_label, "label")
        desc_label.pack(anchor="w", padx=16, pady=(16, 12))

        # Instance URL row
        url_row = tk.Frame(frame)
        self._theme.apply_to_widget(url_row, "main_window")
        url_row.pack(anchor="w", padx=16, pady=(0, 6), fill=tk.X)

        url_label = tk.Label(url_row, text="SearXNG Server:", anchor="w", width=14)
        self._theme.apply_to_widget(url_label, "label")
        url_label.pack(side=tk.LEFT)

        sm = self._context.get("settings_manager")
        initial_url = _resolve_initial_url(sm, _DEFAULT_INSTANCE_URL)
        self._url_var = tk.StringVar(value=initial_url)
        url_entry = tk.Entry(url_row, textvariable=self._url_var, width=40)
        self._theme.apply_to_widget(url_entry, "entry")
        url_entry.pack(side=tk.LEFT, padx=(6, 0))

        # Query row
        query_row = tk.Frame(frame)
        self._theme.apply_to_widget(query_row, "main_window")
        query_row.pack(anchor="w", padx=16, pady=(0, 12), fill=tk.X)

        query_label = tk.Label(query_row, text="Query:", anchor="w", width=14)
        self._theme.apply_to_widget(query_label, "label")
        query_label.pack(side=tk.LEFT)

        initial_query = _DEFAULT_QUERY
        if sm is not None:
            try:
                initial_query = sm.get_setting(_SETTINGS_KEY_QUERY, _DEFAULT_QUERY) or _DEFAULT_QUERY
            except Exception:
                pass
        self._query_var = tk.StringVar(value=initial_query)
        query_entry = tk.Entry(query_row, textvariable=self._query_var, width=40)
        self._theme.apply_to_widget(query_entry, "entry")
        query_entry.pack(side=tk.LEFT, padx=(6, 0))

        # Max results row
        max_row = tk.Frame(frame)
        self._theme.apply_to_widget(max_row, "main_window")
        max_row.pack(anchor="w", padx=16, pady=(0, 2), fill=tk.X)

        max_label = tk.Label(max_row, text="Max results:", anchor="w", width=14)
        self._theme.apply_to_widget(max_label, "label")
        max_label.pack(side=tk.LEFT)

        initial_max = _DEFAULT_MAX_RESULTS
        if sm is not None:
            try:
                initial_max = sm.get_setting(_SETTINGS_KEY_MAX_RESULTS, _DEFAULT_MAX_RESULTS) or _DEFAULT_MAX_RESULTS
            except Exception:
                pass
        self._max_results_var = tk.StringVar(value=initial_max)
        max_entry = tk.Entry(max_row, textvariable=self._max_results_var, width=8)
        self._theme.apply_to_widget(max_entry, "entry")
        max_entry.pack(side=tk.LEFT, padx=(6, 0))

        bulk_probe_enabled = _DEFAULT_BULK_PROBE_ENABLED
        if sm is not None:
            try:
                bulk_probe_enabled = bool(
                    _coerce_bool(
                        sm.get_setting(
                            _SETTINGS_KEY_BULK_PROBE_ENABLED,
                            _DEFAULT_BULK_PROBE_ENABLED,
                        ),
                        _DEFAULT_BULK_PROBE_ENABLED,
                    )
                )
            except Exception:
                pass
        self._bulk_probe_var = tk.BooleanVar(value=bulk_probe_enabled)
        bulk_probe_cb = tk.Checkbutton(
            max_row,
            text="Run Probe on Results",
            variable=self._bulk_probe_var,
        )
        self._theme.apply_to_widget(bulk_probe_cb, "checkbox")
        bulk_probe_cb.pack(side=tk.LEFT, padx=(12, 0))

        max_hint_row = tk.Frame(frame)
        self._theme.apply_to_widget(max_hint_row, "main_window")
        max_hint_row.pack(anchor="w", padx=16, pady=(0, 10), fill=tk.X)

        max_hint_spacer = tk.Label(max_hint_row, text="", width=14, anchor="w")
        self._theme.apply_to_widget(max_hint_spacer, "label")
        max_hint_spacer.pack(side=tk.LEFT)

        max_hint_label = tk.Label(max_hint_row, text="Maximum 500", anchor="w")
        self._theme.apply_to_widget(max_hint_label, "label")
        max_hint_label.pack(side=tk.LEFT, padx=(6, 0))

        # Buttons row
        btn_frame = tk.Frame(frame)
        self._theme.apply_to_widget(btn_frame, "main_window")
        btn_frame.pack(anchor="w", padx=16, pady=(0, 8))

        self._test_btn = tk.Button(
            btn_frame, text="Test", command=self._invoke_test
        )
        self._theme.apply_to_widget(self._test_btn, "button_primary")
        self._test_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._run_btn = tk.Button(btn_frame, text="Run", command=self._invoke_run)
        self._theme.apply_to_widget(self._run_btn, "button_primary")
        self._run_btn.pack(side=tk.LEFT, padx=(0, 8))

        results_btn = tk.Button(
            btn_frame, text="Open Results DB", command=self._open_results_browser
        )
        self._theme.apply_to_widget(results_btn, "button_secondary")
        results_btn.pack(side=tk.LEFT)

        # Status area
        self._status_label = tk.Label(frame, text="", anchor="w")
        self._theme.apply_to_widget(self._status_label, "label")
        self._status_label.pack(anchor="w", padx=16, pady=(4, 0))

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------

    def _save_url(self) -> None:
        """Persist current instance URL to settings (no-op if no manager)."""
        sm = self._context.get("settings_manager")
        if sm is None:
            return
        try:
            sm.set_setting(_SETTINGS_KEY_URL, self._url_var.get().strip())
        except Exception:
            pass

    def _save_settings(self) -> None:
        """Persist URL, query, and max_results to settings (no-op if no manager)."""
        sm = self._context.get("settings_manager")
        if sm is None:
            return
        try:
            sm.set_setting(_SETTINGS_KEY_URL, self._url_var.get().strip())
            sm.set_setting(_SETTINGS_KEY_QUERY, self._query_var.get().strip())
            sm.set_setting(_SETTINGS_KEY_MAX_RESULTS, self._max_results_var.get().strip())
            sm.set_setting(_SETTINGS_KEY_BULK_PROBE_ENABLED, bool(self._bulk_probe_var.get()))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Test button
    # ------------------------------------------------------------------

    def _invoke_test(self) -> None:
        """Kick off a threaded preflight check against the instance URL."""
        url = self._url_var.get().strip()
        if not url:
            self._status_label.configure(text="Enter an instance URL first.")
            return

        self._save_url()
        self._test_btn.configure(state="disabled")
        self._status_label.configure(text="Testing instance\u2026")

        def _run() -> None:
            try:
                from experimental.se_dork.client import run_preflight

                result = run_preflight(url)
            except Exception as exc:
                result = SimpleNamespace(
                    ok=False,
                    message=f"Unexpected preflight error: {exc}",
                )
            self.frame.after(0, lambda: self._on_preflight_done(result))

        threading.Thread(target=_run, daemon=True).start()

    def _on_preflight_done(self, result: Any) -> None:
        """Called on the main thread once the preflight thread completes."""
        self._test_btn.configure(state="normal")
        if result.ok:
            self._status_label.configure(text=f"\u2713 {result.message}")
        else:
            self._status_label.configure(text=f"\u2717 {result.message}")

    # ------------------------------------------------------------------
    # Run button
    # ------------------------------------------------------------------

    def _invoke_run(self) -> None:
        """Kick off a threaded dork search run."""
        url = self._url_var.get().strip()
        query = self._query_var.get().strip()
        if not url or not query:
            self._status_label.configure(text="Enter instance URL and query first.")
            return

        try:
            max_results = max(1, min(500, int(self._max_results_var.get().strip() or _DEFAULT_MAX_RESULTS)))
        except (ValueError, TypeError):
            max_results = int(_DEFAULT_MAX_RESULTS)

        self._save_settings()
        self._test_btn.configure(state="disabled")
        self._run_btn.configure(state="disabled")
        self._status_label.configure(text="Running dork search\u2026")

        probe_config_path = None
        sm = self._context.get("settings_manager")
        probe_worker_count = _resolve_probe_worker_count(sm, _DEFAULT_PROBE_WORKERS)
        if sm is not None and hasattr(sm, "get_smbseek_config_path"):
            try:
                probe_config_path = sm.get_smbseek_config_path()
            except Exception:
                probe_config_path = None

        from experimental.se_dork.models import RunOptions
        options = RunOptions(
            instance_url=url,
            query=query,
            max_results=max_results,
            bulk_probe_enabled=bool(self._bulk_probe_var.get()),
            probe_config_path=probe_config_path,
            probe_worker_count=probe_worker_count,
        )

        def _run() -> None:
            try:
                from experimental.se_dork.service import run_dork_search
                result = run_dork_search(options)
            except Exception as exc:
                from experimental.se_dork.models import RunResult, RUN_STATUS_ERROR
                result = RunResult(
                    run_id=None,
                    fetched_count=0,
                    deduped_count=0,
                    status=RUN_STATUS_ERROR,
                    error=str(exc),
                )
            self.frame.after(0, lambda: self._on_run_done(result))

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Open Results DB
    # ------------------------------------------------------------------

    def _open_results_browser(self) -> None:
        """Open the se_dork results browser window."""
        cb = self._context.get("open_se_dork_results_db")
        if cb is not None:
            cb()
        else:
            # Wiring absent — open browser without callback so "Not available"
            # message is shown if the user attempts promotion. Visible failure
            # is preferable to a silent no-op for a mandatory promotion path.
            from gui.components.se_dork_browser_window import show_se_dork_browser_window
            show_se_dork_browser_window(
                self.frame,
                add_record_callback=None,
                settings_manager=self._context.get("settings_manager"),
            )

    def _on_run_done(self, result: Any) -> None:
        """Called on the main thread once the run thread completes."""
        self._test_btn.configure(state="normal")
        self._run_btn.configure(state="normal")
        if result.status == "done":
            message = f"Done \u2014 fetched {result.fetched_count}, stored {result.deduped_count} unique."
            if getattr(result, "probe_enabled", False):
                message += (
                    f"\nProbe: {getattr(result, 'probe_total', 0)} rows \u2022 "
                    f"\u2714 {getattr(result, 'probe_clean', 0)} \u2022 "
                    f"\u2716 {getattr(result, 'probe_issue', 0)} \u2022 "
                    f"\u25cb {getattr(result, 'probe_unprobed', 0)}"
                )
            self._status_label.configure(text=message)
        else:
            self._status_label.configure(text=f"Run failed: {result.error}")


def build_se_dork_tab(parent: tk.Widget, context: dict) -> tk.Widget:
    """Build and return the SearXNG Dorking tab frame."""
    tab = SeDorkTab(parent, context)
    return tab.frame
