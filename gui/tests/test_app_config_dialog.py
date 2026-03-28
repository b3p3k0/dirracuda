"""Regression tests for app config dialog validation popups and wordlist defaults."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog, open_app_config_dialog
from gui.components.pry_dialog import PryDialog
from gui.utils.default_gui_settings import DEFAULT_GUI_SETTINGS


class _Var:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _BoolVar:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value


class _DialogWidget:
    def __init__(self, exists: bool = True) -> None:
        self._exists = exists

    def winfo_exists(self) -> int:
        return 1 if self._exists else 0


class _MainConfigStub:
    def __init__(self) -> None:
        self.config = {}

    def set_config_path(self, _value: str) -> None:
        return None

    def set_smbseek_path(self, _value: str) -> None:
        return None

    def set_database_path(self, _value: str) -> None:
        return None

    def save_config(self) -> bool:
        return True


def _build_dialog(validation_results: dict, *, parent=None, dialog=None) -> AppConfigDialog:
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.parent = parent if parent is not None else object()
    dlg.dialog = dialog if dialog is not None else _DialogWidget(exists=True)
    dlg.settings_manager = None
    dlg.config_editor_callback = None
    dlg.main_config = None
    dlg.refresh_callback = None

    dlg.validation_results = validation_results

    dlg.smbseek_var = _Var("/tmp/backend")
    dlg.database_var = _Var("/tmp/smbseek.db")
    dlg.config_var = _Var("/tmp/config.json")
    dlg.api_key_var = _Var("APIKEY")
    dlg.quarantine_var = _Var("/tmp/quarantine")
    dlg.wordlist_var = _Var("/tmp/wordlist.txt")

    dlg.smbseek_path = "/tmp/backend"
    dlg.database_path = "/tmp/smbseek.db"
    dlg.config_path = "/tmp/config.json"
    dlg.api_key = "APIKEY"
    dlg.quarantine_path = "/tmp/quarantine"
    dlg.wordlist_path = ""

    dlg._validate_all_fields = lambda: None
    return dlg


def _base_validation(*, api_key_valid=True, wordlist_valid=True) -> dict:
    return {
        "smbseek": {"valid": True, "message": ""},
        "database": {"valid": True, "message": ""},
        "config": {"valid": True, "message": ""},
        "api_key": {"valid": api_key_valid, "message": ""},
        "quarantine": {"valid": True, "message": ""},
        "wordlist": {"valid": wordlist_valid, "message": ""},
    }


def test_validate_and_save_invalid_required_uses_dialog_parent(monkeypatch):
    validation = _base_validation()
    validation["smbseek"] = {"valid": False, "message": "Path is required."}
    dlg = _build_dialog(validation)

    calls = []
    monkeypatch.setattr(
        "gui.components.app_config_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert dlg._validate_and_save() is False
    assert len(calls) == 1
    assert calls[0][1]["parent"] is dlg.dialog


def test_validate_and_save_wordlist_warning_uses_dialog_parent(monkeypatch):
    dlg = _build_dialog(_base_validation(api_key_valid=True, wordlist_valid=False))
    dlg.main_config = _MainConfigStub()

    monkeypatch.setattr("gui.components.app_config_dialog.normalize_database_path", lambda *_args, **_kwargs: Path("/tmp/smbseek.db"))

    warning_calls = []
    monkeypatch.setattr(
        "gui.components.app_config_dialog.messagebox.showwarning",
        lambda *args, **kwargs: warning_calls.append((args, kwargs)),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True
    assert len(warning_calls) == 1
    assert warning_calls[0][1]["parent"] is dlg.dialog


def test_validate_and_save_exception_uses_dialog_parent(monkeypatch):
    dlg = _build_dialog(_base_validation())
    monkeypatch.setattr("gui.components.app_config_dialog.normalize_database_path", lambda *_args, **_kwargs: None)

    calls = []
    monkeypatch.setattr(
        "gui.components.app_config_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert dlg._validate_and_save() is False
    assert len(calls) == 1
    assert calls[0][1]["parent"] is dlg.dialog


def test_validate_and_save_refreshes_when_only_clamav_settings_change(monkeypatch):
    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()

    dlg.clamav_enabled = False
    dlg.clamav_backend = "auto"
    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.clamav_enabled_var = _BoolVar(True)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.quarantine_tmpfs_enabled_var = _BoolVar(False)
    dlg.quarantine_tmpfs_size_var = _Var("512")

    refresh_calls = []
    dlg.refresh_callback = lambda: refresh_calls.append(True)

    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True
    assert len(refresh_calls) == 1


def test_validate_and_save_refreshes_when_only_tmpfs_settings_change(monkeypatch):
    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()

    dlg.clamav_enabled = False
    dlg.clamav_backend = "auto"
    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.clamav_enabled_var = _BoolVar(False)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.quarantine_tmpfs_enabled_var = _BoolVar(True)
    dlg.quarantine_tmpfs_size_var = _Var("512")

    refresh_calls = []
    dlg.refresh_callback = lambda: refresh_calls.append(True)

    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True
    assert len(refresh_calls) == 1


def test_open_app_config_dialog_failure_uses_parent(monkeypatch):
    parent = object()
    calls = []

    class _Raiser:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("gui.components.app_config_dialog.AppConfigDialog", _Raiser)
    monkeypatch.setattr(
        "gui.components.app_config_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    open_app_config_dialog(parent=parent)

    assert len(calls) == 1
    assert calls[0][1]["parent"] is parent


def test_app_config_load_clears_missing_legacy_wordlist(tmp_path):
    config_path = tmp_path / "conf" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"pry": {"wordlist_path": "conf/wordlists/rockyou.txt"}}),
        encoding="utf-8",
    )

    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.api_key = ""
    dlg.wordlist_path = "placeholder"
    dlg.quarantine_path = "~/.dirracuda/quarantine"

    dlg._load_runtime_settings_from_config(str(config_path))

    assert dlg.wordlist_path == ""


def test_app_config_load_preserves_existing_legacy_wordlist(tmp_path):
    config_path = tmp_path / "conf" / "config.json"
    wordlist_path = tmp_path / "conf" / "wordlists" / "rockyou.txt"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    wordlist_path.parent.mkdir(parents=True, exist_ok=True)
    wordlist_path.write_text("password\n", encoding="utf-8")
    config_path.write_text(
        json.dumps({"pry": {"wordlist_path": "conf/wordlists/rockyou.txt"}}),
        encoding="utf-8",
    )

    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.api_key = ""
    dlg.wordlist_path = ""
    dlg.quarantine_path = "~/.dirracuda/quarantine"

    dlg._load_runtime_settings_from_config(str(config_path))

    assert dlg.wordlist_path == "conf/wordlists/rockyou.txt"


def test_pry_defaults_clear_missing_legacy_wordlist(tmp_path):
    config_path = tmp_path / "conf" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"pry": {"wordlist_path": "conf/wordlists/rockyou.txt"}}),
        encoding="utf-8",
    )

    dialog = PryDialog.__new__(PryDialog)
    dialog.settings = None
    dialog.config_path = config_path

    defaults = dialog._load_defaults()

    assert defaults["wordlist_path"] == ""


def test_pry_defaults_preserve_custom_wordlist(tmp_path):
    config_path = tmp_path / "conf" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"pry": {"wordlist_path": "custom/not-there.txt"}}),
        encoding="utf-8",
    )

    dialog = PryDialog.__new__(PryDialog)
    dialog.settings = None
    dialog.config_path = config_path

    defaults = dialog._load_defaults()

    assert defaults["wordlist_path"] == "custom/not-there.txt"


def test_default_gui_settings_wordlist_is_blank():
    assert DEFAULT_GUI_SETTINGS["pry"]["wordlist_path"] == ""


def test_config_example_wordlist_is_blank():
    config_example_path = Path(__file__).resolve().parents[2] / "conf" / "config.json.example"
    parsed = json.loads(config_example_path.read_text(encoding="utf-8"))
    assert parsed["pry"]["wordlist_path"] == ""
