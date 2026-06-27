"""Rendered layout checks for the compact Start Scan dialog."""

from __future__ import annotations

import json
import tkinter as tk

import pytest

import gui.components.unified_scan_dialog as unified_scan_dialog
from gui.components.unified_scan_layout import (
    DEFAULT_GEOMETRY,
    MIN_HEIGHT,
    MIN_WIDTH,
)


class _TemplateStoreStub:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def list_templates(self):
        return []

    def load_template(self, _slug):
        return None

    def set_last_used(self, _slug) -> None:
        pass


class _SettingsStub:
    def __init__(self, overrides=None) -> None:
        self.values = dict(overrides or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value) -> None:
        self.values[key] = value


def _build_dialog(monkeypatch, tmp_path, overrides=None):
    monkeypatch.setattr(
        unified_scan_dialog,
        "TemplateStore",
        _TemplateStoreStub,
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "discovery": {"max_concurrent_hosts": 10},
                "connection": {"timeout": 10},
            }
        ),
        encoding="utf-8",
    )
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.geometry("1200x900+0+0")
    root.update_idletasks()

    dialog = unified_scan_dialog.UnifiedScanDialog(
        parent=root,
        config_path=str(config_path),
        scan_start_callback=lambda _request: None,
        settings_manager=_SettingsStub(overrides),
    )
    root.update()
    dialog.dialog.update()
    return root, dialog


def _destroy(root, dialog) -> None:
    try:
        dialog.dialog.destroy()
    finally:
        root.destroy()


def test_compact_default_geometry_and_fixed_footer(monkeypatch, tmp_path):
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        width, height = (int(value) for value in DEFAULT_GEOMETRY.split("x"))
        assert dialog.dialog.winfo_width() == width
        assert dialog.dialog.winfo_height() == height
        assert tuple(dialog.dialog.minsize()) == (MIN_WIDTH, MIN_HEIGHT)
        assert dialog._footer_frame.winfo_ismapped()
        assert dialog._footer_frame.winfo_rooty() > dialog._canvas.winfo_rooty()
    finally:
        _destroy(root, dialog)


def test_worst_case_provider_state_fits_without_default_scroll(
    monkeypatch,
    tmp_path,
):
    overrides = {
        "unified_scan_dialog.provider_shodan": True,
        "unified_scan_dialog.provider_searxng": True,
        "unified_scan_dialog.provider_reddit": True,
        "unified_scan_dialog.reddit_mode": "search",
        "unified_scan_dialog.reddit_sort": "top",
        "unified_scan_dialog.reddit_top_window": "year",
        "unified_scan_dialog.searxng_instance_url": "http://halcyon:8090",
        "unified_scan_dialog.searxng_query": 'intitle:"index of /"',
        "unified_scan_dialog.searxng_max_results": 1000,
        "unified_scan_dialog.reddit_max_posts": 100,
    }
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        for variable in (
            dialog.africa_var,
            dialog.asia_var,
            dialog.europe_var,
            dialog.north_america_var,
            dialog.oceania_var,
            dialog.south_america_var,
        ):
            variable.set(True)
        dialog._update_region_status()
        root.update()

        assert dialog._content.winfo_reqwidth() <= dialog._canvas.winfo_width()
        assert dialog._content.winfo_reqheight() <= dialog._canvas.winfo_height()
        assert dialog._scrollbar_visible is False
        assert dialog._provider_queue_label.cget("text") == (
            "Queue: Reddit -> SearXNG -> Shodan"
        )
        assert dialog._reddit_query_entry.winfo_ismapped()
        assert str(dialog._reddit_top_window_combo.cget("state")) == "readonly"

        providers_bottom = (
            dialog._providers_section.winfo_rooty()
            + dialog._providers_section.winfo_height()
        )
        lower_bottom = (
            dialog._lower_columns.winfo_rooty()
            + dialog._lower_columns.winfo_height()
        )
        assert providers_bottom <= dialog._lower_columns.winfo_rooty()
        assert lower_bottom <= dialog._footer_frame.winfo_rooty()
        assert (
            dialog._config_row.winfo_rooty() + dialog._config_row.winfo_height()
            <= dialog._footer_actions_row.winfo_rooty()
        )
    finally:
        _destroy(root, dialog)


def test_provider_helper_rows_and_searxng_fields_are_aligned(
    monkeypatch,
    tmp_path,
):
    overrides = {
        "unified_scan_dialog.provider_shodan": True,
        "unified_scan_dialog.provider_searxng": True,
        "unified_scan_dialog.provider_reddit": True,
    }
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        root.update()
        helper_x_positions = {
            dialog._shodan_helper_row.winfo_rootx(),
            dialog._searxng_helper_label.winfo_rootx(),
            dialog._reddit_helper_label.winfo_rootx(),
        }
        assert len(helper_x_positions) == 1

        instance_width = dialog._searxng_instance_entry.winfo_width()
        query_width = dialog._searxng_query_entry.winfo_width()
        panel_width = dialog._searxng_opts_frame.winfo_width()
        assert abs(instance_width - query_width) <= 2
        assert panel_width * 0.35 <= instance_width <= panel_width * 0.60
        assert (
            dialog._searxng_results_entry.winfo_rooty()
            > dialog._searxng_query_entry.winfo_rooty()
        )
        assert (
            dialog._searxng_helper_label.winfo_rooty()
            > dialog._searxng_results_entry.winfo_rooty()
        )
    finally:
        _destroy(root, dialog)


