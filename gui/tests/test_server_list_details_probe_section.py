"""Tests for server details probe-section rendering."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_details_module_isolated():
    """
    Import details.py without triggering server_list_window package side effects.

    The server_list_window package __init__ imports window.py, which pulls runtime
    dependencies that are optional in test environments.
    """
    sentinel = object()
    module_names = [
        "gui.components.server_list_window",
        "gui.components.server_list_window.details",
        "gui.utils.probe_cache",
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

        def _stub_module(name: str, attrs: dict) -> None:
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod

        slw_pkg = types.ModuleType("gui.components.server_list_window")
        slw_pkg.__path__ = [str(slw_dir)]
        sys.modules["gui.components.server_list_window"] = slw_pkg

        class _IndicatorPattern:
            pass

        class _DatabaseReader:
            def __init__(self, *_args, **_kwargs):
                pass

        _stub_module("gui.utils.probe_cache", {})
        _stub_module("gui.utils.probe_runner", {})
        _stub_module("gui.utils.extract_runner", {})
        _stub_module(
            "gui.utils.probe_patterns",
            {
                "IndicatorPattern": _IndicatorPattern,
                "attach_indicator_analysis": lambda result, _patterns: result,
            },
        )
        _stub_module(
            "gui.utils.probe_cache_dispatch",
            {
                "load_probe_result_for_host": lambda *_args, **_kwargs: None,
                "get_probe_snapshot_path_for_host": lambda *_args, **_kwargs: None,
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


def _snapshot(shares=None, errors=None):
    return {
        "run_at": "2026-03-27T12:00:00Z",
        "limits": {"max_directories": 3, "max_files": 5, "timeout_seconds": 10},
        "shares": shares or [],
        "errors": errors or [],
    }


def test_probe_section_root_files_show_actual_names_not_placeholder():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "http_root",
                    "root_files": ["index.html", "robots.txt"],
                    "root_files_truncated": False,
                    "directories": [],
                }
            ]
        )
    )

    assert "index.html" in text
    assert "robots.txt" in text
    assert "[[loose files]]" not in text


def test_probe_section_mixed_root_files_and_directories_render_together():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "ftp_root",
                    "root_files": ["readme.txt"],
                    "directories": [
                        {
                            "name": "public",
                            "subdirectories": ["incoming"],
                            "subdirectories_truncated": False,
                            "files": ["manual.pdf"],
                            "files_truncated": False,
                        }
                    ],
                    "directories_truncated": False,
                }
            ]
        )
    )

    assert "Root files:" in text
    assert "readme.txt" in text
    assert "📁 public/" in text
    assert "manual.pdf" in text


def test_probe_section_renders_nested_tree_with_mixed_files():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "www",
                    "directories": [
                        {
                            "name": "app",
                            "subdirectories": [
                                "bootstrap",
                                "bootstrap/cache",
                                "config",
                            ],
                            "subdirectories_truncated": False,
                            "files": [
                                "artisan",
                                "bootstrap/cache/packages.php",
                                "config/app.php",
                            ],
                            "files_truncated": False,
                        }
                    ],
                }
            ]
        )
    )

    assert "      📁 app/" in text
    assert "         📁 bootstrap/" in text
    assert "            📁 cache/" in text
    assert "               • packages.php" in text
    assert "         📁 config/" in text
    assert "            • app.php" in text
    assert "         • artisan" in text


def test_probe_section_deduplicates_nested_subdirectories_and_files():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "dup",
                    "directories": [
                        {
                            "name": "public",
                            "subdirectories": [
                                "assets/css",
                                "assets/css",
                                "assets",
                            ],
                            "files": [
                                "assets/css/site.css",
                                "assets/css/site.css",
                            ],
                        }
                    ],
                }
            ]
        )
    )

    assert text.count("📁 assets/") == 1
    assert text.count("📁 css/") == 1
    assert text.count("• site.css") == 1


def test_probe_section_keeps_directory_file_truncation_notices():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "ftp_root",
                    "directories": [
                        {
                            "name": "public",
                            "subdirectories": ["incoming/daily"],
                            "subdirectories_truncated": True,
                            "files": ["incoming/readme.txt"],
                            "files_truncated": True,
                        }
                    ],
                    "directories_truncated": True,
                }
            ]
        )
    )

    assert "additional subdirectories not shown" in text
    assert "additional files not shown" in text
    assert "additional directories not shown" in text


def test_probe_section_shows_root_file_truncation_notice():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[
                {
                    "share": "http_root",
                    "root_files": ["a.txt"],
                    "root_files_truncated": True,
                    "directories": [],
                }
            ]
        )
    )

    assert "additional root files not shown" in text


def test_probe_section_handles_string_and_dict_error_entries():
    details = _import_details_module_isolated()
    text = details._format_probe_section(
        _snapshot(
            shares=[],
            errors=[
                "socket timeout",
                {"share": "http_root", "message": "403 Forbidden"},
            ],
        )
    )

    assert "Unknown share: socket timeout" in text
    assert "http_root: 403 Forbidden" in text


def test_load_probe_config_includes_depth_default_when_settings_missing():
    details = _import_details_module_isolated()

    config = details._load_probe_config(None)

    assert config["max_depth"] == 1


def test_load_probe_config_clamps_depth_to_supported_range():
    details = _import_details_module_isolated()

    class _Settings:
        def __init__(self, values):
            self._values = values

        def get_setting(self, key, default=None):
            return self._values.get(key, default)

    high = details._load_probe_config(_Settings({"probe.max_depth_levels": 99}))
    assert high["max_depth"] == 3

    low = details._load_probe_config(_Settings({"probe.max_depth_levels": 0}))
    assert low["max_depth"] == 1
