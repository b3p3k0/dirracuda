import subprocess
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


def _make_op(*, cautious_mode: bool, smbclient_available: bool = True):
    return SimpleNamespace(
        cautious_mode=cautious_mode,
        smbclient_available=smbclient_available,
        _smbclient_auth_cache={},
        config=_MockConfig(),
        output=SimpleNamespace(print_if_verbose=lambda *_args, **_kwargs: None),
    )


def test_smb_alternative_tries_all_combos_and_returns_matching_method(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.40"
    calls = []

    def _fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if len(calls) < 3:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="NT_STATUS_LOGON_FAILURE")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(auth.subprocess, "run", _fake_run)

    result = auth.test_smb_alternative(op, ip)

    assert result == "Guest/Guest"
    assert op._smbclient_auth_cache[ip] == "Guest/Guest"
    assert calls == [
        ["smbclient", "-L", f"//{ip}", "-N"],
        ["smbclient", "-L", f"//{ip}", "--user", "guest%"],
        ["smbclient", "-L", f"//{ip}", "--user", "guest%guest"],
    ]


def test_smb_alternative_uses_cache_without_subprocess(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.40"
    op._smbclient_auth_cache[ip] = "Guest/Blank"

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called for cached hosts")

    monkeypatch.setattr(auth.subprocess, "run", _should_not_run)

    assert auth.test_smb_alternative(op, ip) == "Guest/Blank"


def test_smb_alternative_cautious_mode_enforces_smb2_plus_flags(monkeypatch):
    op = _make_op(cautious_mode=True)
    ip = "10.20.30.40"
    captured = []

    def _fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(auth.subprocess, "run", _fake_run)

    result = auth.test_smb_alternative(op, ip)

    assert result == "Anonymous"
    assert captured[0] == [
        "smbclient",
        "--max-protocol=SMB3",
        "--option=client min protocol=SMB2",
        "-L",
        f"//{ip}",
        "-N",
    ]


def test_smb_alternative_legacy_mode_does_not_force_smb2_plus_flags(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.40"
    captured = []

    def _fake_run(cmd, **_kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(auth.subprocess, "run", _fake_run)

    result = auth.test_smb_alternative(op, ip)

    assert result == "Anonymous"
    assert captured[0] == ["smbclient", "-L", f"//{ip}", "-N"]
    assert "--max-protocol=SMB3" not in captured[0]
    assert "--option=client min protocol=SMB2" not in captured[0]


def test_smb_alternative_treats_sharename_output_as_success(monkeypatch):
    op = _make_op(cautious_mode=False)
    ip = "10.20.30.40"

    def _fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="Sharename       Type      Comment\n---------       ----      -------\n",
            stderr="NT_STATUS_ACCESS_DENIED",
        )

    monkeypatch.setattr(auth.subprocess, "run", _fake_run)

    result = auth.test_smb_alternative(op, ip)

    assert result == "Anonymous"
    assert op._smbclient_auth_cache[ip] == "Anonymous"


def test_smb_alternative_skips_when_smbclient_unavailable(monkeypatch):
    op = _make_op(cautious_mode=False, smbclient_available=False)

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called when smbclient is unavailable")

    monkeypatch.setattr(auth.subprocess, "run", _should_not_run)

    assert auth.test_smb_alternative(op, "10.20.30.40") is None