def test_unchecked_provider_controls_stay_visible_and_disabled(
    monkeypatch,
    tmp_path,
):
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        assert dialog._searxng_opts_frame.winfo_ismapped()
        assert dialog._reddit_opts_frame.winfo_ismapped()
        searxng_entries = [
            widget
            for widget in dialog._searxng_opts_frame.winfo_children()
            if widget.winfo_class() == "TEntry"
        ]
        assert searxng_entries
        assert all(
            str(widget.cget("state")) == "disabled"
            for widget in searxng_entries
        )
        assert str(dialog._reddit_top_window_combo.cget("state")) == "disabled"

        dialog.provider_shodan_var.set(False)
        dialog._sync_shodan_options_state()
        shodan_entries = [
            widget
            for widget in dialog._shodan_opts_frame.winfo_children()
            if widget.winfo_class() == "TEntry"
        ]
        assert shodan_entries
        assert all(
            str(widget.cget("state")) == "disabled"
            for widget in shodan_entries
        )
        assert dialog._provider_queue_label.cget("text") == "Queue: none selected"
    finally:
        _destroy(root, dialog)


def test_country_and_region_targeting_are_mutually_exclusive(
    monkeypatch,
    tmp_path,
):
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        dialog.country_var.set("us")
        root.update()
        assert dialog.country_var.get() == "US"
        assert all(
            str(widget.cget("state")) == "disabled"
            for widget in dialog._region_checkbuttons
        )
        assert all(
            str(widget.cget("state")) == "disabled"
            for widget in dialog._region_action_buttons
        )

        dialog.country_var.set("")
        root.update()
        assert all(
            str(widget.cget("state")) == "normal"
            for widget in dialog._region_checkbuttons
        )

        dialog.africa_var.set(True)
        dialog._on_region_selection_changed()
        assert str(dialog.country_entry.cget("state")) == "disabled"

        dialog._clear_all_regions()
        assert str(dialog.country_entry.cget("state")) == "normal"
    finally:
        _destroy(root, dialog)


def test_conflicting_targeting_state_keeps_explicit_country_codes(
    monkeypatch,
    tmp_path,
):
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        dialog.country_var.set("GB")
        dialog.africa_var.set(True)
        dialog.asia_var.set(True)
        dialog._sync_targeting_mode_state()

        assert dialog.country_var.get() == "GB"
        assert not dialog._get_selected_region_countries()
        assert str(dialog.country_entry.cget("state")) == "normal"
        assert all(
            str(widget.cget("state")) == "disabled"
            for widget in dialog._region_checkbuttons
        )
    finally:
        _destroy(root, dialog)


def test_reddit_query_and_top_window_follow_mode_and_sort(
    monkeypatch,
    tmp_path,
):
    overrides = {"unified_scan_dialog.provider_reddit": True}
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        assert not dialog._reddit_query_entry.winfo_ismapped()
        assert str(dialog._reddit_top_window_combo.cget("state")) == "disabled"

        dialog.reddit_mode_var.set("search")
        dialog.reddit_sort_var.set("top")
        dialog._sync_reddit_options_state()
        root.update()
        assert dialog._reddit_query_entry.winfo_ismapped()
        reddit_combos = [
            widget
            for widget in dialog._reddit_opts_frame.winfo_children()
            if widget.winfo_class() == "TCombobox"
        ]
        assert len(reddit_combos) == 3
        assert all(
            str(widget.cget("state")) == "readonly"
            for widget in reddit_combos
        )
        assert str(dialog._reddit_top_window_combo.cget("state")) == "readonly"

        dialog.reddit_sort_var.set("new")
        dialog._sync_reddit_options_state()
        assert str(dialog._reddit_top_window_combo.cget("state")) == "disabled"
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# C11B — SearXNG scale rows (geometry and widget presence)
# ---------------------------------------------------------------------------

def test_searxng_scale_widgets_present(monkeypatch, tmp_path):
    """Three ttk.Scale widgets must exist inside the SearXNG options frame."""
    from tkinter import ttk

    overrides = {
        "unified_scan_dialog.provider_searxng": True,
    }
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        root.update()
        frame = dialog._searxng_opts_frame
        scale_widgets = getattr(frame, "_searxng_scale_widgets", [])
        assert len(scale_widgets) == 3, (
            f"Expected 3 ttk.Scale widgets, found {len(scale_widgets)}"
        )
        for sc in scale_widgets:
            assert isinstance(sc, ttk.Scale)
    finally:
        _destroy(root, dialog)


