"""Rendered Start Scan launch and mixed-provider default checks with temp state."""
from experimental.dorkbook.defaults import read_defaults
from gui.components import dorkbook_window
from gui.tests.test_unified_scan_dialog_layout import _build_dialog, _destroy

import pytest


@pytest.mark.parametrize("geometry", ["840x680", "960x720", "1200x800"])
def test_dorkbook_buttons_align_at_supported_sizes(monkeypatch, tmp_path, geometry):
    root, dialog = _build_dialog(monkeypatch, tmp_path, {
        "unified_scan_dialog.provider_shodan": True,
        "unified_scan_dialog.provider_searxng": True,
    })
    try:
        dialog.dialog.geometry(geometry)
        root.update()
        shodan = dialog._shodan_opts_frame._dorkbook_button
        web = dialog._searxng_opts_frame._dorkbook_button
        assert shodan.cget("text") == web.cget("text") == "Dorkbook..."
        assert shodan.winfo_width() == web.winfo_width()
        assert shodan.winfo_height() == web.winfo_height()
        assert shodan.winfo_rootx() == web.winfo_rootx()
        assert web.winfo_rootx() >= dialog._searxng_query_entry.winfo_rootx() + dialog._searxng_query_entry.winfo_width()
        assert abs((web.winfo_rooty() + web.winfo_height() / 2) -
                   (dialog._searxng_query_entry.winfo_rooty() + dialog._searxng_query_entry.winfo_height() / 2)) <= 2
        assert web.winfo_rootx() + web.winfo_width() <= dialog.dialog.winfo_rootx() + dialog.dialog.winfo_width()
    finally:
        _destroy(root, dialog)


def test_both_buttons_reuse_library_and_preserve_mixed_provider_queries(monkeypatch, tmp_path):
    root, dialog = _build_dialog(monkeypatch, tmp_path, {
        "unified_scan_dialog.provider_shodan": True,
        "unified_scan_dialog.provider_searxng": True,
        "unified_scan_dialog.searxng_instance_url": "http://search.example",
    })
    monkeypatch.setattr(dorkbook_window, "_WINDOW_INSTANCE", None)
    show = dorkbook_window.show_dorkbook_window
    settings = dialog._settings_manager
    settings.get_window_setting = lambda _name, _key, default=None: default
    settings.set_window_setting = lambda *_args: None
    monkeypatch.setattr(dorkbook_window, "show_dorkbook_window",
                        lambda *args, **kwargs: show(*args, db_path=tmp_path / "dorks.db", **kwargs))
    # Fail instead of blocking the test on an unexpected modal error.
    monkeypatch.setattr(dorkbook_window.messagebox, "showerror",
                        lambda *args, **kwargs: pytest.fail(str(args)))
    before = read_defaults(dialog.config_path)
    caps = (dialog.smb_max_results_var.get(), dialog.ftp_max_results_var.get(),
            dialog.http_max_results_var.get(), dialog.searxng_max_results_var.get())
    try:
        dialog._shodan_opts_frame._dorkbook_button.invoke()
        root.update()
        window = dorkbook_window._WINDOW_INSTANCE
        assert window.tree.selection() == ("shodan",)
        ebook = next(row for row in window.rows.values() if row["builtin_key"] == "builtin_shodan_epub")
        window.tree.selection_set(str(ebook["entry_id"]))
        window._on_apply()
        assert read_defaults(dialog.config_path) == {**before, "shodan:HTTP": ebook["query"]}

        dialog._searxng_opts_frame._dorkbook_button.invoke()
        root.update()
        assert dorkbook_window._WINDOW_INSTANCE is window
        assert window.tree.selection() == ("self_hosted",)
        assert window.tree.get_children("") == ("shodan", "self_hosted")
        music = next(row for row in window.rows.values() if row["builtin_key"] == "builtin_self_hosted_music_folders")
        window.tree.selection_set(str(music["entry_id"]))
        window._on_apply()
        assert read_defaults(dialog.config_path) == {
            **before, "shodan:HTTP": ebook["query"], "self_hosted": music["query"]}
        assert dialog.searxng_query_var.get() == music["query"]
        assert dialog.provider_shodan_var.get() and dialog.provider_searxng_var.get()
        assert caps == (dialog.smb_max_results_var.get(), dialog.ftp_max_results_var.get(),
                        dialog.http_max_results_var.get(), dialog.searxng_max_results_var.get())
        request = dialog._build_scan_request()
        assert set(request["providers"]) == {"shodan", "searxng"}
        assert request["searxng_query"] == music["query"]
        assert dialog.result is None  # Applying did not start the scan.
        window._on_close()
    finally:
        _destroy(root, dialog)
