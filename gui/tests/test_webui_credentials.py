"""Focused tests for trusted desktop Web UI credential reset."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.components.experimental_features.webui_tab import WebUITab


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Button:
    def __init__(self):
        self.states = []

    def configure(self, **kwargs):
        self.states.append(kwargs.get("state"))


class _Frame:
    def winfo_exists(self):
        return True

    def after(self, _delay, callback):
        callback()


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


def _tab(*, existing=True, password="new-password-passphrase", confirmation=None):
    tab = WebUITab.__new__(WebUITab)
    tab.frame = _Frame()
    tab._cred_dialog = _Frame()
    tab._multi_cred_error = False
    tab._creds_existed_at_open = existing
    tab._stored_username_at_open = "admin" if existing else None
    tab._cred_username_var = _Var("admin")
    tab._cred_password_var = _Var(password)
    tab._cred_confirm_password_var = _Var(
        password if confirmation is None else confirmation
    )
    tab._cred_status_var = _Var("Ready")
    tab._save_creds_btn = _Button()
    tab._schedule_dialog_ui = lambda callback: callback()
    tab._schedule_ui = lambda callback: callback()
    tab._refresh_status = Mock()
    tab._get_webui_cfg = lambda: SimpleNamespace(
        bind_address="127.0.0.1",
        port=2600,
    )
    tab._normalize_cfg_error = lambda detail: str(detail)
    return tab


@pytest.fixture(autouse=True)
def _immediate_threads(monkeypatch):
    monkeypatch.setattr(
        "gui.components.experimental_features.webui_tab.threading.Thread",
        _ImmediateThread,
    )
    monkeypatch.setattr(
        "experimental.webui.auth.check_credential_store",
        lambda: None,
    )


def _patch_post_save(
    monkeypatch,
    *,
    status,
    restart_result=None,
    cleanup=None,
):
    cleanup = cleanup or Mock(return_value=0)
    restart_call = Mock(
        return_value=restart_result
        or SimpleNamespace(state="running", reason="", backend="direct")
    )
    monkeypatch.setattr(
        "experimental.webui.rate_limiter.clear_account_lockouts",
        cleanup,
    )
    monkeypatch.setattr(
        "experimental.webui.service_control.get_status",
        Mock(return_value=status),
    )
    monkeypatch.setattr(
        "experimental.webui.service_control.restart",
        restart_call,
    )
    return cleanup, restart_call


@pytest.mark.parametrize("state", ["stopped", "stale"])
def test_reset_does_not_verify_current_password_and_inactive_service_stays_stopped(
    monkeypatch,
    state,
):
    tab = _tab()
    saved = Mock()
    monkeypatch.setattr("experimental.webui.auth.set_password", saved)
    monkeypatch.setattr(
        "experimental.webui.auth.verify_password",
        Mock(side_effect=AssertionError("desktop reset must not verify old password")),
    )
    cleanup, restart_call = _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state=state, managed=False, backend="direct"),
    )

    tab._on_save_credentials_dialog()

    saved.assert_called_once_with("admin", "new-password-passphrase")
    cleanup.assert_called_once_with("admin")
    restart_call.assert_not_called()
    assert "Service remains stopped" in tab._cred_status_var.value
    tab._refresh_status.assert_called_once()


@pytest.mark.parametrize(
    ("password", "confirmation", "message"),
    [
        ("", "", "new password is required"),
        ("new-password-passphrase", "", "password confirmation is required"),
        ("new-password-passphrase", "different-password", "passwords do not match"),
    ],
)
def test_invalid_confirmation_never_writes_credentials(
    monkeypatch,
    password,
    confirmation,
    message,
):
    tab = _tab(password=password, confirmation=confirmation)
    saved = Mock()
    monkeypatch.setattr("experimental.webui.auth.set_password", saved)

    tab._on_save_credentials_dialog()

    saved.assert_not_called()
    assert message in tab._cred_status_var.value


def test_bootstrap_requires_confirmation_and_writes_verifiable_credential(
    monkeypatch,
    tmp_path,
):
    from experimental.webui.auth import set_password, verify_password

    tab = _tab(existing=False)
    credential_path = tmp_path / "creds.json"
    monkeypatch.setattr(
        "experimental.webui.auth.set_password",
        lambda username, password: set_password(username, password, credential_path),
    )
    _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="stopped", managed=False, backend="direct"),
    )

    tab._on_save_credentials_dialog()

    assert verify_password(
        "admin",
        "new-password-passphrase",
        credential_path,
    )


def test_existing_credential_is_replaced_without_old_password(
    monkeypatch,
    tmp_path,
):
    from experimental.webui.auth import set_password, verify_password

    old_password = "old-password-passphrase"
    credential_path = tmp_path / "creds.json"
    set_password("admin", old_password, credential_path)
    tab = _tab(existing=True)
    monkeypatch.setattr(
        "experimental.webui.auth.set_password",
        lambda username, password: set_password(username, password, credential_path),
    )
    _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="stopped", managed=False, backend="direct"),
    )

    tab._on_save_credentials_dialog()

    assert verify_password(
        "admin",
        "new-password-passphrase",
        credential_path,
    )
    assert not verify_password("admin", old_password, credential_path)


@pytest.mark.parametrize("backend", ["direct", "systemd"])
def test_running_service_restarts_and_reports_session_revocation(
    monkeypatch,
    backend,
):
    tab = _tab()
    monkeypatch.setattr("experimental.webui.auth.set_password", Mock())
    _cleanup, restart_call = _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="running", managed=True, backend=backend),
        restart_result=SimpleNamespace(state="running", reason="", backend=backend),
    )

    tab._on_save_credentials_dialog()

    restart_call.assert_called_once_with("127.0.0.1", 2600)
    assert "existing browser sessions were signed out" in tab._cred_status_var.value


def test_managed_unhealthy_service_restarts(monkeypatch):
    tab = _tab()
    monkeypatch.setattr("experimental.webui.auth.set_password", Mock())
    _cleanup, restart_call = _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="unhealthy", managed=True, backend="direct"),
    )

    tab._on_save_credentials_dialog()

    restart_call.assert_called_once()


@pytest.mark.parametrize("state", ["unmanaged", "ambiguous"])
def test_unowned_service_is_not_signalled(monkeypatch, state):
    tab = _tab()
    monkeypatch.setattr("experimental.webui.auth.set_password", Mock())
    _cleanup, restart_call = _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state=state, managed=False, backend="direct"),
    )

    tab._on_save_credentials_dialog()

    restart_call.assert_not_called()
    assert "ownership could not be confirmed" in tab._cred_status_var.value


def test_restart_failure_is_reported_as_partial_success(monkeypatch):
    tab = _tab()
    saved = Mock()
    monkeypatch.setattr("experimental.webui.auth.set_password", saved)
    _cleanup, restart_call = _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="running", managed=True, backend="direct"),
        restart_result=SimpleNamespace(
            state="failed",
            reason="health timeout",
            backend="direct",
        ),
    )

    tab._on_save_credentials_dialog()

    saved.assert_called_once()
    restart_call.assert_called_once()
    assert tab._cred_status_var.value.startswith("Saved credentials")
    assert "service restart failed: health timeout" in tab._cred_status_var.value


def test_lockout_cleanup_failure_is_reported_without_reverting_password(
    monkeypatch,
):
    tab = _tab()
    saved = Mock()
    monkeypatch.setattr("experimental.webui.auth.set_password", saved)
    cleanup = Mock(side_effect=RuntimeError("database unavailable"))
    _patch_post_save(
        monkeypatch,
        status=SimpleNamespace(state="stopped", managed=False, backend="direct"),
        cleanup=cleanup,
    )

    tab._on_save_credentials_dialog()

    saved.assert_called_once()
    assert tab._cred_status_var.value.startswith("Saved credentials")
    assert "account lockouts could not be cleared" in tab._cred_status_var.value


def test_status_failure_is_reported_without_reverting_password(monkeypatch):
    tab = _tab()
    saved = Mock()
    monkeypatch.setattr("experimental.webui.auth.set_password", saved)
    monkeypatch.setattr(
        "experimental.webui.rate_limiter.clear_account_lockouts",
        Mock(return_value=0),
    )
    monkeypatch.setattr(
        "experimental.webui.service_control.get_status",
        Mock(side_effect=RuntimeError("status unavailable")),
    )
    restart_call = Mock()
    monkeypatch.setattr(
        "experimental.webui.service_control.restart",
        restart_call,
    )

    tab._on_save_credentials_dialog()

    saved.assert_called_once()
    restart_call.assert_not_called()
    assert tab._cred_status_var.value.startswith("Saved credentials")
    assert "service state handling failed" in tab._cred_status_var.value


def test_existing_account_dialog_omits_current_password_and_shows_confirmation(
    monkeypatch,
):
    import tkinter as tk

    class _Theme:
        @staticmethod
        def apply_to_widget(_widget, _style):
            return None

    monkeypatch.setattr(
        "experimental.webui.auth.credential_exists",
        lambda: True,
    )
    monkeypatch.setattr(
        "experimental.webui.auth.get_credential_usernames",
        lambda: ["admin"],
    )
    monkeypatch.setattr(
        "gui.components.experimental_features.webui_tab.ensure_dialog_focus",
        lambda *_args: None,
    )

    root = tk.Tk()
    root.withdraw()
    try:
        tab = WebUITab.__new__(WebUITab)
        tab.frame = tk.Frame(root)
        tab._theme = _Theme()
        tab._cred_dialog = None

        tab._open_credentials_dialog()

        def descendants(widget):
            result = []
            for child in widget.winfo_children():
                result.append(child)
                result.extend(descendants(child))
            return result

        labels = [
            widget.cget("text")
            for widget in descendants(tab._cred_dialog)
            if isinstance(widget, tk.Label)
        ]
        assert "Current Password:" not in labels
        assert "New Password:" in labels
        assert "Confirm New Password:" in labels
        assert any("workstation authorizes" in text for text in labels)
    finally:
        if getattr(tab, "_cred_dialog", None) is not None:
            tab._close_credentials_dialog()
        root.destroy()
