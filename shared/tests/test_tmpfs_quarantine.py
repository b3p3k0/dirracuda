"""Unit tests for shared/tmpfs_quarantine.py."""

from __future__ import annotations

from pathlib import Path

import shared.tmpfs_quarantine as tq


def _cfg(*, use_tmpfs: bool, size_mb: int = 512, disk_root: str = "~/.dirracuda/data/quarantine"):
    return {
        "quarantine": {
            "use_tmpfs": use_tmpfs,
            "tmpfs_size_mb": size_mb,
        },
        "file_browser": {
            "quarantine_root": disk_root,
        },
    }


def test_bootstrap_disabled_preserves_explicit_base_path(monkeypatch, tmp_path):
    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=False))

    assert state["use_tmpfs"] is False
    assert state["tmpfs_active"] is False

    custom = tmp_path / "custom"
    resolved = tq.resolve_effective_quarantine_root(custom)
    assert resolved == custom


def test_bootstrap_non_linux_falls_back_with_warning(monkeypatch):
    monkeypatch.setattr(tq.platform, "system", lambda: "Darwin")
    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    assert state["use_tmpfs"] is True
    assert state["platform_supported"] is False
    assert state["tmpfs_active"] is False
    assert state["warning_code"] == "non_linux"

    warning = tq.consume_tmpfs_startup_warning()
    assert warning is not None
    assert "disk quarantine" in warning
    assert tq.consume_tmpfs_startup_warning() is None


def test_bootstrap_uses_canonical_mountpoint_when_present(monkeypatch):
    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")

    def _mounted(path: Path) -> bool:
        return path == tq._TMPFS_CANONICAL

    monkeypatch.setattr(tq, "_is_tmpfs_mounted", _mounted)

    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    assert state["tmpfs_active"] is True
    assert state["legacy_mount_in_use"] is False
    assert state["warning_code"] is None
    assert state["effective_root"] == str(tq._TMPFS_CANONICAL)


def test_bootstrap_uses_legacy_mountpoint_with_warning(monkeypatch):
    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")

    def _mounted(path: Path) -> bool:
        return path == tq._TMPFS_LEGACY_DIRRACUDA

    monkeypatch.setattr(tq, "_is_tmpfs_mounted", _mounted)

    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    assert state["tmpfs_active"] is True
    assert state["legacy_mount_in_use"] is True
    assert state["warning_code"] == "legacy_mountpoint"
    assert state["effective_root"] == str(tq._TMPFS_LEGACY_DIRRACUDA)

    warning = tq.consume_tmpfs_startup_warning()
    assert warning is not None
    assert str(tq._TMPFS_CANONICAL) in warning
    assert "/etc/fstab" in warning


def test_bootstrap_without_mount_falls_back_to_disk(monkeypatch):
    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: False)

    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True, disk_root="/tmp/disk_q"))

    assert state["use_tmpfs"] is True
    assert state["tmpfs_active"] is False
    assert state["warning_code"] == "mount_not_found"
    assert state["effective_root"] == "/tmp/disk_q"
    assert "does not mount tmpfs automatically" in (state["warning_message"] or "")

    # Guardrail: detect-only runtime should not expose mount helper anymore.
    assert not hasattr(tq, "_run_mount_tmpfs")


def test_cleanup_never_attempts_umount(monkeypatch, tmp_path):
    root = tmp_path / "qtmpfs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "host").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda p: p == root)
    monkeypatch.setattr(tq, "_TMPFS_CANONICAL", root)
    monkeypatch.setattr(tq, "_TMPFS_CANDIDATES", (("canonical", root),))

    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    result = tq.cleanup_tmpfs_quarantine()
    assert result["ok"] is True
    assert "no unmount attempted" in result["message"]


def test_tmpfs_has_quarantined_files_ignores_readme(monkeypatch, tmp_path):
    root = tmp_path / "tmpfs_q"
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.txt").write_text("marker", encoding="utf-8")

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda p: p == root)
    monkeypatch.setattr(tq, "_TMPFS_CANONICAL", root)
    monkeypatch.setattr(tq, "_TMPFS_CANDIDATES", (("canonical", root),))

    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))
    assert tq.tmpfs_has_quarantined_files() is False

    (root / "host").mkdir(parents=True, exist_ok=True)
    assert tq.tmpfs_has_quarantined_files() is True
