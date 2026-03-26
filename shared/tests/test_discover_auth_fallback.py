import sys
import types
from types import SimpleNamespace

# commands.discover.__init__ imports DiscoverOperation, which imports shodan.
# Provide a lightweight stub so auth tests can run without external dependency.
if "shodan" not in sys.modules:
    shodan_stub = types.ModuleType("shodan")
    shodan_stub.Shodan = object
    sys.modules["shodan"] = shodan_stub

from commands.discover import auth


class _MockConfig:
    def __init__(self, timeout: int = 7) -> None:
        self._timeout = timeout

    def get_connection_timeout(self) -> int:
        return self._timeout

    def get(self, *_args, **_kwargs):
        return 10


class _StubAdapter:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = []

    def probe_authentication(self, ip, cautious_mode, timeout_seconds):
        self.calls.append(
            {
                "ip": ip,
                "cautious_mode": cautious_mode,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def _make_op(*, cautious_mode: bool):
    return SimpleNamespace(
        cautious_mode=cautious_mode,
        _smbclient_auth_cache={},
        config=_MockConfig(),
        output=SimpleNamespace(print_if_verbose=lambda *_args, **_kwargs: None),
        shodan_host_metadata={},
        _connection_pool=SimpleNamespace(return_connection=lambda *_args, **_kwargs: None),
    )


def test_smb_alternative_uses_cache_without_adapter(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.40"
    op._smbclient_auth_cache[ip] = "Guest/Blank"

    monkeypatch.setattr(auth, "check_smbclient_availability", lambda: True)

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("get_smb_adapter should not be called for cached hosts")

    monkeypatch.setattr(auth, "get_smb_adapter", _should_not_call)

    assert auth.test_smb_alternative(op, ip) == "Guest/Blank"


def test_smb_alternative_populates_cache_from_adapter(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.41"
    adapter = _StubAdapter({"success": True, "auth_method": "Guest/Guest"})

    monkeypatch.setattr(auth, "check_smbclient_availability", lambda: True)
    monkeypatch.setattr(auth, "get_smb_adapter", lambda _op: adapter)

    first = auth.test_smb_alternative(op, ip)
    second = auth.test_smb_alternative(op, ip)

    assert first == "Guest/Guest"
    assert second == "Guest/Guest"
    assert op._smbclient_auth_cache[ip] == "Guest/Guest"
    assert len(adapter.calls) == 1


def test_smb_alternative_returns_none_on_failed_probe(monkeypatch):
    op = _make_op(cautious_mode=True)
    ip = "10.20.30.42"
    adapter = _StubAdapter({"success": False, "auth_method": None})

    monkeypatch.setattr(auth, "check_smbclient_availability", lambda: True)
    monkeypatch.setattr(auth, "get_smb_adapter", lambda _op: adapter)

    assert auth.test_smb_alternative(op, ip) is None
    assert op._smbclient_auth_cache[ip] is None
    assert adapter.calls[0]["cautious_mode"] is True


def test_smb_alternative_skips_when_transport_unavailable(monkeypatch):
    op = _make_op(cautious_mode=False)
    monkeypatch.setattr(auth, "check_smbclient_availability", lambda: False)
    assert auth.test_smb_alternative(op, "10.20.30.43") is None


def test_single_host_returns_auth_without_smbclient_suffix(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.44"
    op.shodan_host_metadata[ip] = {"country_name": "USA", "country_code": "US"}

    monkeypatch.setattr(auth, "check_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "test_smb_alternative", lambda *_args, **_kwargs: "Guest/Blank")

    result = auth.test_single_host(op, ip, country="USA")

    assert result is not None
    assert result["auth_method"] == "Guest/Blank"
    assert "(smbclient)" not in result["auth_method"]


def test_single_host_returns_none_when_probe_fails(monkeypatch):
    op = _make_op(cautious_mode=False)

    monkeypatch.setattr(auth, "check_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth, "test_smb_alternative", lambda *_args, **_kwargs: None)

    assert auth.test_single_host(op, "10.20.30.45") is None
