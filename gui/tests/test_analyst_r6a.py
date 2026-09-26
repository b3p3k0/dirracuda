"""R6a Analyst read-first launcher and Runs result tests."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from experimental.analyst.service import (
    AnalystRunSummary,
    DirectoryRunRequest,
    create_directory_run,
    list_run_summaries,
)
from experimental.analyst.state import RunState
from experimental.analyst.store import open_connection
from shared.path_service import get_paths


def _summary(
    *, state: RunState, risk_level: str | None = None,
) -> AnalystRunSummary:
    return AnalystRunSummary(
        run_id="a" * 32,
        state=state,
        report_label="host12",
        mode="fast",
        created_at_utc="2026-09-18T12:00:00Z",
        updated_at_utc="2026-09-18T12:01:00Z",
        discovered_files=10,
        terminal_files=4,
        selected_files=3,
        model_reviewed_files=2,
        detector_hits=1,
        model_findings=1,
        schedule_state="available",
        resource_not_before_utc=None,
        risk_level=risk_level,
    )


def _widget_texts(widget: tk.Misc) -> set[str]:
    texts: set[str] = set()
    for child in widget.winfo_children():
        try:
            if "text" not in child.keys():
                raise tk.TclError
            text = child.cget("text")
        except tk.TclError:
            text = ""
        if text:
            texts.add(str(text))
        texts.update(_widget_texts(child))
    return texts


def _widgets(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _widgets(child)


@pytest.fixture(autouse=True)
def _isolated_profiles(tmp_path, monkeypatch):
    """N1: keep the profile selector off the real user database."""
    from experimental.analyst import profiles as profile_store

    path = tmp_path / "profiles.db"
    real_ensure = profile_store.ensure_default_profile
    real_list = profile_store.list_profiles
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **kwargs: real_ensure(path=path),
    )
    monkeypatch.setattr(
        profile_store, "list_profiles", lambda **kwargs: real_list(path=path),
    )


def test_run_summaries_surface_read_risk_and_result_labels(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "one.txt").write_text("public", encoding="utf-8")
    paths = get_paths(home_root=tmp_path / "home")
    run_id, _inventory = create_directory_run(
        DirectoryRunRequest(source, output, "host12", "fast"),
        path=paths.analyst_db_file,
        run_id_factory=lambda _size: "b" * 32,
    )
    conn = open_connection(paths.analyst_db_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO analyst_read("
            "run_id,report_schema_version,read_mode,risk_level,host_summary,"
            "likely_owner,contacts_json,files_read,files_total,flagged_files,"
            "created_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, 1, "quick", "HIGH", "Public host", None, "[]",
                1, 1, 1, "2026-09-18T12:02:00Z",
            ),
        )
        conn.execute(
            "UPDATE analyst_runs SET state='complete',completion_code='complete',"
            "finished_at_utc=?,finalization_token=? WHERE run_id=?",
            ("2026-09-18T12:02:00Z", "c" * 64, run_id),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    listed = list_run_summaries(path=paths.analyst_db_file)
    assert listed[0].risk_level == "HIGH"
    assert listed[0].result_label == "● HIGH risk"
    assert _summary(state=RunState.RUNNING).result_label == (
        "4/10 finalized · 2/3 model-reviewed"
    )
    assert _summary(state=RunState.COMPLETE).result_label == "-"


@pytest.mark.gui_smoke
def test_tab_has_read_first_main_form_and_advanced_dialog(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    focus = MagicMock()
    monkeypatch.setattr(module, "ensure_dialog_focus", focus)

    root = tk.Tk()
    root.withdraw()
    try:
        settings = MagicMock()
        settings.get_setting.return_value = False
        tab = module.AnalystTab(root, {"settings_manager": settings})
        main_texts = _widget_texts(tab._main_form)
        assert main_texts == {
            "Input Dir", "Output Dir", "Browse", "Name", "optional", "Read",
            "Quick look", "Full read",
        }
        labels = {
            str(widget.cget("text")): widget
            for widget in _widgets(tab._main_form)
            if widget.winfo_class() == "Label"
        }
        assert int(labels["Input Dir"].grid_info()["row"]) == 0
        assert int(labels["Output Dir"].grid_info()["row"]) == 1
        assert sum(
            widget.winfo_class() == "Button" and widget.cget("text") == "Browse"
            for widget in _widgets(tab._main_form)
        ) == 2
        assert tab._analyze_btn.cget("text") == "Analyze"
        assert tab._advanced_btn.cget("text") == "Advanced..."
        assert {
            "Source", "Saved scan", "Model server", "Model",
            "Offer a quick review after an extraction",
        }.isdisjoint(_widget_texts(tab.frame))

        tab._source_var.set("/tmp/extracted/host12")
        assert tab._label_var.get() == "host12"
        radios = {
            str(widget.cget("text")): widget
            for widget in _widgets(tab._main_form)
            if widget.winfo_class() == "Radiobutton"
        }
        radios["Quick look"].invoke()
        assert tab._mode_var.get() == "fast"
        radios["Full read"].invoke()
        assert tab._mode_var.get() == "deep"

        assert tab._runs.heading("progress", "text") == "Result"
        assert str(tab._runs.cget("selectmode")) == "extended"
        assert tab._select_all_btn.cget("text") == "Select all"
        assert tab._export_btn.cget("state") == "disabled"

        tab._open_advanced()
        dialog = tab._advanced_dialog
        assert dialog is not None
        advanced_texts = _widget_texts(dialog)
        # N1: the disabled Local/Remote radios, the Host/Port entries and the
        # Test button are replaced by a live profile selector plus "Manage...".
        assert {
            "Source", "A folder", "From a saved scan",
            "Saved scan", "Reload", "Model server", "Manage\u2026", "Model",
            "Offer a quick review after an extraction", "Cancel", "Save",
        } <= advanced_texts
        assert not (
            {"Local", "Remote AI box", "later card", "Host", "Port", "Test"}
            & advanced_texts
        )
        assert "Output Dir" not in advanced_texts
        assert "Output folder" not in advanced_texts
        # The selector is live and defaults to the loopback profile.
        assert tab._profile_combo is not None
        assert str(tab._profile_combo.cget("state")) == "readonly"
        assert "127.0.0.1:11434" in tab._profile_var.get()
        assert tab._selected_endpoint() == "http://127.0.0.1:11434"
        assert tab._profile_note_var.get() == ""
        assert dialog.grab_current() == dialog
        focus.assert_called_once_with(dialog, root)
    finally:
        root.destroy()
