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
