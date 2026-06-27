"""Tests for the desktop Server Detail Sherlock section (C6)."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.sherlock_details import format_sherlock_section


def _result(**overrides):
    base = {
        "stale": False,
        "total_hit_count": 2,
        "highest_severity": "high",
        "detail_count": 2,
        "truncated": False,
        "scanned_at": "2026-06-27T10:00:00",
        "hits": [
            {
                "severity": "high",
                "category": "Credentials",
                "label": "Password files",
                "pattern": "*password*",
                "display_path": "share/passwords.txt",
            },
            {
                "severity": "med",
                "category": "Finance",
                "label": "Payroll",
                "pattern": "*payroll*",
                "display_path": "share/payroll.xlsx",
            },
        ],
    }
    base.update(overrides)
    return base


def test_format_none_reports_no_scan():
    text = format_sherlock_section(None)
    assert "🔎 Sherlock:" in text
    assert "No Sherlock scan recorded" in text


def test_format_stale_explains_and_advises_rescan():
    text = format_sherlock_section(_result(stale=True))
    assert "stale" in text.lower()
    assert "re-scan" in text.lower()
    # Quiet on the count when stale: no risk line.
    assert "Risk:" not in text


def test_format_zero_hits_reports_no_match():
    text = format_sherlock_section(_result(total_hit_count=0, hits=[]))
    assert "No risky names matched" in text


def test_format_hits_render_severity_category_label_pattern_path():
    text = format_sherlock_section(_result())
    assert "Risk: HIGH 2" in text
    assert "HIGH · Credentials · Password files (*password*) — share/passwords.txt" in text
    assert "MED · Finance · Payroll (*payroll*) — share/payroll.xlsx" in text


def test_format_truncated_adds_more_notice():
    text = format_sherlock_section(
        _result(total_hit_count=5, detail_count=2, truncated=True)
    )
    assert "+3 more not shown" in text


def test_format_malformed_severity_reports_count_without_label():
    text = format_sherlock_section(_result(highest_severity="bogus"))
    # Unrecognized highest severity -> count without a mislabeled severity.
    assert "Risk: 2 hit(s)" in text
    assert "HIGH" not in text.split("\n", 2)[1]  # not on the risk line


# --- integration: detail render reloads Sherlock (post-reprobe refresh) ---


def _import_details_module_isolated():
    sentinel = object()
    module_names = [
        "gui.components.server_list_window",
        "gui.components.server_list_window.details",
        "gui.utils.probe_runner",
        "gui.utils.probe_patterns",
        "gui.utils.extract_runner",
        "gui.utils.probe_cache_dispatch",
        "gui.utils.database_access",
        "gui.utils.dialog_helpers",
        "gui.components.batch_extract_dialog",
        "shared.quarantine",
    ]
    prior_modules = {name: sys.modules.get(name, sentinel) for name in module_names}
    slw_dir = Path(__file__).resolve().parents[1] / "components" / "server_list_window"

    try:
        for name in module_names:
            sys.modules.pop(name, None)

        def _stub_module(name, attrs):
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod

        slw_pkg = types.ModuleType("gui.components.server_list_window")
        slw_pkg.__path__ = [str(slw_dir)]
        sys.modules["gui.components.server_list_window"] = slw_pkg

        class _DatabaseReader:
            def __init__(self, *_args, **_kwargs):
                pass

        _stub_module("gui.utils.probe_runner", {})
        _stub_module("gui.utils.extract_runner", {})
        _stub_module(
            "gui.utils.probe_patterns",
            {
                "IndicatorPattern": type("IndicatorPattern", (), {}),
                "attach_indicator_analysis": lambda result, _patterns: result,
            },
        )
        _stub_module(
            "gui.utils.probe_cache_dispatch",
            {
                "load_probe_result_for_host": lambda *_args, **_kwargs: None,
                "dispatch_probe_run": lambda *_args, **_kwargs: {"shares": [], "errors": []},
            },
        )
        _stub_module("gui.utils.database_access", {"DatabaseReader": _DatabaseReader})
        _stub_module("gui.utils.dialog_helpers", {"ensure_dialog_focus": lambda *_args, **_kwargs: None})
        _stub_module(
            "gui.components.batch_extract_dialog",
            {"BatchExtractSettingsDialog": type("BatchExtractSettingsDialog", (), {})},
        )
        _stub_module(
            "shared.quarantine",
            {"create_quarantine_dir": lambda *_args, **_kwargs: Path("/tmp")},
        )

        return importlib.import_module("gui.components.server_list_window.details")
    finally:
        for name, previous in prior_modules.items():
            if previous is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class _FakeText:
    def __init__(self):
        self.content = ""

    def configure(self, **_kwargs):
        pass

    def delete(self, *_args):
        self.content = ""

    def insert(self, _index, text):
        self.content += text


def test_render_reloads_sherlock_section_after_reprobe(monkeypatch):
    details = _import_details_module_isolated()
    seq = iter([None, _result()])
    monkeypatch.setattr(
        details, "_load_sherlock_result_for_detail", lambda *_a, **_k: next(seq)
    )

    server = {"ip_address": "1.2.3.4", "host_type": "S"}
    widget = _FakeText()

    # First render (e.g. on open): no scan recorded.
    details._render_server_details(widget, server, None)
    assert "No Sherlock scan recorded" in widget.content

    # Re-render after a probe rewrote the Sherlock result: section refreshes.
    details._render_server_details(widget, server, {"run_at": "x", "shares": []})
    assert "Risk: HIGH 2" in widget.content
    assert "No Sherlock scan recorded" not in widget.content


def test_load_sherlock_result_returns_none_without_settings_manager():
    details = _import_details_module_isolated()
    assert details._load_sherlock_result_for_detail({"ip_address": "1.2.3.4"}, settings_manager=None) is None


def test_load_sherlock_result_calls_reader_with_host_identity(monkeypatch):
    details = _import_details_module_isolated()

    captured = {}

    class _Reader:
        def __init__(self, db_path):
            captured["db_path"] = db_path

        def get_sherlock_result_for_host(self, ip, host_type, *, protocol_server_id=None, port=None):
            captured.update(
                ip=ip, host_type=host_type, protocol_server_id=protocol_server_id, port=port
            )
            return {"stale": False, "total_hit_count": 0, "hits": []}

    class _Settings:
        def get_database_path(self):
            return "/tmp/main.db"

    monkeypatch.setattr(details, "DatabaseReader", _Reader)
    out = details._load_sherlock_result_for_detail(
        {"ip_address": "9.9.9.9", "host_type": "H", "protocol_server_id": 4, "port": 8443},
        settings_manager=_Settings(),
    )
    assert out == {"stale": False, "total_hit_count": 0, "hits": []}
    assert captured["ip"] == "9.9.9.9"
    assert captured["host_type"] == "H"
    assert captured["protocol_server_id"] == 4
    assert captured["port"] == 8443
