"""Unit tests for protocol-native bulk extract runners."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from shared.quarantine_postprocess import PostProcessResult
from gui.utils import protocol_extract_runner as per


class _FakeEntry:
    def __init__(self, name: str, is_dir: bool, size: int = 0, modified_time=None):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.modified_time = modified_time


class _FakeListResult:
    def __init__(self, entries, warning=None):
        self.entries = entries
        self.warning = warning
        self.truncated = False


class _FakeFtpNavigator:
    """In-memory FTP tree stub for extract-runner tests."""

    def __init__(self, *args, **kwargs):
        self._cancel_event = None
        self._connected = False
        self._tree = {
            "/": [
                _FakeEntry("root.txt", False, size=10),
                _FakeEntry("root.bin", False, size=10),
                _FakeEntry("pub", True),
            ],
            "/pub": [
                _FakeEntry("child.txt", False, size=10),
                _FakeEntry("deep", True),
            ],
            "/pub/deep": [
                _FakeEntry("deep.txt", False, size=10),
            ],
        }

    def connect(self, host: str, port: int = 21) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def list_dir(self, path: str):
        key = "/" + str(PurePosixPath(path)).lstrip("/")
        if key != "/" and key.endswith("/"):
            key = key.rstrip("/")
        return _FakeListResult(self._tree.get(key, []))

    def download_file(self, remote_path: str, dest_dir: Path):
        size = 10
        out = Path(dest_dir) / PurePosixPath(remote_path).name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * size)
        return SimpleNamespace(saved_path=out, size=size)



def _fake_http_download(**kwargs):
    dest_path = Path(kwargs["dest_path"])
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"x" * 16)
    return 16, None


def test_run_ftp_extract_enforces_depth_and_extension_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(per, "FtpNavigator", _FakeFtpNavigator)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    summary = per.run_ftp_extract(
        "198.51.100.10",
        port=21,
        download_dir=tmp_path / "extract",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=10,
        max_seconds=60,
        max_depth=1,
        allowed_extensions=[".txt"],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="allow_only",
        clamav_config={"enabled": False},
    )

    downloaded_paths = {row["path"] for row in summary["files"]}
    skipped_reasons = {(row["path"], row["reason"]) for row in summary["skipped"]}

    assert downloaded_paths == {"root.txt", "pub/child.txt"}
    assert ("root.bin", "not_included_extension") in skipped_reasons
    # max_depth=1 means /pub/deep/deep.txt is out of scope.
    assert "pub/deep/deep.txt" not in downloaded_paths


def test_run_ftp_extract_enforces_file_count_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(per, "FtpNavigator", _FakeFtpNavigator)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    summary = per.run_ftp_extract(
        "198.51.100.11",
        port=21,
        download_dir=tmp_path / "extract",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=1,
        max_seconds=60,
        max_depth=3,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="download_all",
        clamav_config={"enabled": False},
    )

    assert summary["totals"]["files_downloaded"] == 1
    assert summary["stop_reason"] == "file_limit"


def test_run_http_extract_enforces_extension_mode(monkeypatch, tmp_path):
    def _fake_fetch_listing(**kwargs):
        path = kwargs["path"]
        if path == "/root":
            return True, ["/root/pub/"], ["/root/a.txt", "/root/a.bin"], None
        if path == "/root/pub":
            return True, ["/root/pub/deep/"], ["/root/pub/b.txt"], None
        if path == "/root/pub/deep":
            return True, [], ["/root/pub/deep/c.txt"], None
        return False, [], [], f"{path} missing"

    monkeypatch.setattr(per, "_http_fetch_listing", _fake_fetch_listing)
    monkeypatch.setattr(per, "_http_download_file", _fake_http_download)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    summary = per.run_http_extract(
        "203.0.113.5",
        port=443,
        scheme="https",
        request_host="cdn.example.org",
        start_path="/root",
        allow_insecure_tls=False,
        download_dir=tmp_path / "extract",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=10,
        max_seconds=60,
        max_depth=1,
        allowed_extensions=[".txt"],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="allow_only",
        clamav_config={"enabled": False},
    )

    downloaded_paths = {row["path"] for row in summary["files"]}
    skipped_reasons = {(row["path"], row["reason"]) for row in summary["skipped"]}

    assert downloaded_paths == {"root/a.txt", "root/pub/b.txt"}
    assert ("root/a.bin", "not_included_extension") in skipped_reasons
    # max_depth=1 means /root/pub/deep/c.txt is out of scope.
    assert "root/pub/deep/c.txt" not in downloaded_paths


def test_run_http_extract_clamav_passthrough_success(monkeypatch, tmp_path):
    def _fake_fetch_listing(**kwargs):
        return True, [], ["/root/a.txt"], None

    def _fake_setup(_cfg, _ip, _dir, _share):
        accum = {
            "enabled": True,
            "backend_used": None,
            "files_scanned": 0,
            "clean": 0,
            "infected": 0,
            "errors": 0,
            "promoted": 0,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        }

        def _pp(inp):
            return PostProcessResult(
                final_path=inp.file_path,
                verdict="clean",
                moved=False,
                destination="quarantine",
                metadata=SimpleNamespace(backend_used="clamdscan", signature=None),
                error=None,
            )

        return _pp, accum, None

    monkeypatch.setattr(per, "_http_fetch_listing", _fake_fetch_listing)
    monkeypatch.setattr(per, "_http_download_file", _fake_http_download)
    monkeypatch.setattr(per, "build_browser_download_clamav_setup", _fake_setup)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    summary = per.run_http_extract(
        "203.0.113.6",
        port=80,
        scheme="http",
        request_host=None,
        start_path="/root",
        allow_insecure_tls=True,
        download_dir=tmp_path / "extract",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=10,
        max_seconds=60,
        max_depth=1,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="download_all",
        clamav_config={"enabled": True},
    )

    assert summary["clamav"]["enabled"] is True
    assert summary["clamav"]["files_scanned"] == 1
    assert summary["clamav"]["clean"] == 1


def test_run_http_extract_clamav_passthrough_error(monkeypatch, tmp_path):
    def _fake_fetch_listing(**kwargs):
        return True, [], ["/root/a.txt"], None

    def _fake_setup(_cfg, _ip, _dir, _share):
        accum = {
            "enabled": True,
            "backend_used": None,
            "files_scanned": 0,
            "clean": 0,
            "infected": 0,
            "errors": 0,
            "promoted": 0,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        }

        def _pp(_inp):
            raise RuntimeError("clamav failure")

        return _pp, accum, None

    monkeypatch.setattr(per, "_http_fetch_listing", _fake_fetch_listing)
    monkeypatch.setattr(per, "_http_download_file", _fake_http_download)
    monkeypatch.setattr(per, "build_browser_download_clamav_setup", _fake_setup)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    summary = per.run_http_extract(
        "203.0.113.7",
        port=80,
        scheme="http",
        request_host=None,
        start_path="/root",
        allow_insecure_tls=True,
        download_dir=tmp_path / "extract",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=10,
        max_seconds=60,
        max_depth=1,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="download_all",
        clamav_config={"enabled": True},
    )

    assert summary["clamav"]["enabled"] is True
    assert summary["clamav"]["errors"] == 1
    assert any("post_processor error" in row["message"] for row in summary["errors"])
