"""Regression tests for non-blocking single-instance scan dialogs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.ftp_scan_dialog as ftp_scan_dialog
import gui.components.http_scan_dialog as http_scan_dialog
import gui.components.scan_dialog as scan_dialog
import gui.components.unified_scan_dialog as unified_scan_dialog


class _WindowStub:
    def __init__(self, *, exists: bool = True) -> None:
        self._exists = exists

    def winfo_exists(self) -> bool:
        return self._exists


class _ExistingDialogStub:
    def __init__(self) -> None:
        self.dialog = _WindowStub(exists=True)
        self.focus_calls = 0

    def focus_dialog(self) -> None:
        self.focus_calls += 1


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    unified_scan_dialog._ACTIVE_UNIFIED_SCAN_DIALOG = None
    ftp_scan_dialog._ACTIVE_FTP_SCAN_DIALOG = None
    http_scan_dialog._ACTIVE_HTTP_SCAN_DIALOG = None
    scan_dialog._ACTIVE_SCAN_DIALOG = None
    yield
    unified_scan_dialog._ACTIVE_UNIFIED_SCAN_DIALOG = None
    ftp_scan_dialog._ACTIVE_FTP_SCAN_DIALOG = None
    http_scan_dialog._ACTIVE_HTTP_SCAN_DIALOG = None
    scan_dialog._ACTIVE_SCAN_DIALOG = None


def test_show_unified_scan_dialog_focuses_existing_instance(monkeypatch):
    existing = _ExistingDialogStub()
    unified_scan_dialog._ACTIVE_UNIFIED_SCAN_DIALOG = existing

    monkeypatch.setattr(
        unified_scan_dialog,
        "UnifiedScanDialog",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not construct")),
    )

    result = unified_scan_dialog.show_unified_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result is None
    assert existing.focus_calls == 1


def test_show_unified_scan_dialog_constructs_and_clears_singleton(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.dialog = _WindowStub(exists=True)
            self.show_calls = 0
            captured["instance"] = self

        def show(self):
            self.show_calls += 1
            self.dialog._exists = False
            return "start"

        def focus_dialog(self):
            raise AssertionError("focus path should not run for first open")

    monkeypatch.setattr(unified_scan_dialog, "UnifiedScanDialog", _DialogStub)

    result = unified_scan_dialog.show_unified_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result == "start"
    assert captured["instance"].show_calls == 1
    assert unified_scan_dialog._ACTIVE_UNIFIED_SCAN_DIALOG is None


def test_show_ftp_scan_dialog_focuses_existing_instance(monkeypatch):
    existing = _ExistingDialogStub()
    ftp_scan_dialog._ACTIVE_FTP_SCAN_DIALOG = existing

    monkeypatch.setattr(
        ftp_scan_dialog,
        "FtpScanDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not construct")),
    )

    result = ftp_scan_dialog.show_ftp_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result is None
    assert existing.focus_calls == 1


def test_show_ftp_scan_dialog_constructs_and_clears_singleton(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, *_args, **_kwargs):
            self.dialog = _WindowStub(exists=True)
            self.show_calls = 0
            captured["instance"] = self

        def show(self):
            self.show_calls += 1
            self.dialog._exists = False
            return "cancel"

        def focus_dialog(self):
            raise AssertionError("focus path should not run for first open")

    monkeypatch.setattr(ftp_scan_dialog, "FtpScanDialog", _DialogStub)

    result = ftp_scan_dialog.show_ftp_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result == "cancel"
    assert captured["instance"].show_calls == 1
    assert ftp_scan_dialog._ACTIVE_FTP_SCAN_DIALOG is None


def test_show_http_scan_dialog_focuses_existing_instance(monkeypatch):
    existing = _ExistingDialogStub()
    http_scan_dialog._ACTIVE_HTTP_SCAN_DIALOG = existing

    monkeypatch.setattr(
        http_scan_dialog,
        "HttpScanDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not construct")),
    )

    result = http_scan_dialog.show_http_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result is None
    assert existing.focus_calls == 1


def test_show_http_scan_dialog_constructs_and_clears_singleton(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, *_args, **_kwargs):
            self.dialog = _WindowStub(exists=True)
            self.show_calls = 0
            captured["instance"] = self

        def show(self):
            self.show_calls += 1
            self.dialog._exists = False
            return "start"

        def focus_dialog(self):
            raise AssertionError("focus path should not run for first open")

    monkeypatch.setattr(http_scan_dialog, "HttpScanDialog", _DialogStub)

    result = http_scan_dialog.show_http_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        scan_start_callback=lambda _req: None,
    )

    assert result == "start"
    assert captured["instance"].show_calls == 1
    assert http_scan_dialog._ACTIVE_HTTP_SCAN_DIALOG is None


def test_show_scan_dialog_focuses_existing_instance(monkeypatch):
    existing = _ExistingDialogStub()
    scan_dialog._ACTIVE_SCAN_DIALOG = existing

    monkeypatch.setattr(
        scan_dialog,
        "ScanDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not construct")),
    )

    result = scan_dialog.show_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        config_editor_callback=lambda _path: None,
        scan_start_callback=lambda _req: None,
    )

    assert result is None
    assert existing.focus_calls == 1


def test_show_scan_dialog_constructs_and_clears_singleton(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, *_args, **_kwargs):
            self.dialog = _WindowStub(exists=True)
            self.show_calls = 0
            captured["instance"] = self

        def show(self):
            self.show_calls += 1
            self.dialog._exists = False
            return "cancel"

        def focus_dialog(self):
            raise AssertionError("focus path should not run for first open")

    monkeypatch.setattr(scan_dialog, "ScanDialog", _DialogStub)

    result = scan_dialog.show_scan_dialog(
        parent=object(),
        config_path="/tmp/config.json",
        config_editor_callback=lambda _path: None,
        scan_start_callback=lambda _req: None,
    )

    assert result == "cancel"
    assert captured["instance"].show_calls == 1
    assert scan_dialog._ACTIVE_SCAN_DIALOG is None
