from types import SimpleNamespace

from commands.access import share_enumerator


class _MockConfig:
    def __init__(self, timeout: int = 11) -> None:
        self._timeout = timeout

    def get_connection_timeout(self) -> int:
        return self._timeout


class _AdapterStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def list_shares(self, ip, username, password, cautious_mode, timeout_seconds):
        self.calls.append(
            {
                "ip": ip,
                "username": username,
                "password": password,
                "cautious_mode": cautious_mode,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.result


def _make_op(cautious_mode: bool):
    return SimpleNamespace(
        cautious_mode=cautious_mode,
        config=_MockConfig(),
        output=SimpleNamespace(print_if_verbose=lambda *_args, **_kwargs: None),
        _smb_adapter=None,
    )


def test_enumerate_shares_filters_to_non_admin_disk(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub(
        {
            "success": True,
            "shares": [
                {"name": "Public", "is_disk": True, "is_admin": False},
                {"name": "IPC$", "is_disk": False, "is_admin": True},
                {"name": "Docs$", "is_disk": True, "is_admin": True},
                {"name": "Printer", "is_disk": False, "is_admin": False},
                {"name": "invalid!", "is_disk": True, "is_admin": False},
            ],
        }
    )

    monkeypatch.setattr(share_enumerator, "_get_smb_adapter", lambda _op: adapter)

    shares = share_enumerator.enumerate_shares(op, "10.20.30.50", "guest", "")

    assert shares == ["Public"]


def test_enumerate_shares_returns_empty_on_adapter_failure(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub({"success": False, "error": "timeout", "shares": []})

    monkeypatch.setattr(share_enumerator, "_get_smb_adapter", lambda _op: adapter)

    shares = share_enumerator.enumerate_shares(op, "10.20.30.51", "guest", "")

    assert shares == []


def test_enumerate_shares_detailed_marks_error_as_fatal(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub({"success": False, "status_code": "ERROR", "error": "adapter bug", "shares": []})

    monkeypatch.setattr(share_enumerator, "_get_smb_adapter", lambda _op: adapter)

    result = share_enumerator.enumerate_shares_detailed(op, "10.20.30.60", "guest", "")

    assert result["success"] is False
    assert result["fatal"] is True
    assert result["status_code"] == "ERROR"


def test_enumerate_shares_detailed_keeps_access_denied_nonfatal(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub(
        {
            "success": False,
            "status_code": "NT_STATUS_ACCESS_DENIED",
            "error": "Access denied",
            "shares": [],
        }
    )

    monkeypatch.setattr(share_enumerator, "_get_smb_adapter", lambda _op: adapter)

    result = share_enumerator.enumerate_shares_detailed(op, "10.20.30.61", "guest", "")

    assert result["success"] is False
    assert result["fatal"] is False
    assert result["status_code"] == "NT_STATUS_ACCESS_DENIED"


def test_enumerate_shares_passes_cautious_mode_to_adapter(monkeypatch):
    op = _make_op(cautious_mode=True)
    adapter = _AdapterStub({"success": True, "shares": []})

    monkeypatch.setattr(share_enumerator, "_get_smb_adapter", lambda _op: adapter)

    share_enumerator.enumerate_shares(op, "10.20.30.52", "guest", "")

    assert adapter.calls[0]["cautious_mode"] is True
