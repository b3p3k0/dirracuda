"""Focused Web UI desktop remote-bind behavior tests."""

from gui.components.experimental_features.webui_tab import WebUITab


class _ValueVar:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Dialog:
    def winfo_exists(self):
        return True


class _WarningLabel:
    def __init__(self):
        self.text = ""
        self.mapped = False

    def configure(self, **kwargs):
        self.text = kwargs.get("text", self.text)

    def winfo_ismapped(self):
        return self.mapped

    def pack(self, **_kwargs):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False


def _tab(bind_address, remote_enabled):
    tab = WebUITab.__new__(WebUITab)
    tab._cfg_dialog = _Dialog()
    tab._cfg_bind_var = _ValueVar(bind_address)
    tab._cfg_remote_var = _ValueVar(remote_enabled)
    tab._cfg_tls_enabled_var = _ValueVar(True)
    tab._cfg_tls_insecure_var = _ValueVar(False)
    tab._cfg_remote_warn_label = _WarningLabel()
    return tab


def test_remote_toggle_promotes_ipv4_loopback_and_warns():
    tab = _tab("127.0.0.1", True)

    tab._update_cfg_remote_warning()

    assert tab._cfg_bind_var.get() == "0.0.0.0"
    assert "all IPv4 interfaces (0.0.0.0)" in tab._cfg_remote_warn_label.text
    assert tab._cfg_remote_warn_label.mapped is True


def test_remote_toggle_promotes_ipv6_loopback():
    tab = _tab("::1", True)

    tab._update_cfg_remote_warning()

    assert tab._cfg_bind_var.get() == "::"
    assert "all IPv6 interfaces (::)" in tab._cfg_remote_warn_label.text


def test_disabling_remote_restores_loopback_from_wildcard():
    tab = _tab("0.0.0.0", False)
    tab._cfg_remote_warn_label.mapped = True

    tab._update_cfg_remote_warning()

    assert tab._cfg_bind_var.get() == "127.0.0.1"
    assert tab._cfg_remote_warn_label.mapped is False


def test_remote_toggle_preserves_explicit_interface_address():
    tab = _tab("192.168.1.251", True)

    tab._update_cfg_remote_warning()

    assert tab._cfg_bind_var.get() == "192.168.1.251"
    assert "192.168.1.251" in tab._cfg_remote_warn_label.text
