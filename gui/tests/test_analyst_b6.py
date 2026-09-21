from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest


class _Settings:
    def __init__(self, initial=None):
        self._store = dict(initial or {})

    def get_setting(self, key, default=None):
        return self._store.get(key, default)

    def set_setting(self, key, value):
        self._store[key] = value


@pytest.fixture
def tab(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()

    def _make(settings):
        widget = module.AnalystTab(root, {"settings_manager": settings})
        return widget

    try:
        yield _make, module
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_browse_source_starts_in_persisted_dir_and_persists_selection(
    tab, tmp_path, monkeypatch,
) -> None:
    make, module = tab
    saved = tmp_path / "source"
    saved.mkdir()
    chosen = tmp_path / "picked"
    chosen.mkdir()
    settings = _Settings({"analyst.input_folder": str(saved)})
    widget = make(settings)

    captured = {}

    def fake_askdirectory(**kwargs):
        captured.update(kwargs)
        return str(chosen)

    monkeypatch.setattr(module.filedialog, "askdirectory", fake_askdirectory)

    widget._browse_source()

    assert captured["initialdir"] == str(saved)
    assert widget._source_var.get() == str(chosen)
    assert settings.get_setting("analyst.input_folder") == str(chosen)


@pytest.mark.gui_smoke
def test_browse_initialdir_prefers_valid_current_field(tab, tmp_path) -> None:
    make, _module = tab
    saved = tmp_path / "saved"
    saved.mkdir()
    current = tmp_path / "current"
    current.mkdir()
    widget = make(_Settings({"analyst.input_folder": str(saved)}))
    widget._source_var.set(str(current))

    assert (
        widget._browse_initialdir(widget._source_var.get(), "analyst.input_folder")
        == str(current)
    )


@pytest.mark.gui_smoke
def test_browse_initialdir_none_when_nothing_valid(tab, tmp_path) -> None:
    make, _module = tab
    widget = make(_Settings({"analyst.input_folder": str(tmp_path / "missing")}))

    assert widget._browse_initialdir("", "analyst.input_folder") is None
