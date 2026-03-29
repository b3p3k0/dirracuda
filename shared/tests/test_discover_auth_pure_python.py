import sys
import types
from types import SimpleNamespace

# commands.discover.__init__ imports DiscoverOperation -> shodan.
if "shodan" not in sys.modules:
    shodan_stub = types.ModuleType("shodan")
    shodan_stub.Shodan = object
    sys.modules["shodan"] = shodan_stub

from commands.discover import auth


class _MockConfig:
    def __init__(self, timeout: int = 9) -> None:
        self._timeout = timeout

    def get_connection_timeout(self) -> int:
        return self._timeout

    def get(self, *_args, **_kwargs):
        return 10


class _AdapterRecorder:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls = []

    def probe_authentication(self, ip, cautious_mode, timeout_seconds):
        self.calls.append((ip, cautious_mode, timeout_seconds))
        return self.result


def _make_op(cautious_mode: bool):
    return SimpleNamespace(
        cautious_mode=cautious_mode,
        _auth_method_cache={},
        config=_MockConfig(),
        output=SimpleNamespace(print_if_verbose=lambda *_args, **_kwargs: None),
        shodan_host_metadata={},
        _connection_pool=SimpleNamespace(return_connection=lambda *_args, **_kwargs: None),
    )


def test_smb_alternative_passes_cautious_mode_to_adapter(monkeypatch):
    op = _make_op(cautious_mode=True)
    adapter = _AdapterRecorder({"success": False, "auth_method": None})

    monkeypatch.setattr(auth, "check_transport_availability", lambda: True)
    monkeypatch.setattr(auth, "get_smb_adapter", lambda _op: adapter)

    auth.test_smb_alternative(op, "10.50.60.70")

    assert adapter.calls[0][1] is True


def test_legacy_mode_uses_adapter_success_method(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterRecorder({"success": True, "auth_method": "Guest/Guest"})

    monkeypatch.setattr(auth, "check_transport_availability", lambda: True)
    monkeypatch.setattr(auth, "get_smb_adapter", lambda _op: adapter)

    result = auth.test_smb_alternative(op, "10.50.60.71")

    assert result == "Guest/Guest"
    assert adapter.calls[0][1] is False


def test_single_host_uses_adapter_result_and_country_fallback(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.50.60.72"
    adapter = _AdapterRecorder({"success": True, "auth_method": "Anonymous"})

    monkeypatch.setattr(auth, "check_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "check_transport_availability", lambda: True)
    monkeypatch.setattr(auth, "get_smb_adapter", lambda _op: adapter)

    result = auth.test_single_host(op, ip, country="GB")

    assert result["ip_address"] == ip
    assert result["country"] == "GB"
    assert result["auth_method"] == "Anonymous"
