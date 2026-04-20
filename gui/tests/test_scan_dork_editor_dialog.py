"""Tests for Start-Scan discovery dork editor dialog."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui.components.scan_dork_editor_dialog as dork_editor_dialog


class _Var:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class _DialogWidget:
    def __init__(self, exists: bool = True) -> None:
        self._exists = exists
        self.destroyed = False

    def winfo_exists(self) -> int:
        return 1 if self._exists else 0

    def destroy(self) -> None:
        self.destroyed = True
        self._exists = False


@pytest.fixture(autouse=True)
def _reset_singleton():
    dork_editor_dialog._ACTIVE_SCAN_DORK_EDITOR_DIALOG = None
    yield
    dork_editor_dialog._ACTIVE_SCAN_DORK_EDITOR_DIALOG = None


def test_show_scan_dork_editor_dialog_focuses_existing_instance(monkeypatch):
    class _Existing:
        def __init__(self) -> None:
            self.dialog = _DialogWidget(exists=True)
            self.focus_calls = 0

        def focus_dialog(self) -> None:
            self.focus_calls += 1

    existing = _Existing()
    dork_editor_dialog._ACTIVE_SCAN_DORK_EDITOR_DIALOG = existing

    monkeypatch.setattr(
        dork_editor_dialog,
        "ScanDorkEditorDialog",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not construct")),
    )

    dork_editor_dialog.show_scan_dork_editor_dialog(parent=object(), config_path="/tmp/config.json")
    assert existing.focus_calls == 1


def test_show_scan_dork_editor_dialog_constructs_and_clears_singleton(monkeypatch):
    captured = {}

    class _DialogStub:
        def __init__(self, parent, config_path, settings_manager=None, on_close_callback=None):
            self.parent = parent
            self.config_path = config_path
            self.settings_manager = settings_manager
            self.on_close_callback = on_close_callback
            self.dialog = _DialogWidget(exists=True)
            captured["instance"] = self

        def focus_dialog(self) -> None:
            raise AssertionError("focus path should not run for first open")

    monkeypatch.setattr(dork_editor_dialog, "ScanDorkEditorDialog", _DialogStub)

    dork_editor_dialog.show_scan_dork_editor_dialog(parent=object(), config_path="/tmp/config.json")
    inst = captured["instance"]
    assert dork_editor_dialog._ACTIVE_SCAN_DORK_EDITOR_DIALOG is inst

    inst.on_close_callback(inst)
    assert dork_editor_dialog._ACTIVE_SCAN_DORK_EDITOR_DIALOG is None


def test_load_dorks_from_config_uses_defaults_and_config_values(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "shodan": {"query_components": {"base_query": "smb custom"}},
                "ftp": {"shodan": {"query_components": {"base_query": "ftp custom"}}},
            }
        ),
        encoding="utf-8",
    )

    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.config_path = cfg
    dlg.smb_dork = dlg.DORK_DEFAULTS["smb_dork"]
    dlg.ftp_dork = dlg.DORK_DEFAULTS["ftp_dork"]
    dlg.http_dork = dlg.DORK_DEFAULTS["http_dork"]
    dlg._open_dork_values = dlg.DORK_DEFAULTS.copy()

    dlg._load_dorks_from_config()

    assert dlg.smb_dork == "smb custom"
    assert dlg.ftp_dork == "ftp custom"
    assert dlg.http_dork == dlg.DORK_DEFAULTS["http_dork"]
    assert dlg._open_dork_values["smb_dork"] == "smb custom"
    assert dlg._open_dork_values["ftp_dork"] == "ftp custom"


def test_reset_and_default_actions():
    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.smb_dork_var = _Var("changed")
    dlg.ftp_dork_var = _Var("ftp changed")
    dlg.http_dork_var = _Var("http changed")
    dlg._open_dork_values = {
        "smb_dork": "smb open",
        "ftp_dork": "ftp open",
        "http_dork": "http open",
    }

    dlg._set_dork_default("smb_dork")
    assert dlg.smb_dork_var.get() == dlg.DORK_DEFAULTS["smb_dork"]

    dlg._reset_dork_to_open("ftp_dork")
    assert dlg.ftp_dork_var.get() == "ftp open"


def test_validate_and_save_rejects_blank_query(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "config.json"

    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.parent = object()
    dlg.dialog = _DialogWidget(exists=True)
    dlg.config_path = cfg
    dlg.smb_dork_var = _Var("")
    dlg.ftp_dork_var = _Var("ftp ok")
    dlg.http_dork_var = _Var("http ok")
    dlg.status_labels = {}
    dlg.validation_results = {
        "smb_dork": {"valid": False, "message": ""},
        "ftp_dork": {"valid": False, "message": ""},
        "http_dork": {"valid": False, "message": ""},
    }

    errors = []
    monkeypatch.setattr(
        dork_editor_dialog.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    assert dlg._validate_and_save() is False
    assert errors
    assert not cfg.exists()


def test_validate_and_save_persists_dorks_only(tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"shodan": {"api_key": "XYZ"}, "keep_me": {"a": 1}}), encoding="utf-8")

    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.parent = object()
    dlg.dialog = _DialogWidget(exists=True)
    dlg.config_path = cfg
    dlg.smb_dork_var = _Var(" smb new ")
    dlg.ftp_dork_var = _Var("ftp new")
    dlg.http_dork_var = _Var("http new")
    dlg.status_labels = {}
    dlg.validation_results = {
        "smb_dork": {"valid": False, "message": ""},
        "ftp_dork": {"valid": False, "message": ""},
        "http_dork": {"valid": False, "message": ""},
    }
    dlg.smb_dork = dlg.DORK_DEFAULTS["smb_dork"]
    dlg.ftp_dork = dlg.DORK_DEFAULTS["ftp_dork"]
    dlg.http_dork = dlg.DORK_DEFAULTS["http_dork"]
    dlg._open_dork_values = dlg.DORK_DEFAULTS.copy()

    assert dlg._validate_and_save() is True

    out = json.loads(cfg.read_text(encoding="utf-8"))
    assert out["keep_me"] == {"a": 1}
    assert out["shodan"]["api_key"] == "XYZ"
    assert out["shodan"]["query_components"]["base_query"] == "smb new"
    assert out["ftp"]["shodan"]["query_components"]["base_query"] == "ftp new"
    assert out["http"]["shodan"]["query_components"]["base_query"] == "http new"


def test_validate_and_save_rejects_malformed_existing_config(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{bad json", encoding="utf-8")

    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.parent = object()
    dlg.dialog = _DialogWidget(exists=True)
    dlg.config_path = cfg
    dlg.smb_dork_var = _Var("smb new")
    dlg.ftp_dork_var = _Var("ftp new")
    dlg.http_dork_var = _Var("http new")
    dlg.status_labels = {}
    dlg.validation_results = {
        "smb_dork": {"valid": False, "message": ""},
        "ftp_dork": {"valid": False, "message": ""},
        "http_dork": {"valid": False, "message": ""},
    }

    errors = []
    monkeypatch.setattr(
        dork_editor_dialog.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    assert dlg._validate_and_save() is False
    assert errors
    assert cfg.read_text(encoding="utf-8") == "{bad json"


def test_populate_discovery_dork_from_dorkbook_opens_and_populates(monkeypatch):
    calls = {"populate": []}

    class _DialogStub:
        def __init__(self, parent, config_path, settings_manager=None, on_close_callback=None):
            self.parent = parent
            self.config_path = config_path
            self.settings_manager = settings_manager
            self.on_close_callback = on_close_callback
            self.dialog = _DialogWidget(exists=True)

        def focus_dialog(self) -> None:
            return None

        def populate_from_dorkbook(self, *, protocol: str, query: str) -> None:
            calls["populate"].append((protocol, query))

    monkeypatch.setattr(dork_editor_dialog, "ScanDorkEditorDialog", _DialogStub)

    dork_editor_dialog.populate_discovery_dork_from_dorkbook(
        parent=object(),
        config_path="/tmp/config.json",
        protocol="FTP",
        query='port:21 "230 Login successful"',
    )

    assert calls["populate"] == [("FTP", 'port:21 "230 Login successful"')]


def test_populate_from_dorkbook_maps_protocol_to_field_and_is_manual_save():
    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.smb_dork_var = _Var("smb old")
    dlg.ftp_dork_var = _Var("ftp old")
    dlg.http_dork_var = _Var("http old")
    dlg.focus_dialog = lambda: None

    dlg.populate_from_dorkbook(protocol="HTTP", query="http.title:\"Index of /\"")

    assert dlg.smb_dork_var.get() == "smb old"
    assert dlg.ftp_dork_var.get() == "ftp old"
    assert dlg.http_dork_var.get() == "http.title:\"Index of /\""


def test_populate_from_dorkbook_rejects_unknown_protocol():
    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.smb_dork_var = _Var("smb old")
    dlg.ftp_dork_var = _Var("ftp old")
    dlg.http_dork_var = _Var("http old")
    dlg.focus_dialog = lambda: None

    with pytest.raises(ValueError, match="Unsupported protocol"):
        dlg.populate_from_dorkbook(protocol="SMTP", query="port:25")


def test_open_dorkbook_uses_current_scan_context(monkeypatch):
    dlg = dork_editor_dialog.ScanDorkEditorDialog.__new__(dork_editor_dialog.ScanDorkEditorDialog)
    dlg.dialog = object()
    dlg.parent = object()
    dlg.settings_manager = object()
    dlg.config_path = Path("/tmp/config.json")
    dlg._messagebox_parent = lambda: object()

    calls = []
    fake_module = types.ModuleType("gui.components.dorkbook_window")
    fake_module.show_dorkbook_window = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "gui.components.dorkbook_window", fake_module)

    dlg._open_dorkbook()

    assert len(calls) == 1
    assert calls[0]["scan_query_config_path"] == "/tmp/config.json"
