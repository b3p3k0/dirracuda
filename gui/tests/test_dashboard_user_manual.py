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
