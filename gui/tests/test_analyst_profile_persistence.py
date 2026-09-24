"""The selected server persists, and a stale model cannot ride across servers.

Both bugs found in real use: the selector reverted to the first profile on
reopen, which made a reported model look like a digest model with a missing
digest, which surfaced as "the source or output directory is not supported".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from experimental.analyst import profiles as profile_store
from experimental.analyst.service import AnalystServiceError, ServiceFailure
from experimental.analyst.store import initialize_database
from gui.components.experimental_features import analyst_profile_editor as editor

_MIMIR = "http://100.125.197.36:9292"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "analyst.db"
    initialize_database(path)
    real_list = profile_store.list_profiles
    real_ensure = profile_store.ensure_default_profile
    monkeypatch.setattr(profile_store, "list_profiles",
                        lambda **k: real_list(path=path))
    monkeypatch.setattr(profile_store, "ensure_default_profile",
                        lambda **k: real_ensure(path=path))
    return path


class _Var:
    def __init__(self, value=""):
        self._v = value

    def set(self, value):
        self._v = value

    def get(self):
        return self._v


class _Combo:
    def __init__(self):
        self.values = ()
        self._c = -1

    def configure(self, values=None, **kwargs):
        if values is not None:
            self.values = tuple(values)

    def current(self, index=None):
        if index is None:
            return self._c
        self._c = index
        return index


class _Tab:
    def __init__(self):
        self._profile_var = _Var()
        self._profile_note_var = _Var()
        self._profile_choices = ()
        self._profile_combo = _Combo()
        self._advanced_dialog = None
        self._model_choices = ["one"]
        self._selected_model_tag = "gpt-oss-120b"
        self._selected_model_digest = None
        self._model_var = _Var("gpt-oss-120b")
        self._model_combo = _Combo()
        self._model_status_var = _Var()


def test_a_saved_profile_is_restored_not_the_first_one(db: Path):
    profile_store.ensure_default_profile(path=db)
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    tab = _Tab()
    editor.refresh_profile_choices(tab, select_id=mimir.profile_id)
    assert editor.selected_profile(tab).name == "mimir"


def test_without_a_saved_id_the_first_profile_is_used(db: Path):
    profile_store.ensure_default_profile(path=db)
    profile_store.create_profile("mimir", _MIMIR, backend_kind="openai", path=db)
    tab = _Tab()
    editor.refresh_profile_choices(tab)
    assert editor.selected_profile(tab).is_loopback


def test_switching_server_clears_a_model_from_the_other_one(db: Path):
    profile_store.ensure_default_profile(path=db)
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    tab = _Tab()
    editor.refresh_profile_choices(tab, select_id=mimir.profile_id)
    editor.clear_model_if_server_changed(tab)

    tab._profile_combo.current(0)
    editor.clear_model_if_server_changed(tab)

    assert tab._selected_model_tag is None
    assert tab._selected_model_digest is None
    assert tab._model_var.get() == ""
    assert "Connect / Refresh" in tab._model_status_var.get()


def test_staying_on_one_server_keeps_the_model(db: Path):
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    tab = _Tab()
    editor.refresh_profile_choices(tab, select_id=mimir.profile_id)
    editor.clear_model_if_server_changed(tab)
    editor.clear_model_if_server_changed(tab)
    assert tab._selected_model_tag == "gpt-oss-120b"


# --------------------------------------------------------------------------
# The error that sent the operator to look at their folders
# --------------------------------------------------------------------------

def test_a_model_identity_mismatch_is_not_reported_as_a_directory_problem():
    from experimental.analyst.service import _run_model_identity

    with pytest.raises(ValueError):
        _run_model_identity("gpt-oss-120b", None, "digest")
    assert ServiceFailure.MODEL_IDENTITY.value == "model_identity"
    assert ServiceFailure.MODEL_IDENTITY is not ServiceFailure.CONTRACT


def test_the_gui_explains_a_model_identity_failure():
    from gui.components.experimental_features import analyst_tab

    message = analyst_tab._CREATE_FAILURE_MESSAGES["model_identity"]
    assert "server" in message.lower()
    assert "directory" not in message.lower()


def test_a_reported_model_persists_without_a_digest():
    """Requiring a digest here silently dropped every llama.cpp selection."""
    from gui.components.experimental_features.analyst_tab import AnalystTab

    class _Settings:
        def __init__(self, values):
            self._v = values

        def get_setting(self, key, default=None):
            return self._v.get(key, default)

    loaded = AnalystTab._load_selected_model(
        _Settings({
            "analyst.selected_model_tag": "gpt-oss-120b",
            "analyst.selected_model_digest": None,
        })
    )
    assert loaded == ("gpt-oss-120b", None)


def test_a_digest_model_still_round_trips():
    from gui.components.experimental_features.analyst_tab import AnalystTab

    class _Settings:
        def __init__(self, values):
            self._v = values

        def get_setting(self, key, default=None):
            return self._v.get(key, default)

    loaded = AnalystTab._load_selected_model(
        _Settings({
            "analyst.selected_model_tag": "qwen3.6:27b",
            "analyst.selected_model_digest": "a" * 64,
        })
    )
    assert loaded == ("qwen3.6:27b", "a" * 64)


# --------------------------------------------------------------------------
# The selector widget dies with the dialog; the choice must not die with it
# --------------------------------------------------------------------------

class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = value


class _DeadCombo(_Combo):
    """A combobox whose window is gone, as after the dialog is destroyed."""

    def current(self, index=None):
        raise RuntimeError('invalid command name ".!combobox"')


def test_a_destroyed_selector_does_not_fall_back_to_the_first_profile(db: Path):
    """The exact launch bug: Analyze ran against Ollama after saving mimir."""
    profile_store.ensure_default_profile(path=db)
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    tab = _Tab()
    tab._context = {"settings_manager": _Settings(
        {"analyst.selected_profile_id": mimir.profile_id}
    )}
    editor.refresh_profile_choices(tab, select_id=mimir.profile_id)

    tab._profile_combo = _DeadCombo()
    assert editor.selected_profile(tab).name == "mimir"

    tab._profile_combo = None
    assert editor.selected_profile(tab).name == "mimir"
    assert editor.selected_endpoint(tab) == _MIMIR


def test_a_launch_before_the_dialog_opens_still_finds_the_saved_server(db: Path):
    profile_store.ensure_default_profile(path=db)
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    tab = _Tab()
    tab._profile_combo = None
    tab._context = {"settings_manager": _Settings(
        {"analyst.selected_profile_id": mimir.profile_id}
    )}
    assert editor.selected_profile(tab).name == "mimir"


def test_a_failed_store_read_is_not_retried_behind_the_tab(monkeypatch):
    """refresh_profile_choices already tried. Do not go back to the real DB."""
    tab = _Tab()
    tab._context = {"settings_manager": _Settings()}
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **k: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    monkeypatch.setattr(
        profile_store, "list_profiles",
        lambda **k: pytest.fail("the store must not be read a second time"),
    )
    editor.refresh_profile_choices(tab)
    assert editor.selected_profile(tab) is None


# --------------------------------------------------------------------------
# A reported catalogue is never persisted, so the tab must hold onto it
# --------------------------------------------------------------------------

@pytest.fixture
def gui(monkeypatch):
    import tkinter as tk

    from gui.components.experimental_features import analyst_tab as module

    monkeypatch.setattr(module.AnalystTab, "_refresh_runs", lambda _self: None)
    root = tk.Tk()
    root.withdraw()
    try:
        yield module, root
    finally:
        root.destroy()


def _click(widget, text):
    import tkinter as tk

    for child in widget.winfo_children():
        if isinstance(child, tk.Button) and child.cget("text") == text:
            child.invoke()
            return True
        if _click(child, text):
            return True
    return False


@pytest.mark.gui_smoke
def test_reopening_keeps_the_model_list_the_server_reported(
    gui, db: Path, monkeypatch,
) -> None:
    module, root = gui
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    settings = _Settings({"analyst.selected_profile_id": mimir.profile_id})
    listed = []
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models",
        lambda **kwargs: listed.append(kwargs) or (),
    )
    tab = module.AnalystTab(root, {"settings_manager": settings})

    tab._open_advanced()
    reported = (editor.ReportedModel("a-8b"), editor.ReportedModel("b-70b"))
    tab._finish_model_discovery(tab._advanced_dialog, reported, None)
    assert tuple(tab._model_combo.cget("values")) == ("a-8b", "b-70b")
    assert _click(tab._advanced_dialog, "Save")

    tab._open_advanced()
    assert tuple(tab._model_combo.cget("values")) == ("a-8b", "b-70b")
    assert listed == [{"endpoint": _MIMIR}]
    assert _click(tab._advanced_dialog, "Cancel")


@pytest.mark.gui_smoke
def test_after_saving_the_dialog_a_launch_targets_the_chosen_server(
    gui, db: Path, monkeypatch,
) -> None:
    """The reported blocker: Analyze said the model did not match the server."""
    module, root = gui
    profile_store.ensure_default_profile(path=db)
    mimir = profile_store.create_profile(
        "mimir", _MIMIR, backend_kind="openai", plaintext_ack=True, path=db,
    )
    settings = _Settings()
    monkeypatch.setattr(
        "experimental.analyst.service.list_discovered_models",
        lambda **kwargs: (),
    )
    tab = module.AnalystTab(root, {"settings_manager": settings})

    tab._open_advanced()
    labels = list(tab._profile_combo.cget("values"))
    index = next(i for i, text in enumerate(labels) if "mimir" in text)
    tab._profile_combo.current(index)
    tab._profile_selected()
    tab._finish_model_discovery(
        tab._advanced_dialog, (editor.ReportedModel("gpt-oss-120b"),), None,
    )
    assert _click(tab._advanced_dialog, "Save")

    assert tab._advanced_dialog is None
    assert tab._selected_backend_kind() == "openai"
    assert tab._selected_endpoint() == _MIMIR
    assert tab._selected_model_tag == "gpt-oss-120b"
    assert tab._selected_model_digest is None
    assert settings.values["analyst.selected_profile_id"] == mimir.profile_id
