"""Tests for dashboard scan-start Shodan API key gate behavior."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget


class _Backend:
    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode


def _make_dashboard(tmp_path: Path, config_payload: dict, *, mock_mode: bool = False) -> tuple[DashboardWidget, Path]:
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config_payload), encoding="utf-8")

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = object()
    dash.config_path = str(cfg)
    dash.settings_manager = None
    dash.backend_interface = _Backend(mock_mode=mock_mode)
    return dash, cfg


def test_api_key_gate_allows_scan_when_configured(monkeypatch, tmp_path):
    dash, _cfg = _make_dashboard(tmp_path, {"shodan": {"api_key": "CONFIG_KEY"}})

    prompt_called = {"count": 0}

    def _prompt():
        prompt_called["count"] += 1
        return "SHOULD_NOT_BE_USED"

    monkeypatch.setattr(dash, "_prompt_for_shodan_api_key", _prompt)

    scan_options = {}
    assert dash._ensure_shodan_api_key_for_scan(scan_options) is True
    assert prompt_called["count"] == 0
    assert "api_key_override" not in scan_options


def test_api_key_gate_persists_override_without_prompt(monkeypatch, tmp_path):
    dash, cfg = _make_dashboard(tmp_path, {"shodan": {"api_key": ""}})

    monkeypatch.setattr(dash, "_prompt_for_shodan_api_key", lambda: None)

    scan_options = {"api_key_override": "OVERRIDE_KEY"}
    assert dash._ensure_shodan_api_key_for_scan(scan_options) is True

    persisted = json.loads(cfg.read_text(encoding="utf-8"))
    assert persisted["shodan"]["api_key"] == "OVERRIDE_KEY"
    assert scan_options["api_key_override"] == "OVERRIDE_KEY"


def test_api_key_gate_prompts_and_persists_when_missing(monkeypatch, tmp_path):
    dash, cfg = _make_dashboard(tmp_path, {"shodan": {"api_key": ""}})

    monkeypatch.setattr(dash, "_prompt_for_shodan_api_key", lambda: "PROMPT_KEY")

    scan_options = {}
    assert dash._ensure_shodan_api_key_for_scan(scan_options) is True

    persisted = json.loads(cfg.read_text(encoding="utf-8"))
    assert persisted["shodan"]["api_key"] == "PROMPT_KEY"
    assert scan_options["api_key_override"] == "PROMPT_KEY"


def test_api_key_gate_cancelled_prompt_aborts_scan(monkeypatch, tmp_path):
    dash, cfg = _make_dashboard(tmp_path, {"shodan": {"api_key": ""}})

    monkeypatch.setattr(dash, "_prompt_for_shodan_api_key", lambda: None)
    calls = {"info": 0}

    def _showinfo(*_args, **_kwargs):
        calls["info"] += 1

    monkeypatch.setattr("gui.components.dashboard.messagebox.showinfo", _showinfo)

    scan_options = {}
    assert dash._ensure_shodan_api_key_for_scan(scan_options) is False
    assert calls["info"] == 1

    persisted = json.loads(cfg.read_text(encoding="utf-8"))
    assert persisted["shodan"]["api_key"] == ""


def test_api_key_gate_skips_prompt_in_mock_mode(monkeypatch, tmp_path):
    dash, _cfg = _make_dashboard(tmp_path, {"shodan": {"api_key": ""}}, mock_mode=True)

    prompt_called = {"count": 0}

    def _prompt():
        prompt_called["count"] += 1
        return "IGNORED"

    monkeypatch.setattr(dash, "_prompt_for_shodan_api_key", _prompt)

    assert dash._ensure_shodan_api_key_for_scan({}) is True
    assert prompt_called["count"] == 0
