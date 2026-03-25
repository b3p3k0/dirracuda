from __future__ import annotations

from pathlib import Path

import shared.path_migration as path_migration


def test_no_legacy_creates_canonical(tmp_path, monkeypatch):
    legacy = tmp_path / ".smbseek"
    canonical = tmp_path / ".dirracuda"
    monkeypatch.setattr(path_migration, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(path_migration, "_CANONICAL_DIR", canonical)

    assert path_migration.migrate_user_data_root() is True
    assert canonical.exists()
    assert not legacy.exists()


def test_migrate_moves_legacy_directory(tmp_path, monkeypatch):
    legacy = tmp_path / ".smbseek"
    canonical = tmp_path / ".dirracuda"
    monkeypatch.setattr(path_migration, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(path_migration, "_CANONICAL_DIR", canonical)

    legacy.mkdir()
    (legacy / "gui_settings.json").write_text('{"ok": true}', encoding="utf-8")
    (legacy / "probes").mkdir()
    (legacy / "probes" / "host.json").write_text("{}", encoding="utf-8")

    assert path_migration.legacy_user_data_needs_migration() is True
    assert path_migration.migrate_user_data_root() is True
    assert not legacy.exists()
    assert (canonical / "gui_settings.json").exists()
    assert (canonical / "probes" / "host.json").exists()


def test_noop_when_canonical_exists(tmp_path, monkeypatch):
    legacy = tmp_path / ".smbseek"
    canonical = tmp_path / ".dirracuda"
    monkeypatch.setattr(path_migration, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(path_migration, "_CANONICAL_DIR", canonical)

    legacy.mkdir()
    (legacy / "keep.txt").write_text("legacy", encoding="utf-8")
    canonical.mkdir()
    (canonical / "existing.txt").write_text("canonical", encoding="utf-8")

    assert path_migration.legacy_user_data_needs_migration() is False
    assert path_migration.migrate_user_data_root() is True
    assert legacy.exists()
    assert (canonical / "existing.txt").read_text(encoding="utf-8") == "canonical"


def test_move_failure_returns_false(tmp_path, monkeypatch):
    legacy = tmp_path / ".smbseek"
    canonical = tmp_path / ".dirracuda"
    monkeypatch.setattr(path_migration, "_LEGACY_DIR", legacy)
    monkeypatch.setattr(path_migration, "_CANONICAL_DIR", canonical)
    legacy.mkdir()

    def _raise_move(_src: str, _dst: str) -> None:
        raise OSError("boom")

    monkeypatch.setattr(path_migration.shutil, "move", _raise_move)
    assert path_migration.migrate_user_data_root() is False
    assert legacy.exists()
    assert not canonical.exists()
