"""Focused layout tests for the Accessories dialog shell."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_accessories_dialog_default_width_allows_sherlock_user_colors(monkeypatch):
    """The Accessories shell must be wide enough for Sherlock's User3 picker."""
    from gui.components import experimental_features_dialog as dialog_mod

    captured_geometry = []

    class _FakeWidget:
        def __init__(self, *args, **kwargs):
            pass

        def pack(self, *args, **kwargs):
            pass

    class _FakeToplevel(_FakeWidget):
        def title(self, title):
            pass

        def geometry(self, value):
            captured_geometry.append(value)

        def resizable(self, *args):
            pass

        def transient(self, parent):
            pass

        def protocol(self, *args):
            pass

        def destroy(self):
            pass

        def winfo_exists(self):
            return True

    monkeypatch.setattr(dialog_mod.tk, "Toplevel", _FakeToplevel)
    monkeypatch.setattr(dialog_mod.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(dialog_mod.tk, "Button", _FakeWidget)
    monkeypatch.setattr(dialog_mod.ttk, "Notebook", _FakeWidget)
    monkeypatch.setattr(dialog_mod, "add_shortcut_hint", lambda *a, **kw: None)
    monkeypatch.setattr(dialog_mod, "bind_submit_shortcuts", lambda *a, **kw: None)
    monkeypatch.setattr(dialog_mod, "bind_close_shortcuts", lambda *a, **kw: None)
    monkeypatch.setattr(dialog_mod, "ensure_dialog_focus", lambda *a, **kw: None)
    monkeypatch.setattr(
        "gui.components.experimental_features.registry.build_all_tabs",
        lambda *a, **kw: None,
    )

    dialog = dialog_mod.ExperimentalFeaturesDialog.__new__(
        dialog_mod.ExperimentalFeaturesDialog
    )
    dialog._theme = MagicMock()
    dialog._warning_frame_built = False
    dialog.dismiss_var = None
    monkeypatch.setattr(dialog, "_build_warning_section", lambda *a: None)

    dialog._build(MagicMock(), {}, MagicMock())

    assert captured_geometry == [dialog_mod._DIALOG_GEOMETRY]
    assert dialog_mod._DIALOG_GEOMETRY.startswith("655x")
