"""Pacing field loading, validation and persistence without a Tk display."""

from types import SimpleNamespace

import pytest

from gui.components.scan_dialog import ScanDialog
from gui.utils.default_gui_settings import DEFAULT_GUI_SETTINGS


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    dlg = ScanDialog.__new__(ScanDialog)
    dlg.config_path = tmp_path / "missing.json"
    dlg._backend_interface = None
    dlg._concurrency_upper_limit = 100
    dlg._delay_upper_limit = 60
    saved = {}
    dlg._settings_manager = SimpleNamespace(
        set_setting=lambda key, value: saved.__setitem__(key, value),
        get_setting=lambda key, default=None: saved.get(key, default),
    )
    for name, value in {
        "recent_hours": "", "rescan_all": False, "rescan_failed": False,
        "security_mode": "cautious", "verbose": False, "api_key": "",
        "country": "", "discovery_concurrency": "1", "access_concurrency": "1",
        "rate_limit_delay": "0", "share_access_delay": "0", "smb_max_results": "100",
        "bulk_probe_enabled": False, "bulk_extract_enabled": False,
        "skip_indicator_extract": True, "africa": False, "asia": False,
        "europe": False, "north_america": False, "oceania": False, "south_america": False,
    }.items():
        setattr(dlg, name + "_var", _Var(value))
    monkeypatch.setattr("gui.components.scan_dialog.resolve_config_path_from_settings", lambda _: None)
    monkeypatch.setattr("gui.components.scan_dialog.load_query_budget_state", lambda **kwargs: {
        "ftp_max_shodan_results_per_scan": 100, "http_max_shodan_results_per_scan": 100,
        "ftp_max_query_credits_per_scan": 1, "http_max_query_credits_per_scan": 1,
    })
    monkeypatch.setattr("gui.components.scan_dialog.persist_query_budget_state", lambda *args: None)
    return dlg


@pytest.mark.parametrize("delay", [0, 1])
def test_scan_dialog_accepts_and_persists_delay(dialog, delay):
    dialog.rate_limit_delay_var.set(str(delay))
    dialog.share_access_delay_var.set(str(delay))

    options = dialog._build_scan_options(None)

    for key in ("rate_limit_delay", "share_access_delay"):
        assert options[key] == delay
        assert dialog._settings_manager.get_setting("scan_dialog." + key) == delay


@pytest.mark.parametrize("key", ["rate_limit_delay", "share_access_delay"])
def test_scan_dialog_rejects_negative_delay(dialog, key):
    getattr(dialog, key + "_var").set("-1")

    with pytest.raises(ValueError, match="must be at least 0"):
        dialog._build_scan_options(None)

    assert dialog._settings_manager.get_setting("scan_dialog." + key) is None


@pytest.mark.parametrize("connection", [None, {}, {"rate_limit_delay": 0, "share_access_delay": 0},
                                         {"rate_limit_delay": 1, "share_access_delay": 2}])
def test_scan_dialog_loads_pacing_defaults(dialog, connection):
    if connection is not None:
        dialog._backend_interface = SimpleNamespace(load_effective_config=lambda: {"connection": connection})

    dialog._load_backend_defaults()

    for key in ("rate_limit_delay", "share_access_delay"):
        assert getattr(dialog, key + "_var").get() == str((connection or {}).get(key, 0))


def test_scan_dialog_gui_defaults_disable_pacing():
    assert DEFAULT_GUI_SETTINGS["scan_dialog"]["rate_limit_delay"] == 0
    assert DEFAULT_GUI_SETTINGS["scan_dialog"]["share_access_delay"] == 0
