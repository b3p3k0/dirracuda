"""Tests for About -> User Manual transition flow."""

from __future__ import annotations

from gui.components.dashboard import DashboardWidget
import gui.dashboard.widget as dashboard_widget_module


class _DialogStub:
    def __init__(self) -> None:
        self.destroy_calls = 0

    def destroy(self) -> None:
        self.destroy_calls += 1


def test_open_user_manual_from_about_closes_dialog_then_opens_manual(monkeypatch) -> None:
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = object()
    dash.theme = object()

    calls = []
    monkeypatch.setattr(
        dashboard_widget_module,
        "open_help_manual_dialog",
        lambda parent, *, theme=None: calls.append((parent, theme)),
    )

    about_dialog = _DialogStub()
    DashboardWidget._open_user_manual_from_about(dash, about_dialog)

    assert about_dialog.destroy_calls == 1
    assert calls == [(dash.parent, dash.theme)]


def test_open_user_manual_from_about_tolerates_destroy_errors(monkeypatch) -> None:
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = object()
    dash.theme = object()

    calls = []
    monkeypatch.setattr(
        dashboard_widget_module,
        "open_help_manual_dialog",
        lambda parent, *, theme=None: calls.append((parent, theme)),
    )

    class _BrokenDialog:
        def destroy(self):
            raise RuntimeError("destroy failed")

    DashboardWidget._open_user_manual_from_about(dash, _BrokenDialog())
    assert calls == [(dash.parent, dash.theme)]


# --------------------------------------------------------------------------
# Extraction discipline (Shodan and About are satellites of widget.py)
# --------------------------------------------------------------------------

def test_the_shodan_status_vocabulary_has_one_definition() -> None:
    """Retyping it during the move is exactly the bug this guards.

    The first draft of the satellite rewrote the credits line from memory as
    "<123 credits>" instead of "<query credits: 123>", and two tests caught it.
    """
    import gui.components.dashboard_shodan as shodan
    import gui.dashboard.widget as widget

    assert (
        widget._format_shodan_status_with_credits
        is shodan._format_shodan_status_with_credits
    )
    for name in (
        "_SHODAN_STATUS_NO_KEY",
        "_SHODAN_STATUS_CHECKING",
        "_SHODAN_STATUS_UNAVAILABLE",
    ):
        assert getattr(widget, name) == getattr(shodan, name)


def test_the_credits_line_keeps_its_exact_wording() -> None:
    import gui.components.dashboard_shodan as shodan

    assert shodan._format_shodan_status_with_credits("123") == (
        "✔ Shodan API key configured <query credits: 123>"
    )
    assert shodan._format_shodan_status_with_credits("") == (
        shodan._SHODAN_STATUS_UNAVAILABLE
    )


def test_the_satellites_never_import_the_widget_at_module_level() -> None:
    """widget.py imports them, so an import back would be a cycle."""
    import ast
    import pathlib

    import gui.components.dashboard_about as about
    import gui.components.dashboard_shodan as shodan

    for module in (about, shodan):
        tree = ast.parse(pathlib.Path(module.__file__).read_text())
        names = [
            node.module or "" for node in tree.body
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name for node in tree.body
            if isinstance(node, ast.Import) for alias in node.names
        ]
        assert "gui.dashboard.widget" not in names


def test_a_patch_on_the_widget_namespace_reaches_the_about_satellite(
    monkeypatch,
) -> None:
    """The seam the two user-manual tests above rely on."""
    import gui.components.dashboard_about as about
    import gui.dashboard.widget as widget

    sentinel = object()
    monkeypatch.setattr(widget, "open_help_manual_dialog", sentinel)
    assert about._w("open_help_manual_dialog") is sentinel


def test_messagebox_resolves_through_the_shim_in_both_satellites(
    monkeypatch,
) -> None:
    import gui.components.dashboard as shim
    import gui.components.dashboard_about as about
    import gui.components.dashboard_shodan as shodan

    sentinel = object()
    monkeypatch.setattr(shim, "messagebox", sentinel)
    assert about._mb() is sentinel
    assert shodan._mb() is sentinel
