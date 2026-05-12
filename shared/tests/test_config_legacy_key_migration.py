from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.config import SMBSeekConfig


def _write_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_legacy_keys_are_removed_and_backup_is_created(tmp_path, caplog) -> None:
    config_file = tmp_path / "config.json"
    payload = {
        "database": {"path": str(tmp_path / "dirracuda.db")},
        "pry": {"wordlist_path": "words.txt"},
        "rce": {"enabled_default": False},
    }
    _write_config(config_file, payload)

    with caplog.at_level(logging.WARNING):
        cfg = SMBSeekConfig(config_file=str(config_file))

    assert "pry" not in cfg.config
    assert "rce" not in cfg.config

    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert "pry" not in persisted
    assert "rce" not in persisted

    backups = sorted(tmp_path.glob("config.json.legacy_keys_backup_*"))
    assert len(backups) == 1
    backup_data = json.loads(backups[0].read_text(encoding="utf-8"))
    assert "pry" in backup_data
    assert "rce" in backup_data
    assert "Legacy config keys removed from" in caplog.text


def test_config_without_legacy_keys_is_not_rewritten(tmp_path) -> None:
    config_file = tmp_path / "config.json"
    payload = {"database": {"path": str(tmp_path / "dirracuda.db")}}
    _write_config(config_file, payload)
    before = config_file.read_text(encoding="utf-8")

    cfg = SMBSeekConfig(config_file=str(config_file))

    assert "pry" not in cfg.config
    assert "rce" not in cfg.config
    assert config_file.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("config.json.legacy_keys_backup_*")) == []


def test_legacy_key_rewrite_failure_uses_sanitized_in_memory_config(tmp_path, monkeypatch, caplog) -> None:
    config_file = tmp_path / "config.json"
    payload = {
        "database": {"path": str(tmp_path / "dirracuda.db")},
        "pry": {"wordlist_path": "words.txt"},
        "rce": {"enabled_default": False},
    }
    _write_config(config_file, payload)

    def _force_failure(self, _sanitized):
        return None, "permission denied"

    monkeypatch.setattr(SMBSeekConfig, "_rewrite_config_without_legacy_keys", _force_failure)

    with caplog.at_level(logging.WARNING):
        cfg = SMBSeekConfig(config_file=str(config_file))

    assert "pry" not in cfg.config
    assert "rce" not in cfg.config
    assert "Failed to rewrite config file" in caplog.text

    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert "pry" in persisted
    assert "rce" in persisted