def test_searxng_value_labels_present(monkeypatch, tmp_path):
    """Three value labels must be attached to the SearXNG options frame."""
    overrides = {
        "unified_scan_dialog.provider_searxng": True,
    }
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        root.update()
        frame = dialog._searxng_opts_frame
        val_labels = getattr(frame, "_searxng_tuning_value_labels", [])
        assert len(val_labels) == 3, (
            f"Expected 3 value labels, found {len(val_labels)}"
        )
    finally:
        _destroy(root, dialog)


def test_searxng_controls_no_scroll_at_default_geometry(monkeypatch, tmp_path):
    """With SearXNG enabled and sliders visible, content must not overflow canvas at 960x700."""
    overrides = {
        "unified_scan_dialog.provider_searxng": True,
        "unified_scan_dialog.searxng_instance_url": "http://halcyon:8090",
        "unified_scan_dialog.searxng_query": 'intitle:"index of /"',
        "unified_scan_dialog.searxng_max_results": 1000,
    }
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        root.update()
        canvas_h = dialog._canvas.winfo_height()
        content_h = dialog._content.winfo_reqheight()
        assert content_h <= canvas_h, (
            f"SearXNG controls overflow at 960x700: "
            f"reqheight={content_h} > canvas={canvas_h}"
        )
        assert not dialog._scrollbar_visible
    finally:
        _destroy(root, dialog)


def test_searxng_scale_disabled_when_searxng_unchecked(monkeypatch, tmp_path):
    """Scale and value-label widgets must be disabled when SearXNG is unchecked."""
    from tkinter import ttk

    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        root.update()
        dialog.provider_searxng_var.set(False)
        dialog._sync_searxng_options_state()
        frame = dialog._searxng_opts_frame
        scale_widgets = getattr(frame, "_searxng_scale_widgets", [])
        val_labels = getattr(frame, "_searxng_tuning_value_labels", [])
        for sc in scale_widgets:
            assert str(sc.cget("state")) == "disabled"
        for lbl in val_labels:
            assert str(lbl.cget("state")) == "disabled"
    finally:
        _destroy(root, dialog)


def test_searxng_scale_enabled_when_searxng_checked(monkeypatch, tmp_path):
    """Scale widgets must be enabled when SearXNG is checked."""
    from tkinter import ttk

    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        root.update()
        dialog.provider_searxng_var.set(True)
        dialog._sync_searxng_options_state()
        frame = dialog._searxng_opts_frame
        scale_widgets = getattr(frame, "_searxng_scale_widgets", [])
        for sc in scale_widgets:
            assert str(sc.cget("state")) == "normal"
    finally:
        _destroy(root, dialog)


# ---------------------------------------------------------------------------
# C5.1 — Sherlock run-after-probe control in Runtime & safety
# ---------------------------------------------------------------------------

def _find_by_text(widget, text):
    """Depth-first collect descendant widgets whose -text option equals text."""
    found = []
    try:
        if widget.cget("text") == text:
            found.append(widget)
    except (tk.TclError, AttributeError):
        pass
    for child in widget.winfo_children():
        found.extend(_find_by_text(child, text))
    return found


def _find_by_text_prefix(widget, prefix):
    found = []
    try:
        if str(widget.cget("text")).startswith(prefix):
            found.append(widget)
    except (tk.TclError, AttributeError):
        pass
    for child in widget.winfo_children():
        found.extend(_find_by_text_prefix(child, prefix))
    return found


def test_sherlock_toggle_seeds_from_shard(monkeypatch, tmp_path):
    """The checkbox initializes from sherlock.run_after_probe in the config shard."""
    overrides = {"sherlock": {"run_after_probe": True}}
    root, dialog = _build_dialog(monkeypatch, tmp_path, overrides)
    try:
        assert dialog.sherlock_run_after_probe_var.get() is True
    finally:
        _destroy(root, dialog)


def test_sherlock_row_between_bulk_probe_and_extract(monkeypatch, tmp_path):
    """Sherlock checkbox + settings button render between bulk probe and bulk extract."""
    root, dialog = _build_dialog(monkeypatch, tmp_path)
    try:
        root.update()
        probe = _find_by_text(dialog.dialog, "Run bulk probe after each scan")
        sherlock_cb = _find_by_text(dialog.dialog, "Sherlock: run after probe")
        settings_btn = _find_by_text_prefix(dialog.dialog, "Sherlock settings")
        extract = _find_by_text(dialog.dialog, "Run bulk extract after each scan")

        assert probe and sherlock_cb and settings_btn and extract
        assert (
            probe[0].winfo_rooty()
            < sherlock_cb[0].winfo_rooty()
            < extract[0].winfo_rooty()
        )
    finally:
        _destroy(root, dialog)
