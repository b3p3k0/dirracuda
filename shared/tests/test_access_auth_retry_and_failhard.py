from types import SimpleNamespace

import pytest

from commands.access.operation import AccessOperation, FatalAccessError


class _ConfigStub:
    def get_connection_timeout(self):
        return 5

    def get_share_access_delay(self):
        return 0

    def get_max_concurrent_hosts(self):
        return 1


class _OutputStub:
    def __init__(self, *, verbose_enabled=False):
        self.messages = []
        self.verbose_enabled = verbose_enabled

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))

    def success(self, message):
        self.messages.append(("success", message))

    def print_if_verbose(self, message):
        if self.verbose_enabled:
            self.messages.append(("verbose", message))


class _DatabaseStub:
    def __init__(self, hosts):
        self._hosts = hosts

    def get_authenticated_hosts(self, ip_filter=None, recent_hours=None):
        _ = ip_filter, recent_hours
        return list(self._hosts)

    def store_share_access_result(self, _session_id, _result):
        return True


def _make_operation(hosts=None, *, verbose_enabled=False):
    if hosts is None:
        hosts = []
    return AccessOperation(
        _ConfigStub(),
        _OutputStub(verbose_enabled=verbose_enabled),
        _DatabaseStub(hosts),
        session_id=777,
        cautious_mode=False,
        check_rce=False,
    )


def test_parse_auth_method_guest_guest_takes_precedence():
    op = _make_operation()

    username, password = op.parse_auth_method("Guest/Guest")

    assert username == "guest"
    assert password == "guest"


def test_process_target_retries_auth_methods_until_enumeration_success(monkeypatch):
    op = _make_operation()
    host = {"ip_address": "10.99.99.10", "country": "US", "auth_method": "Anonymous"}
    calls = []

    monkeypatch.setattr(op, "check_port", lambda *_args, **_kwargs: True)

    responses = iter(
        [
            {
                "success": False,
                "fatal": False,
                "shares": [],
                "status_code": "NT_STATUS_LOGON_FAILURE",
                "error": "Authentication failed",
            },
            {
                "success": True,
                "fatal": False,
                "shares": ["Public"],
                "status_code": "OK",
                "error": None,
            },
        ]
    )

    def _fake_enumerate(_ip, username, password):
        calls.append((username, password))
        return next(responses)

    monkeypatch.setattr(op, "enumerate_shares_detailed", _fake_enumerate)
    monkeypatch.setattr(
        op,
        "test_share_access",
        lambda *_args, **_kwargs: {"accessible": True, "share_name": "Public", "error": None, "auth_status": "OK"},
    )

    result = op.process_target(host, 1)

    assert calls == [("guest", "guest"), ("guest", "")]
    assert result["auth_method"] == "Guest/Blank"
    assert result["shares_found"] == ["Public"]
    assert result["accessible_shares"] == ["Public"]
    info_messages = [message for level, message in op.output.messages if level == "info"]
    assert not any("Using auth:" in message for message in info_messages)


def test_process_target_emits_auth_attempt_lines_only_in_verbose_channel(monkeypatch):
    op = _make_operation(verbose_enabled=True)
    host = {"ip_address": "10.99.99.13", "country": "US", "auth_method": "Anonymous"}
    calls = []

    monkeypatch.setattr(op, "check_port", lambda *_args, **_kwargs: True)

    responses = iter(
        [
            {
                "success": False,
                "fatal": False,
                "shares": [],
                "status_code": "NT_STATUS_LOGON_FAILURE",
                "error": "Authentication failed",
            },
            {
                "success": True,
                "fatal": False,
                "shares": ["Public"],
                "status_code": "OK",
                "error": None,
            },
        ]
    )

    def _fake_enumerate(_ip, username, password):
        calls.append((username, password))
        return next(responses)

    monkeypatch.setattr(op, "enumerate_shares_detailed", _fake_enumerate)
    monkeypatch.setattr(
        op,
        "test_share_access",
        lambda *_args, **_kwargs: {"accessible": True, "share_name": "Public", "error": None, "auth_status": "OK"},
    )

    result = op.process_target(host, 1)

    assert calls == [("guest", "guest"), ("guest", "")]
    assert result["auth_method"] == "Guest/Blank"
    verbose_messages = [message for level, message in op.output.messages if level == "verbose"]
    assert len([message for message in verbose_messages if "Using auth:" in message]) == 2


def test_process_target_raises_fatal_access_error_on_backend_failure(monkeypatch):
    op = _make_operation()
    host = {"ip_address": "10.99.99.11", "country": "US", "auth_method": "Anonymous"}

    monkeypatch.setattr(op, "check_port", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        op,
        "enumerate_shares_detailed",
        lambda *_args, **_kwargs: {
            "success": False,
            "fatal": True,
            "shares": [],
            "status_code": "ERROR",
            "error": "Missing required SMB backend dependency: impacket",
        },
    )

    with pytest.raises(FatalAccessError):
        op.process_target(host, 1)


def test_execute_aborts_on_fatal_access_error(monkeypatch):
    hosts = [{"ip_address": "10.99.99.12", "country": "US", "auth_method": "Anonymous"}]
    op = _make_operation(hosts)

    monkeypatch.setattr("commands.access.operation.SMB_AVAILABLE", True)
    monkeypatch.setattr("commands.access.operation.share_enumerator.preflight_access_backend", lambda _op: None)
    monkeypatch.setattr(op, "process_target", lambda *_args, **_kwargs: (_ for _ in ()).throw(FatalAccessError("fatal backend failure")))

    with pytest.raises(RuntimeError, match="fatal backend failure"):
        op.execute(target_ips={"10.99.99.12"})
