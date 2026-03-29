from types import SimpleNamespace

from commands.access import share_tester


class _MockConfig:
    def __init__(self, timeout: int = 13) -> None:
        self._timeout = timeout

    def get_connection_timeout(self) -> int:
        return self._timeout


class _OutputStub:
    def __init__(self) -> None:
        self.errors = []
        self.warnings = []
        self.verbose = []

    def print_if_verbose(self, message: str) -> None:
        self.verbose.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


class _AdapterStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def probe_share_read(self, ip, share_name, username, password, cautious_mode, timeout_seconds):
        self.calls.append(
            {
                "ip": ip,
                "share_name": share_name,
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
        output=_OutputStub(),
        _smb_adapter=None,
    )


def test_share_access_returns_accessible_for_ok(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub(
        {
            "accessible": True,
            "status_code": "OK",
            "error": None,
        }
    )

    monkeypatch.setattr(share_tester, "_get_smb_adapter", lambda _op: adapter)

    result = share_tester.test_share_access(op, "10.1.1.1", "Public", "guest", "")

    assert result["accessible"] is True
    assert result["auth_status"] == "OK"
    assert result["error"] is None


def test_share_access_maps_missing_share_status(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub(
        {
            "accessible": False,
            "status_code": "NT_STATUS_BAD_NETWORK_NAME",
            "error": "Share not found",
        }
    )

    monkeypatch.setattr(share_tester, "_get_smb_adapter", lambda _op: adapter)

    result = share_tester.test_share_access(op, "10.1.1.2", "Missing", "guest", "")

    assert result["accessible"] is False
    assert result["auth_status"] == "NT_STATUS_BAD_NETWORK_NAME"
    assert result["error"] == "Share not found on server (server reported NT_STATUS_BAD_NETWORK_NAME)"
    assert op.output.errors == []


def test_share_access_keeps_access_denied_semantics(monkeypatch):
    op = _make_op(cautious_mode=True)
    adapter = _AdapterStub(
        {
            "accessible": False,
            "status_code": "ACCESS_DENIED",
            "error": "Access denied or empty share",
        }
    )

    monkeypatch.setattr(share_tester, "_get_smb_adapter", lambda _op: adapter)

    result = share_tester.test_share_access(op, "10.1.1.3", "Private", "guest", "")

    assert result["accessible"] is False
    assert result["auth_status"] == "ACCESS_DENIED"
    assert result["error"] == "Access denied or empty share"
    assert op.output.errors == []


def test_share_access_normalizes_timeout_without_nt_status(monkeypatch):
    op = _make_op(cautious_mode=False)
    adapter = _AdapterStub(
        {
            "accessible": False,
            "status_code": "ERROR",
            "error": "socket timed out while listing path",
        }
    )

    monkeypatch.setattr(share_tester, "_get_smb_adapter", lambda _op: adapter)

    result = share_tester.test_share_access(op, "10.1.1.4", "Public", "guest", "")

    assert result["accessible"] is False
    assert result["auth_status"] == "TIMEOUT"
    assert "timed out" in result["error"]
    assert len(op.output.warnings) == 1


def test_share_access_passes_cautious_mode_and_timeout(monkeypatch):
    op = _make_op(cautious_mode=True)
    adapter = _AdapterStub(
        {
            "accessible": False,
            "status_code": "NT_STATUS_LOGON_FAILURE",
            "error": "Authentication failed",
        }
    )

    monkeypatch.setattr(share_tester, "_get_smb_adapter", lambda _op: adapter)

    share_tester.test_share_access(op, "10.1.1.5", "Public", "guest", "guest")

    call = adapter.calls[0]
    assert call["cautious_mode"] is True
    assert call["timeout_seconds"] == 13
