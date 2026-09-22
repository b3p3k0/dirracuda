"""N1: the Analyst tab drives a real profile, and says when one is held.

Covers `N1_CARD.md` acceptance 6 (the host/port controls drive a real profile)
and the UI half of acceptance 7 (a remote profile is visibly not usable yet).
"""

from __future__ import annotations

import tkinter as tk

import pytest

from experimental.analyst import profiles as profile_store
from experimental.analyst.profiles import BackendKind
from gui.components.experimental_features import analyst_profile_editor as editor
from gui.components.experimental_features import analyst_tab

_REMOTE = "http://100.125.197.36:9292"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the profile store at a throwaway database."""
    path = tmp_path / "analyst.db"
    real_list = profile_store.list_profiles
    real_ensure = profile_store.ensure_default_profile
    monkeypatch.setattr(
        profile_store, "list_profiles",
        lambda **kwargs: real_list(path=path),
    )
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **kwargs: real_ensure(path=path),
    )
    return path


class _FakeCombo:
    """Minimal stand-in for the profile ttk.Combobox."""

    def __init__(self):
        self.values = ()
        self._current = -1

    def configure(self, values=None, **kwargs):
        if values is not None:
            self.values = tuple(values)

    def current(self, index=None):
        if index is None:
            return self._current
        self._current = index
        return index


class _FakeVar:
    def __init__(self, value=""):
        self._value = value

    def set(self, value):
        self._value = value

    def get(self):
        return self._value


class _FakeTab:
    """Enough of AnalystTab for the satellite's tab-side helpers."""

    def __init__(self):
        self._profile_var = _FakeVar()
        self._profile_note_var = _FakeVar()
        self._profile_choices = ()
        self._profile_combo = _FakeCombo()
        self._advanced_dialog = None


# --------------------------------------------------------------------------
# describe_profile
# --------------------------------------------------------------------------

def test_describe_loopback_profile_has_no_held_marker(db):
    profile = profile_store.ensure_default_profile(path=db)
    text = editor.describe_profile(profile)
    assert "http://127.0.0.1:11434" in text
    assert "This machine" in text
    assert "held until N3" not in text


def test_describe_remote_profile_is_marked_held(db):
    profile = profile_store.create_profile("mimir", _REMOTE, path=db)
    text = editor.describe_profile(profile)
    assert "mimir" in text
    assert "Tailscale / CGNAT" in text
    assert "held until N3" in text


def test_describe_labels_each_address_class(db):
    cases = {
        "http://192.168.1.242:11434": "Private LAN",
        "http://8.8.8.8:11434": "Public internet",
        "http://box.lan:11434": "Hostname (not resolved)",
    }
    for index, (url, label) in enumerate(cases.items()):
        profile = profile_store.create_profile(f"p{index}", url, path=db)
        assert label in editor.describe_profile(profile)


# --------------------------------------------------------------------------
# Tab-side helpers
# --------------------------------------------------------------------------

def test_short_label_stays_compact_for_the_narrow_selector(db):
    profile = profile_store.create_profile("mimir", _REMOTE, path=db)
    short = editor.describe_profile_short(profile)
    assert short == "mimir — http://100.125.197.36:9292  — held"
    assert len(short) < len(editor.describe_profile(profile))


def test_short_label_has_no_held_marker_for_loopback(db):
    profile = profile_store.ensure_default_profile(path=db)
    assert editor.describe_profile_short(profile) == (
        "Local Ollama — http://127.0.0.1:11434"
    )


def test_refresh_creates_and_selects_the_loopback_default(db):
    tab = _FakeTab()
    editor.refresh_profile_choices(tab)
    assert len(tab._profile_choices) == 1
    assert tab._profile_choices[0].is_loopback
    assert tab._profile_combo.current() == 0
    assert "127.0.0.1" in tab._profile_var.get()
    assert tab._profile_note_var.get() == ""


def test_refresh_lists_every_profile_and_can_preselect(db):
    profile_store.ensure_default_profile(path=db)
    remote = profile_store.create_profile("mimir", _REMOTE, path=db)
    tab = _FakeTab()
    editor.refresh_profile_choices(tab, select_id=remote.profile_id)
    assert len(tab._profile_combo.values) == 2
    assert editor.selected_profile(tab).profile_id == remote.profile_id


def test_selecting_a_remote_profile_shows_the_held_note(db):
    profile_store.ensure_default_profile(path=db)
    remote = profile_store.create_profile("mimir", _REMOTE, path=db)
    tab = _FakeTab()
    editor.refresh_profile_choices(tab, select_id=remote.profile_id)
    assert tab._profile_note_var.get() == editor.REMOTE_HELD_NOTE
    assert "not usable yet" in editor.REMOTE_HELD_NOTE


def test_selecting_a_loopback_profile_clears_the_note(db):
    default = profile_store.ensure_default_profile(path=db)
    profile_store.create_profile("mimir", _REMOTE, path=db)
    tab = _FakeTab()
    editor.refresh_profile_choices(tab, select_id=default.profile_id)
    assert tab._profile_note_var.get() == ""


