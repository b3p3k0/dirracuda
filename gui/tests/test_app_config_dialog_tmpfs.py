"""Tests for tmpfs quarantine config behavior in AppConfigDialog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.app_config_dialog import AppConfigDialog


class _BoolVar:
    def __init__(self, value: bool) -> None:
        self._value = value

    def get(self) -> bool:
        return self._value


class _StringVar:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _Widget:
    def __init__(self) -> None:
        self.state = None
        self.text = ""

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]
        if "text" in kwargs:
            self.text = kwargs["text"]


class _Dialog:
    def __init__(self) -> None:
        self.exists = True

    def winfo_exists(self) -> int:
        return 1 if self.exists else 0


def _bare_dialog() -> AppConfigDialog:
    dlg = AppConfigDialog.__new__(AppConfigDialog)
    dlg.parent = object()
    dlg.dialog = _Dialog()
    dlg.settings_manager = None
    dlg.config_editor_callback = None
    dlg.main_config = None
    dlg.refresh_callback = None

    dlg.api_key = ""
    dlg.wordlist_path = ""
    dlg.quarantine_path = "~/.dirracuda/data/quarantine"

    dlg.quarantine_tmpfs_enabled = False
    dlg.quarantine_tmpfs_size_mb = 512
    dlg._tmpfs_supported_platform = True

    dlg.quarantine_tmpfs_enabled_var = None
    dlg.quarantine_tmpfs_size_var = None
    dlg.quarantine_entry_widget = None
    dlg.quarantine_browse_button = None
    dlg.quarantine_tmpfs_size_entry = None
    dlg.quarantine_tmpfs_note_label = None

    return dlg


def test_load_runtime_settings_reads_tmpfs_config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "quarantine": {
                    "use_tmpfs": True,
                    "tmpfs_size_mb": 1024,
                },
                "file_browser": {
                    "quarantine_root": "~/.dirracuda/data/quarantine",
                },
            }
        ),
        encoding="utf-8",
    )

    dlg = _bare_dialog()
    dlg._load_runtime_settings_from_config(str(cfg))

    assert dlg.quarantine_tmpfs_enabled is True
    assert dlg.quarantine_tmpfs_size_mb == 1024


def test_apply_runtime_settings_writes_tmpfs_keys():
    dlg = _bare_dialog()
    out = {}

    dlg._apply_runtime_settings(
        out,
        api_key="",
        quarantine_path="~/.dirracuda/data/quarantine",
        wordlist_path="",
        clamav_settings=None,
        quarantine_tmpfs_settings={"use_tmpfs": True},
    )

    assert out["quarantine"]["use_tmpfs"] is True
    assert "tmpfs_size_mb" not in out["quarantine"]


def test_sync_quarantine_controls_disables_path_widgets_when_tmpfs_enabled():
    dlg = _bare_dialog()
    dlg._tmpfs_supported_platform = True
    dlg.quarantine_tmpfs_enabled_var = _BoolVar(True)
    dlg.quarantine_entry_widget = _Widget()
    dlg.quarantine_browse_button = _Widget()
    dlg.quarantine_tmpfs_note_label = _Widget()

    dlg._sync_quarantine_controls_for_tmpfs()

    assert dlg.quarantine_entry_widget.state == "disabled"
    assert dlg.quarantine_browse_button.state == "disabled"
    assert "pre-mount" in dlg.quarantine_tmpfs_note_label.text.lower()


def test_sync_quarantine_controls_non_linux_forces_disabled_tmpfs_controls():
    dlg = _bare_dialog()
    dlg._tmpfs_supported_platform = False
    dlg.quarantine_tmpfs_enabled_var = _BoolVar(True)
    dlg.quarantine_entry_widget = _Widget()
    dlg.quarantine_browse_button = _Widget()
    dlg.quarantine_tmpfs_note_label = _Widget()

    dlg._sync_quarantine_controls_for_tmpfs()

    assert dlg.quarantine_entry_widget.state == "normal"
    assert dlg.quarantine_browse_button.state == "normal"
    assert "linux only" in dlg.quarantine_tmpfs_note_label.text.lower()
