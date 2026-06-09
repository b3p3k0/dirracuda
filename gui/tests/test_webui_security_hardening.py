"""Focused desktop tests for Web UI security configuration."""

from __future__ import annotations

from gui.components.experimental_features.webui_tab import WebUITab


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Dialog:
    def winfo_exists(self):
        return True


def _configured_tab():
    tab = WebUITab.__new__(WebUITab)
    tab._cfg_dialog = _Dialog()
    tab._cfg_enabled_value = True
    tab._cfg_bind_var = _Var("0.0.0.0")
    tab._cfg_port_var = _Var("2600")
    tab._cfg_remote_var = _Var(True)
    tab._cfg_allowlist_var = _Var("10.0.0.0/8, 192.168.0.0/16")
    tab._cfg_trusted_hosts_var = _Var("ScanBox.LAN., dirracuda.example")
    tab._cfg_idle_var = _Var("30")
    tab._cfg_abs_var = _Var("8")
    tab._cfg_tls_enabled_var = _Var(False)
    tab._cfg_tls_insecure_var = _Var(True)
    tab._cfg_tls_cert_var = _Var("")
    tab._cfg_tls_key_var = _Var("")
    tab._cfg_auth_threshold_var = _Var("5")
    tab._cfg_auth_window_var = _Var("900")
    tab._cfg_auth_base_var = _Var("300")
    tab._cfg_auth_max_var = _Var("3600")
    tab._cfg_initial_insecure_remote = False
    return tab


def test_desktop_build_includes_trusted_hosts():
    cfg = _configured_tab()._build_config_from_dialog()

    assert cfg.trusted_hosts == ["ScanBox.LAN.", "dirracuda.example"]


def test_desktop_confirms_transition_to_remote_plaintext(monkeypatch):
    tab = _configured_tab()
    calls = []
    monkeypatch.setattr(
        "gui.components.experimental_features.webui_tab.safe_messagebox.askyesno",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert tab._confirm_insecure_remote_transition() is True
    assert len(calls) == 1
    assert "without encryption" in calls[0][0][1]


def test_desktop_does_not_reconfirm_existing_remote_plaintext(monkeypatch):
    tab = _configured_tab()
    tab._cfg_initial_insecure_remote = True
    monkeypatch.setattr(
        "gui.components.experimental_features.webui_tab.safe_messagebox.askyesno",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("confirmation should not be shown")
        ),
    )

    assert tab._confirm_insecure_remote_transition() is True