def test_selected_profile_falls_back_to_the_first_entry(db):
    profile_store.ensure_default_profile(path=db)
    tab = _FakeTab()
    editor.refresh_profile_choices(tab)
    tab._profile_combo._current = -1
    assert editor.selected_profile(tab) is tab._profile_choices[0]


def test_selected_profile_is_none_when_the_store_is_unreadable(monkeypatch):
    tab = _FakeTab()
    monkeypatch.setattr(
        profile_store, "ensure_default_profile",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    editor.refresh_profile_choices(tab)
    assert tab._profile_choices == ()
    assert editor.selected_profile(tab) is None
    assert tab._profile_var.get() == ""


def test_manage_profiles_is_a_no_op_without_a_dialog(db):
    tab = _FakeTab()
    editor.manage_profiles(tab)  # must not raise


# --------------------------------------------------------------------------
# The dead controls are gone (acceptance 6)
# --------------------------------------------------------------------------

def test_the_disabled_server_controls_no_longer_exist():
    source = open(analyst_tab.__file__, "r", encoding="utf-8").read()
    for dead in ("_server_kind_var", "_server_host_var", "_server_port_var"):
        assert dead not in source, f"{dead} is still defined"
    assert "later card" not in source


def test_the_tab_exposes_the_profile_selector():
    source = open(analyst_tab.__file__, "r", encoding="utf-8").read()
    assert "_profile_combo" in source
    assert "_manage_profiles" in source
    assert "discover_models(endpoint=endpoint)" in source


def test_profile_helpers_live_in_the_satellite_not_the_tab():
    """RB-3: analyst_tab must not grow the profile UI inline."""
    source = open(analyst_tab.__file__, "r", encoding="utf-8").read()
    assert len(source.splitlines()) <= 1500
    for name in (
        "refresh_profile_choices",
        "selected_profile",
        "profile_selected",
        "manage_profiles",
    ):
        assert hasattr(editor, name)


def test_the_satellite_does_not_import_the_tab_at_module_scope():
    """One-way import: analyst_tab -> analyst_profile_editor.

    A module-scope import back into analyst_tab would make the pair circular,
    so every reference to it must sit inside a function body.
    """
    import ast

    with open(editor.__file__, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=editor.__file__)
    for node in tree.body:
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
        assert not any("analyst_tab" in name for name in names), (
            f"module-scope import of analyst_tab at line {node.lineno}"
        )


# --------------------------------------------------------------------------
# Dialog smoke (needs a display)
# --------------------------------------------------------------------------

@pytest.mark.gui_smoke
def test_profile_editor_dialog_opens_and_lists_profiles(db):
    profile_store.ensure_default_profile(path=db)
    profile_store.create_profile("mimir", _REMOTE, path=db)
    root = tk.Tk()
    root.withdraw()
    try:
        dialog = editor.ProfileEditorDialog(root, db_path=db)
        try:
            root.update_idletasks()
            assert dialog._listbox.size() == 2
            dialog._listbox.selection_clear(0, tk.END)
            dialog._listbox.selection_set(1)
            dialog._selection_changed()
            assert dialog._note_var.get() == editor.REMOTE_HELD_NOTE
        finally:
            dialog._close()
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_profile_form_rejects_a_malformed_port(db, monkeypatch):
    errors = []

    class _Box:
        @staticmethod
        def showerror(title, message, **kwargs):
            errors.append(message)

        @staticmethod
        def showinfo(title, message, **kwargs):
            pass

        @staticmethod
        def askyesno(title, message, **kwargs):
            return True

    monkeypatch.setattr(analyst_tab, "safe_messagebox", _Box)
    root = tk.Tk()
    root.withdraw()
    try:
        form = editor._ProfileFormDialog.__new__(editor._ProfileFormDialog)
        form.result = None
        form._dialog = tk.Toplevel(root)
        form._name_var = tk.StringVar(value="bad")
        form._scheme_var = tk.StringVar(value="http")
        form._host_var = tk.StringVar(value="127.0.0.1")
        form._port_var = tk.StringVar(value="99999")
        form._backend_var = tk.StringVar(value="ollama")
        form._save()
        assert form.result is None
        assert errors and "not usable" in errors[0]
    finally:
        root.destroy()


@pytest.mark.gui_smoke
def test_profile_form_saves_a_valid_remote_address(db):
    root = tk.Tk()
    root.withdraw()
    try:
        form = editor._ProfileFormDialog.__new__(editor._ProfileFormDialog)
        form.result = None
        form._dialog = tk.Toplevel(root)
        form._name_var = tk.StringVar(value="mimir")
        form._scheme_var = tk.StringVar(value="http")
        form._host_var = tk.StringVar(value="100.125.197.36")
        form._port_var = tk.StringVar(value="9292")
        form._backend_var = tk.StringVar(value="openai")
        form._save()
        assert form.result == {
            "name": "mimir",
            "endpoint": _REMOTE,
            "backend_kind": "openai",
        }
        assert BackendKind(form.result["backend_kind"]) is BackendKind.OPENAI_COMPAT
    finally:
        root.destroy()
