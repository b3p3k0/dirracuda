"""Regression tests for app config dialog validation popups and wordlist defaults."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog, open_app_config_dialog
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


def _load_xsmbseek_config_class():
    loader = SourceFileLoader("dirracuda_app_config_test", str(Path("dirracuda").resolve()))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.XSMBSeekConfig


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
    dlg.se_dork_instance_url_var = _Var("http://searx.local")
    dlg.se_dork_query_var = _Var("site:*")
    dlg.se_dork_max_results_var = _Var("50")
    dlg.se_dork_bulk_probe_enabled_var = _BoolVar(False)
    dlg.se_dork_probe_workers_var = _Var("3")
    dlg.reddit_mode_var = _Var("feed")
    dlg.reddit_sort_var = _Var("new")
    dlg.reddit_top_window_var = _Var("week")
    dlg.reddit_query_var = _Var("")
    dlg.reddit_username_var = _Var("")
    dlg.reddit_max_posts_var = _Var("50")
    dlg.reddit_parse_body_var = _BoolVar(True)
    dlg.reddit_include_nsfw_var = _BoolVar(False)
    dlg.reddit_replace_cache_var = _BoolVar(False)
    dlg.reddit_bulk_probe_enabled_var = _BoolVar(False)
    dlg.keymaster_auto_check_query_credits_var = _BoolVar(True)
    dlg.censys_pat_var = _Var("")
    dlg.censys_org_id_var = _Var("")
    dlg.censys_credit_profile_var = _Var("free_starter")
    dlg.censys_max_pages_var = _Var("5")
    dlg.censys_query_hours_var = _Var("24")
    dlg.censys_page_size_var = _Var("100")
    dlg.censys_ipv6_enabled_var = _BoolVar(False)
    dlg.quarantine_var = _Var("/tmp/quarantine")
    dlg.smb_dork_var = _Var("smb authentication: disabled")
    dlg.ftp_dork_var = _Var('port:21 "230 Login successful"')
    dlg.http_dork_var = _Var('http.title:"Index of /"')

    dlg.smbseek_path = "/tmp/backend"
    dlg.database_path = "/tmp/smbseek.db"
    dlg.config_path = "/tmp/config.json"
    dlg.api_key = "APIKEY"
    dlg.se_dork_instance_url = "http://searx.local"
    dlg.se_dork_query = "site:*"
    dlg.se_dork_max_results = 50
    dlg.se_dork_bulk_probe_enabled = False
    dlg.se_dork_probe_workers = 3
    dlg.reddit_mode = "feed"
    dlg.reddit_sort = "new"
    dlg.reddit_top_window = "week"
    dlg.reddit_query = ""
    dlg.reddit_username = ""
    dlg.reddit_max_posts = 50
    dlg.reddit_parse_body = True
    dlg.reddit_include_nsfw = False
    dlg.reddit_replace_cache = False
    dlg.reddit_bulk_probe_enabled = False
    dlg.keymaster_auto_check_query_credits = True
    dlg.censys_pat = ""
    dlg.censys_org_id = ""
    dlg.censys_credit_profile = "free_starter"
    dlg.censys_max_pages = 5
    dlg.censys_query_hours = 24
    dlg.censys_page_size = 100
    dlg.censys_ipv6_enabled = False
    dlg.quarantine_path = "/tmp/quarantine"
    dlg.smb_dork = "smb authentication: disabled"
    dlg.ftp_dork = 'port:21 "230 Login successful"'
    dlg.http_dork = 'http.title:"Index of /"'
    dlg._open_dork_values = {
        "smb_dork": dlg.smb_dork,
        "ftp_dork": dlg.ftp_dork,
        "http_dork": dlg.http_dork,
    }

    dlg._validate_all_fields = lambda: None
    return dlg


def _set_clamav_form_vars(dlg: AppConfigDialog, *, enabled: bool) -> None:
    dlg.clamav_enabled = not enabled
    dlg.clamav_backend = "auto"
    dlg.clamav_auto_promote_clean = False
    dlg.clamav_enabled_var = _BoolVar(enabled)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/data/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.clamav_auto_promote_clean_var = _BoolVar(False)
    dlg.quarantine_tmpfs_enabled_var = _BoolVar(False)


def _base_validation(*, api_key_valid=True) -> dict:
    return {
        "smbseek": {"valid": True, "message": ""},
        "database": {"valid": True, "message": ""},
        "config": {"valid": True, "message": ""},
        "api_key": {"valid": api_key_valid, "message": ""},
        "se_dork_instance_url": {"valid": True, "message": ""},
        "se_dork_query": {"valid": True, "message": ""},
        "se_dork_max_results": {"valid": True, "message": ""},
        "se_dork_probe_workers": {"valid": True, "message": ""},
        "reddit_mode": {"valid": True, "message": ""},
        "reddit_sort": {"valid": True, "message": ""},
        "reddit_top_window": {"valid": True, "message": ""},
        "reddit_query": {"valid": True, "message": ""},
        "reddit_username": {"valid": True, "message": ""},
        "reddit_max_posts": {"valid": True, "message": ""},
        "censys_pat": {"valid": True, "message": ""},
        "censys_org_id": {"valid": True, "message": ""},
        "censys_credit_profile": {"valid": True, "message": ""},
        "censys_max_pages": {"valid": True, "message": ""},
        "censys_query_hours": {"valid": True, "message": ""},
        "censys_page_size": {"valid": True, "message": ""},
        "quarantine": {"valid": True, "message": ""},
        "smb_dork": {"valid": True, "message": ""},
        "ftp_dork": {"valid": True, "message": ""},
        "http_dork": {"valid": True, "message": ""},
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
    dlg.clamav_auto_promote_clean = False
    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.clamav_enabled_var = _BoolVar(True)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.clamav_auto_promote_clean_var = _BoolVar(False)
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


def test_validate_and_save_persists_experimental_settings(monkeypatch):
    class _SettingsManagerStub:
        def __init__(self) -> None:
            self.set_values = {}
            self.backend_path = None
            self.database_path = None

        def set_backend_path(self, value, validate=False):
            self.backend_path = (value, validate)

        def set_database_path(self, value, validate=False):
            self.database_path = (value, validate)

        def set_setting(self, key, value):
            self.set_values[key] = value

    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()
    dlg.settings_manager = _SettingsManagerStub()
    dlg.se_dork_instance_url_var = _Var("https://searx.example")
    dlg.se_dork_query_var = _Var("site:example.com")
    dlg.se_dork_max_results_var = _Var("9999")
    dlg.se_dork_bulk_probe_enabled_var = _BoolVar(True)
    dlg.se_dork_probe_workers_var = _Var("0")
    dlg.reddit_mode_var = _Var("search")
    dlg.reddit_sort_var = _Var("top")
    dlg.reddit_top_window_var = _Var("month")
    dlg.reddit_query_var = _Var("ransomware")
    dlg.reddit_username_var = _Var("")
    dlg.reddit_max_posts_var = _Var("9999")
    dlg.reddit_parse_body_var = _BoolVar(False)
    dlg.reddit_include_nsfw_var = _BoolVar(True)
    dlg.reddit_replace_cache_var = _BoolVar(True)
    dlg.reddit_bulk_probe_enabled_var = _BoolVar(True)
    dlg.keymaster_auto_check_query_credits_var = _BoolVar(False)

    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True
    saved = dlg.settings_manager.set_values
    assert saved["se_dork.instance_url"] == "https://searx.example"
    assert saved["se_dork.query"] == "site:example.com"
    assert saved["se_dork.max_results"] == 500
    assert saved["se_dork.bulk_probe_enabled"] is True
    assert saved["probe.batch_max_workers"] == 1
    assert saved["reddit_grab.mode"] == "search"
    assert saved["reddit_grab.sort"] == "top"
    assert saved["reddit_grab.top_window"] == "month"
    assert saved["reddit_grab.query"] == "ransomware"
    assert saved["reddit_grab.max_posts"] == 200
    assert saved["reddit_grab.parse_body"] is False
    assert saved["reddit_grab.include_nsfw"] is True
    assert saved["reddit_grab.replace_cache"] is True
    assert saved["reddit_grab.bulk_probe_enabled"] is True
    assert saved["keymaster.auto_check_query_credits"] is False
    assert "dorkbook.active_protocol_tab" not in saved


def test_validate_and_save_refreshes_when_only_clamav_auto_promote_changes(monkeypatch):
    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()

    dlg.clamav_enabled = False
    dlg.clamav_backend = "auto"
    dlg.clamav_auto_promote_clean = False
    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.clamav_enabled_var = _BoolVar(False)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.clamav_auto_promote_clean_var = _BoolVar(True)
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
    dlg.clamav_auto_promote_clean = False
    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.clamav_enabled_var = _BoolVar(False)
    dlg.clamav_backend_var = _Var("auto")
    dlg.clamav_timeout_var = _Var("60")
    dlg.clamav_extracted_root_var = _Var("~/.dirracuda/extracted")
    dlg.clamav_known_bad_subdir_var = _Var("known_bad")
    dlg.clamav_show_results_var = _BoolVar(True)
    dlg.clamav_auto_promote_clean_var = _BoolVar(False)
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


def test_validate_and_save_persists_clamav_toggle_with_real_main_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "conf" / "config.json"
    db_path = tmp_path / "data" / "dirracuda.db"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "clamav": {"enabled": False, "backend": "auto"},
                "gui_app": {"backend_path": str(tmp_path), "database_path": str(db_path)},
                "database": {"path": str(db_path)},
            }
        ),
        encoding="utf-8",
    )

    XSMBSeekConfig = _load_xsmbseek_config_class()
    dlg = _build_dialog(_base_validation())
    dlg.main_config = XSMBSeekConfig(str(cfg_path))
    dlg.smbseek_var = _Var(str(tmp_path))
    dlg.database_var = _Var(str(db_path))
    dlg.config_var = _Var(str(cfg_path))
    dlg.quarantine_var = _Var(str(tmp_path / "quarantine"))
    dlg.wordlist_var = _Var("")

    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    _set_clamav_form_vars(dlg, enabled=True)
    assert dlg._validate_and_save() is True
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted["clamav"]["enabled"] is True
    assert dlg.main_config.config["clamav"]["enabled"] is True

    _set_clamav_form_vars(dlg, enabled=False)
    assert dlg._validate_and_save() is True
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted["clamav"]["enabled"] is False
    assert dlg.main_config.config["clamav"]["enabled"] is False


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


def test_validate_and_save_ignores_hidden_invalid_censys_org_id(monkeypatch):
    validation = _base_validation()
    validation["censys_org_id"] = {"valid": False, "message": "Organization ID must be a valid UUID."}
    dlg = _build_dialog(validation)
    dlg.main_config = _MainConfigStub()

    calls = []
    monkeypatch.setattr(
        "gui.components.app_config_dialog.messagebox.showerror",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )

    assert dlg._validate_and_save() is True
    assert calls == []


def test_validate_and_save_allows_empty_optional_censys_fields(monkeypatch):
    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()
    dlg.censys_pat_var = _Var("")
    dlg.censys_org_id_var = _Var("")
    dlg.censys_credit_profile_var = _Var("free_starter")
    dlg.censys_max_pages_var = _Var("5")
    dlg.censys_query_hours_var = _Var("24")
    dlg.censys_page_size_var = _Var("100")
    dlg.censys_ipv6_enabled_var = _BoolVar(False)

    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True


def test_validate_and_save_preserves_existing_censys_section_when_hidden(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    censys_section = {
        "personal_access_token": "keep-me",
        "organization_id": "11111111-2222-3333-4444-555555555555",
        "credit_profile": "search_enterprise",
        "defaults": {
            "max_pages": 77,
            "query_hours": 12,
            "page_size": 33,
            "ipv6_enabled": True,
        },
        "custom_key": "preserve",
    }
    cfg_path.write_text(json.dumps({"censys": censys_section}, indent=2), encoding="utf-8")

    dlg = _build_dialog(_base_validation())
    dlg.main_config = _MainConfigStub()
    dlg.config_var = _Var(str(cfg_path))
    dlg.config_path = str(cfg_path)

    monkeypatch.setattr(
        "gui.components.app_config_dialog.normalize_database_path",
        lambda *_args, **_kwargs: Path("/tmp/smbseek.db"),
    )
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showwarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.messagebox.showerror", lambda *_args, **_kwargs: None)

    assert dlg._validate_and_save() is True

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["censys"] == censys_section


def test_top_level_tab_order():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    assert dlg.TOP_LEVEL_TABS == ("Core Paths", "Runtime", "Security", "Experimental")


def test_experimental_tab_order():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    assert dlg.EXPERIMENTAL_TABS == ("SearXNG", "Reddit", "Keymaster", "Dorkbook")


def test_build_top_level_tabs_uses_expected_order(monkeypatch):
    class _Notebook:
        def __init__(self, _parent=None):
            self.added = []

        def add(self, _frame, text):
            self.added.append(text)

    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.theme = SimpleNamespace(apply_to_widget=lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.tk.Frame", lambda *_args, **_kwargs: object())

    notebook = _Notebook()
    tabs = dlg._build_top_level_tabs(notebook)
    assert notebook.added == list(AppConfigDialog.TOP_LEVEL_TABS)
    assert set(tabs.keys()) == set(AppConfigDialog.TOP_LEVEL_TABS)


def test_build_experimental_tabs_uses_expected_order(monkeypatch):
    created = {}

    class _Notebook:
        def __init__(self, _parent=None):
            self.added = []

        def pack(self, **_kwargs):
            return None

        def add(self, _frame, text):
            self.added.append(text)

    def _make_notebook(parent):
        nb = _Notebook(parent)
        created["notebook"] = nb
        return nb

    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.theme = SimpleNamespace(apply_to_widget=lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gui.components.app_config_dialog.ttk.Notebook", _make_notebook)
    monkeypatch.setattr("gui.components.app_config_dialog.tk.Frame", lambda *_args, **_kwargs: object())

    tabs = dlg._build_experimental_tabs(parent=object())
    nb = created["notebook"]
    assert nb.added == list(AppConfigDialog.EXPERIMENTAL_TABS)
    assert set(tabs.keys()) == set(AppConfigDialog.EXPERIMENTAL_TABS)


def test_load_experimental_settings_from_settings_manager_clamps_and_sanitizes():
    class _SettingsManager:
        def __init__(self, values):
            self.values = values

        def get_setting(self, key, default=None):
            return self.values.get(key, default)

    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.settings_manager = _SettingsManager(
        {
            "se_dork.instance_url": " https://searx.example ",
            "se_dork.query": " query ",
            "se_dork.max_results": 9999,
            "se_dork.bulk_probe_enabled": "yes",
            "probe.batch_max_workers": 0,
            "reddit_grab.mode": "bad",
            "reddit_grab.sort": "bad",
            "reddit_grab.top_window": "bad",
            "reddit_grab.query": "foo",
            "reddit_grab.username": "bar",
            "reddit_grab.max_posts": -1,
            "reddit_grab.parse_body": "0",
            "reddit_grab.include_nsfw": "1",
            "reddit_grab.replace_cache": "1",
            "reddit_grab.bulk_probe_enabled": "1",
            "keymaster.auto_check_query_credits": "0",
            "dorkbook.active_protocol_tab": "bad",
        }
    )
    dlg.se_dork_instance_url = ""
    dlg.se_dork_query = ""
    dlg.se_dork_max_results = 50
    dlg.se_dork_bulk_probe_enabled = False
    dlg.se_dork_probe_workers = 3
    dlg.reddit_mode = "feed"
    dlg.reddit_sort = "new"
    dlg.reddit_top_window = "week"
    dlg.reddit_query = ""
    dlg.reddit_username = ""
    dlg.reddit_max_posts = 50
    dlg.reddit_parse_body = True
    dlg.reddit_include_nsfw = False
    dlg.reddit_replace_cache = False
    dlg.reddit_bulk_probe_enabled = False
    dlg.keymaster_auto_check_query_credits = True
    dlg.dorkbook_active_protocol_tab = "SMB"

    dlg._load_experimental_settings_from_settings_manager()

    assert dlg.se_dork_instance_url == "https://searx.example"
    assert dlg.se_dork_query == " query "
    assert dlg.se_dork_max_results == 500
    assert dlg.se_dork_bulk_probe_enabled is True
    assert dlg.se_dork_probe_workers == 1
    assert dlg.reddit_mode == "feed"
    assert dlg.reddit_sort == "new"
    assert dlg.reddit_top_window == "week"
    assert dlg.reddit_query == "foo"
    assert dlg.reddit_username == "bar"
    assert dlg.reddit_max_posts == 1
    assert dlg.reddit_parse_body is False
    assert dlg.reddit_include_nsfw is True
    assert dlg.reddit_replace_cache is True
    assert dlg.reddit_bulk_probe_enabled is True
    assert dlg.keymaster_auto_check_query_credits is False
    assert dlg.dorkbook_active_protocol_tab == "SMB"


def test_validate_field_reddit_query_required_for_search():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.reddit_mode_var = _Var("search")
    dlg.reddit_query_var = _Var("")
    dlg.validation_results = {"reddit_query": {"valid": True, "message": ""}}
    dlg.status_labels = {"reddit_query": SimpleNamespace(config=lambda **_kwargs: None)}
    dlg.theme = SimpleNamespace(
        get_icon_symbol=lambda _name: "!",
        colors={"success": "#0f0", "error": "#f00"},
    )

    dlg._validate_field("reddit_query")
    assert dlg.validation_results["reddit_query"]["valid"] is False


def test_validate_field_reddit_username_required_for_user_mode():
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.reddit_mode_var = _Var("user")
    dlg.reddit_username_var = _Var("bad name")
    dlg.validation_results = {"reddit_username": {"valid": True, "message": ""}}
    dlg.status_labels = {"reddit_username": SimpleNamespace(config=lambda **_kwargs: None)}
    dlg.theme = SimpleNamespace(
        get_icon_symbol=lambda _name: "!",
        colors={"success": "#0f0", "error": "#f00"},
    )

    dlg._validate_field("reddit_username")
    assert dlg.validation_results["reddit_username"]["valid"] is False


def test_default_gui_settings_include_experimental_sections():
    assert "se_dork" in DEFAULT_GUI_SETTINGS
    assert "keymaster" in DEFAULT_GUI_SETTINGS
    assert "dorkbook" in DEFAULT_GUI_SETTINGS
    assert DEFAULT_GUI_SETTINGS["se_dork"]["max_results"] == 50
    assert DEFAULT_GUI_SETTINGS["keymaster"]["auto_check_query_credits"] is True
    assert DEFAULT_GUI_SETTINGS["dorkbook"]["active_protocol_tab"] == "SMB"
