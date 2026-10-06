"""Dork application and stale-form regression checks without Tk or user state."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gui.components import dorkbook_events as events


class Var:
    def __init__(self, value):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


def _scan(tmp_path, value="old"):
    return SimpleNamespace(config_path=tmp_path / "config.json", searxng_query_var=Var(value),
                           _self_hosted_default="old")


@pytest.mark.parametrize("current,expected", [("old", "new"), ("manual", "manual")])
def test_external_refresh_preserves_manual_query(monkeypatch, tmp_path, current, expected):
    dialog = _scan(tmp_path, current)
    monkeypatch.setattr(events, "read_defaults", lambda _: {"self_hosted": "new"})
    events.refresh_self_hosted_query(dialog)
    assert dialog.searxng_query_var.get() == expected
    assert dialog._self_hosted_default == "new"


def test_explicit_apply_updates_matching_input_without_changing_options(tmp_path):
    dialog = _scan(tmp_path, "manual")
    dialog.provider_searxng_var = Var(False)
    payload = (events.config_identity(dialog.config_path), "self_hosted", "applied")
    events.refresh_self_hosted_query(dialog, applied=payload)
    assert dialog.searxng_query_var.get() == "applied"
    assert dialog.provider_searxng_var.get() is False


@pytest.mark.parametrize("path,destination", [("other.json", "self_hosted"), ("config.json", "shodan:HTTP")])
def test_other_destination_apply_does_not_change_input(tmp_path, path, destination):
    dialog = _scan(tmp_path, "manual")
    events.refresh_self_hosted_query(dialog, applied=(events.config_identity(tmp_path / path), destination, "new"))
    assert dialog.searxng_query_var.get() == "manual"


def _shodan_scan(tmp_path):
    return SimpleNamespace(
        config_path=tmp_path / "config.json",
        smb_shodan_query_var=Var("old_smb"),
        ftp_shodan_query_var=Var("old_ftp"),
        http_shodan_query_var=Var("old_http"),
        _shodan_smb_default="old_smb",
        _shodan_ftp_default="old_ftp",
        _shodan_http_default="old_http",
    )


def test_shodan_refresh_preserves_edit_and_snaps_untouched(monkeypatch, tmp_path):
    dialog = _shodan_scan(tmp_path)
    dialog.smb_shodan_query_var.set("manual_smb")  # edited away from baseline
    monkeypatch.setattr(events, "read_defaults", lambda _: {
        "shodan:SMB": "new_smb", "shodan:FTP": "new_ftp", "shodan:HTTP": "new_http",
    })
    events.refresh_shodan_queries(dialog)
    # Edited SMB row survives; its baseline still advances to latest.
    assert dialog.smb_shodan_query_var.get() == "manual_smb"
    assert dialog._shodan_smb_default == "new_smb"
    # Untouched FTP/HTTP rows snap to the latest default.
    assert dialog.ftp_shodan_query_var.get() == "new_ftp"
    assert dialog._shodan_ftp_default == "new_ftp"
    assert dialog.http_shodan_query_var.get() == "new_http"
    assert dialog._shodan_http_default == "new_http"


def test_shodan_apply_replaces_only_matching_destination(tmp_path):
    dialog = _shodan_scan(tmp_path)
    dialog.smb_shodan_query_var.set("manual_smb")
    dialog.http_shodan_query_var.set("manual_http")
    payload = (events.config_identity(dialog.config_path), "shodan:FTP", "applied_ftp")
    events.refresh_shodan_queries(dialog, applied=payload)
    assert dialog.ftp_shodan_query_var.get() == "applied_ftp"
    assert dialog._shodan_ftp_default == "applied_ftp"
    # SMB + HTTP untouched (var and baseline).
    assert dialog.smb_shodan_query_var.get() == "manual_smb"
    assert dialog._shodan_smb_default == "old_smb"
    assert dialog.http_shodan_query_var.get() == "manual_http"
    assert dialog._shodan_http_default == "old_http"


@pytest.mark.parametrize("path,destination", [
    ("other.json", "shodan:SMB"),   # right destination, wrong identity
    ("config.json", "self_hosted"),  # right identity, non-shodan destination
])
def test_shodan_apply_ignores_wrong_identity_or_destination(tmp_path, path, destination):
    dialog = _shodan_scan(tmp_path)
    dialog.smb_shodan_query_var.set("manual_smb")
    events.refresh_shodan_queries(
        dialog, applied=(events.config_identity(tmp_path / path), destination, "x"),
    )
    assert dialog.smb_shodan_query_var.get() == "manual_smb"
    assert dialog.ftp_shodan_query_var.get() == "old_ftp"
    assert dialog.http_shodan_query_var.get() == "old_http"
    assert dialog._shodan_smb_default == "old_smb"


def test_scan_options_no_longer_persist_run_query():
    from gui.components.scan_provider_options import persist_searxng_settings
    dlg = SimpleNamespace(searxng_instance_url_var=Var("http://local"), searxng_query_var=Var("manual"),
                          searxng_max_results_var=Var("10"))
    settings = MagicMock()
    persist_searxng_settings(dlg, settings)
    keys = [call.args[0] for call in settings.set_setting.call_args_list]
    assert "unified_scan_dialog.searxng_query" not in keys
    assert "unified_scan_dialog.searxng_instance_url" in keys
