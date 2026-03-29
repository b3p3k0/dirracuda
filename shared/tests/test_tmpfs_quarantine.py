"""Unit tests for shared/tmpfs_quarantine.py."""

from __future__ import annotations

from pathlib import Path

import shared.tmpfs_quarantine as tq


def _cfg(*, use_tmpfs: bool, size_mb: int = 512, disk_root: str = "~/.dirracuda/quarantine"):
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
    assert "Linux" in (state["fallback_reason"] or "")

    warning = tq.consume_tmpfs_startup_warning()
    assert warning is not None
    assert "disk quarantine" in warning
    assert tq.consume_tmpfs_startup_warning() is None


def test_bootstrap_mount_success_uses_tmpfs_root(monkeypatch):
    calls = []

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: False)
    monkeypatch.setattr(tq, "_is_any_mount", lambda _p: False)

    def _run_mount(mountpoint: Path, size_mb: int, *, with_noswap: bool):
        calls.append((mountpoint, size_mb, with_noswap))
        return True, ""

    monkeypatch.setattr(tq, "_run_mount_tmpfs", _run_mount)
    monkeypatch.setattr(tq, "_kernel_supports_noswap", lambda: False)

    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True, size_mb=900))

    assert state["tmpfs_active"] is True
    assert state["mounted_by_app"] is True
    assert state["effective_root"].endswith(".dirracuda/quarantine_tmpfs")
    assert calls and calls[0][1] == 900
    assert calls[0][2] is False


def test_bootstrap_mount_failure_falls_back_to_disk(monkeypatch):
    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: False)
    monkeypatch.setattr(tq, "_is_any_mount", lambda _p: False)
    monkeypatch.setattr(tq, "_run_mount_tmpfs", lambda *_a, **_kw: (False, "permission denied"))
    monkeypatch.setattr(tq, "_kernel_supports_noswap", lambda: True)

    state = tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True, disk_root="/tmp/disk_q"))

    assert state["use_tmpfs"] is True
    assert state["tmpfs_active"] is False
    assert state["effective_root"] == "/tmp/disk_q"
    assert "permission denied" in (state["fallback_reason"] or "")


def test_noswap_branch_passes_true_when_supported(monkeypatch):
    calls = []

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: False)
    monkeypatch.setattr(tq, "_is_any_mount", lambda _p: False)
    monkeypatch.setattr(tq, "_kernel_supports_noswap", lambda: True)

    def _run_mount(_mountpoint: Path, _size_mb: int, *, with_noswap: bool):
        calls.append(with_noswap)
        return True, ""

    monkeypatch.setattr(tq, "_run_mount_tmpfs", _run_mount)
    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    assert calls == [True]


def test_cleanup_unmounts_only_when_app_mounted(monkeypatch, tmp_path):
    mountpoint = tmp_path / "qtmpfs"
    mountpoint.mkdir(parents=True, exist_ok=True)
    (mountpoint / "1.2.3.4").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: False)
    monkeypatch.setattr(tq, "_is_any_mount", lambda _p: False)
    monkeypatch.setattr(tq, "_run_mount_tmpfs", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(tq, "_TMPFS_DEFAULT", mountpoint)

    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))

    umount_calls = []
    monkeypatch.setattr(tq, "_run_umount", lambda _mp: (umount_calls.append(str(_mp)) or True, ""))

    result = tq.cleanup_tmpfs_quarantine()
    assert result["ok"] is True
    assert umount_calls == [str(mountpoint)]

    # External mount path: app should not unmount.
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: True)
    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))
    umount_calls.clear()
    result2 = tq.cleanup_tmpfs_quarantine()
    assert result2["ok"] is True
    assert umount_calls == []


def test_tmpfs_has_quarantined_files_ignores_readme(monkeypatch, tmp_path):
    root = tmp_path / "tmpfs_q"
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.txt").write_text("marker", encoding="utf-8")

    monkeypatch.setattr(tq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tq, "_is_tmpfs_mounted", lambda _p: True)
    monkeypatch.setattr(tq, "_TMPFS_DEFAULT", root)

    tq.bootstrap_tmpfs_quarantine(config_data=_cfg(use_tmpfs=True))
    assert tq.tmpfs_has_quarantined_files() is False

    (root / "host").mkdir(parents=True, exist_ok=True)
    assert tq.tmpfs_has_quarantined_files() is True
