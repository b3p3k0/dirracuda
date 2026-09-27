"""Real Tk integration against disposable library and config state."""
import json
import tkinter as tk
from types import SimpleNamespace

import pytest

from experimental.dorkbook import store
from experimental.dorkbook.defaults import read_defaults
from gui.components.dorkbook_events import bind_self_hosted_query
from gui.components.dorkbook_window import DorkbookWindow, _EntryEditorDialog
from gui.components.scan_dork_editor_dialog import ScanDorkEditorDialog


@pytest.fixture
def ui(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"se_dork": {"default_query": "old web"}}))
    db = tmp_path / "dorkbook.db"
    store.init_db(db)
    conn = store.open_connection(db)
    web_id = store.create_entry(conn, None, "Web test", 'intitle:"Index of" books', "A teaching example",
                                provider="self_hosted", topic="Books")
    custom_id = store.create_entry(conn, "HTTP", "Shodan test", 'http.title:"Index of" test', "Example")
    conn.commit()
    conn.close()
    window = DorkbookWindow(root, db_path=db, scan_query_config_path=str(cfg))
    root.update()
    try:
        yield root, window, cfg, web_id, custom_id
        assert not errors, errors
    finally:
        root.destroy()


def test_grouped_filter_preview_and_explicit_apply(ui):
    root, window, cfg, web_id, _ = ui
    assert window.tree.get_children("") == ("shodan", "self_hosted")
    assert window.tree.item("shodan", "open")
    assert window.tree.item("self_hosted", "open")
    window.tree.selection_set(str(web_id))
    root.update()
    assert "Self-hosted Search" in window.preview.get("1.0", "end")
    assert read_defaults(cfg)["self_hosted"] == "old web"
    window._on_apply()
    root.update()
    assert read_defaults(cfg)["self_hosted"] == 'intitle:"Index of" books'
    assert window.tree.item(str(web_id), "values")[-1] == "✓"
    window.search_var.set("teaching")
    window._load_entries()
    assert window.tree.get_children("self_hosted") == (str(web_id),)
    window.focus_provider("shodan")
    assert window.search_var.get() == ""
    assert window.tree.selection() == ("shodan",)


def test_apply_refreshes_open_scan_and_shodan_editor(ui):
    root, window, cfg, web_id, custom_id = ui
    scan = SimpleNamespace(dialog=tk.Toplevel(root), config_path=cfg, searxng_query_var=tk.StringVar(value="manual"),
                           _self_hosted_default="old web")
    bind_self_hosted_query(scan)
    editor = ScanDorkEditorDialog(root, str(cfg))
    root.update()
    editor.ftp_dork_var.set("manual ftp")
    window.tree.selection_set(str(web_id))
    window._on_apply()
    assert scan.searxng_query_var.get() == 'intitle:"Index of" books'
    window.tree.selection_set(str(custom_id))
    window._on_apply()
    assert editor.http_dork_var.get() == 'http.title:"Index of" test'
    assert editor.ftp_dork_var.get() == "manual ftp"
    assert editor._validate_and_save()
    assert read_defaults(cfg)["shodan:HTTP"] == 'http.title:"Index of" test'
    assert read_defaults(cfg)["shodan:FTP"] == "manual ftp"
    # Dorkbook survives closing the contextual editor.
    editor.dialog.destroy()
    assert window.window.winfo_exists()


def test_add_dialog_switches_provider_and_records_topic(ui):
    root, window, _, _, _ = ui
    editor = _EntryEditorDialog(window.window, window.theme, title="Add Dork")
    editor.provider_var.set("Self-hosted Search")
    assert str(editor.protocol_entry.cget("state")) == "disabled"
    editor.query_var.set('intitle:"Directory listing"')
    editor.topic_var.set("General")
    editor._on_save()
    assert editor.result["provider"] == "self_hosted"
    assert editor.result["protocol"] is None
    assert editor.result["topic"] == "General"


def test_edit_and_delete_do_not_rewrite_applied_query(ui, monkeypatch):
    root, window, cfg, web_id, _ = ui
    window.tree.selection_set(str(web_id))
    window._on_apply()
    applied = read_defaults(cfg)["self_hosted"]
    monkeypatch.setattr(window, "_show_entry_editor", lambda **kwargs: {
        "provider": "self_hosted", "protocol": None, "nickname": "Changed dork",
        "query": "different query", "topic": "Books", "notes": "Changed note"})
    window._on_edit()
    assert window.rows[str(web_id)]["query"] == "different query"
    assert window.tree.item(str(web_id), "values")[-1] == ""
    assert read_defaults(cfg)["self_hosted"] == applied
    monkeypatch.setattr(window, "_confirm_delete", lambda row: True)
    window._on_delete()
    assert not window.tree.exists(str(web_id))
    assert read_defaults(cfg)["self_hosted"] == applied


def test_library_stays_browsable_when_saved_default_is_invalid(ui):
    root, window, cfg, web_id, _ = ui
    cfg.write_text(json.dumps({"se_dork": {"default_query": []}}))
    window._load_entries()
    assert window.tree.exists(str(web_id))
    assert "Saved defaults unavailable" in window.status_var.get()
    assert window.tree.item(str(web_id), "values")[-1] == ""
