"""R4d Advanced model discovery, persistence, and launch wiring tests."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from experimental.analyst.ollama_contract import DiscoveredModel


_MODEL_ONE = DiscoveredModel("model-one:7b", "1" * 64)
_MODEL_TWO = DiscoveredModel("model-two:14b", "2" * 64)


class _Settings:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.writes = []

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))
        return True


def _find_button(widget: tk.Misc, text: str) -> tk.Button:
    for child in widget.winfo_children():
        if child.winfo_class() == "Button" and child.cget("text") == text:
            return child
        try:
            return _find_button(child, text)
        except LookupError:
            pass
    raise LookupError(text)


@pytest.fixture
def gui(monkeypatch):
    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    try:
        yield module, root
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_open_lists_persisted_models_without_contact_and_preselects_saved(
    gui, monkeypatch,
) -> None:
    module, root = gui
    settings = _Settings({
        "analyst.selected_model_tag": _MODEL_TWO.model_tag,
        "analyst.selected_model_digest": _MODEL_TWO.model_digest,
    })
    listed = MagicMock(return_value=(_MODEL_ONE, _MODEL_TWO))
    discover = MagicMock()
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models", listed,
    )
    monkeypatch.setattr(
        "experimental.analyst.service.discover_models", discover,
    )
    tab = module.AnalystTab(root, {"settings_manager": settings})

    tab._open_advanced()

    listed.assert_called_once_with()
    discover.assert_not_called()
    assert str(tab._model_combo.cget("state")) == "readonly"
    assert tuple(tab._model_combo.cget("values")) == (
        _MODEL_ONE.model_tag, _MODEL_TWO.model_tag,
    )
    assert tab._model_var.get() == _MODEL_TWO.model_tag


@pytest.mark.gui_smoke
def test_connect_discovers_off_thread_repopulates_and_failure_keeps_list(
    gui, monkeypatch,
) -> None:
    module, root = gui
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models",
        lambda: (_MODEL_ONE,),
    )
    worker_threads = []

    def discover():
        worker_threads.append(threading.get_ident())
        return (_MODEL_TWO,)

    monkeypatch.setattr(
        "experimental.analyst.service.discover_models", discover,
    )
    tab = module.AnalystTab(root, {"settings_manager": _Settings()})
    tab._open_advanced()
    callbacks = queue.Queue()
    tab._schedule = callbacks.put

    tab._model_connect_btn.invoke()
    callbacks.get(timeout=2.0)()

    assert worker_threads and worker_threads[0] != threading.get_ident()
    assert tuple(tab._model_combo.cget("values")) == (_MODEL_TWO.model_tag,)
    assert "Found 1 model" in tab._model_status_var.get()

    previous = tuple(tab._model_combo.cget("values"))
    error = MagicMock()
    monkeypatch.setattr(module.safe_messagebox, "showerror", error)

    def fail():
        raise RuntimeError("private transport detail")

    monkeypatch.setattr(
        "experimental.analyst.service.discover_models", fail,
    )
    tab._model_connect_btn.invoke()
    callbacks.get(timeout=2.0)()

    assert tuple(tab._model_combo.cget("values")) == previous
    error.assert_called_once_with(
        "Analyst",
        "Could not reach the model server on loopback.",
        parent=tab._advanced_dialog,
    )


@pytest.mark.gui_smoke
def test_save_persists_selected_tag_and_digest(gui, monkeypatch) -> None:
    module, root = gui
    settings = _Settings()
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models",
        lambda: (_MODEL_ONE, _MODEL_TWO),
    )
    tab = module.AnalystTab(root, {"settings_manager": settings})
    tab._open_advanced()
    tab._model_combo.current(1)

    _find_button(tab._advanced_dialog, "Save").invoke()

    assert settings.values["analyst.selected_model_tag"] == _MODEL_TWO.model_tag
    assert settings.values["analyst.selected_model_digest"] == _MODEL_TWO.model_digest
    assert tab._selected_model_tag == _MODEL_TWO.model_tag
    assert tab._selected_model_digest == _MODEL_TWO.model_digest


@pytest.mark.gui_smoke
def test_analyze_forwards_saved_pair_and_none_without_server_contact(
    gui, monkeypatch, tmp_path: Path,
) -> None:
    module, root = gui
    settings = _Settings({
        "analyst.selected_model_tag": _MODEL_TWO.model_tag,
        "analyst.selected_model_digest": _MODEL_TWO.model_digest,
    })
    discover = MagicMock()
    monkeypatch.setattr(
        "experimental.analyst.service.discover_models", discover,
    )
    launches = []

    def launch(request, **kwargs):
        launches.append((request, kwargs))
        return SimpleNamespace(run_id="a" * 32)

    monkeypatch.setattr(
        "experimental.analyst.service.create_and_launch", launch,
    )
    tab = module.AnalystTab(root, {"settings_manager": settings})
    callbacks = queue.Queue()
    tab._schedule = callbacks.put
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    tab._source_var.set(str(source))
    tab._output_var.set(str(output))

    tab._start_analysis()
    callbacks.get(timeout=2.0)()

    assert launches[0][1] == {
        "model_tag": _MODEL_TWO.model_tag,
        "model_digest": _MODEL_TWO.model_digest,
    }
    discover.assert_not_called()

    tab._selected_model_tag = None
    tab._selected_model_digest = None
    tab._start_analysis()
    callbacks.get(timeout=2.0)()

    assert launches[1][1] == {"model_tag": None, "model_digest": None}
    discover.assert_not_called()
