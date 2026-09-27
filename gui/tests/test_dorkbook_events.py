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


def test_scan_options_no_longer_persist_run_query():
    from gui.components.scan_provider_options import persist_searxng_settings
    dlg = SimpleNamespace(searxng_instance_url_var=Var("http://local"), searxng_query_var=Var("manual"),
                          searxng_max_results_var=Var("10"))
    settings = MagicMock()
    persist_searxng_settings(dlg, settings)
    keys = [call.args[0] for call in settings.set_setting.call_args_list]
    assert "unified_scan_dialog.searxng_query" not in keys
    assert "unified_scan_dialog.searxng_instance_url" in keys
