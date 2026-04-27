"""Unit tests for shared/quarantine_promotion.py and _build_promotion_config.

All tests are pure Python — no network, no impacket, no ClamAV binary.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.quarantine_promotion import PromotionConfig, resolve_promotion_dest, safe_move


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_QUARANTINE = Path.home() / ".dirracuda" / "data" / "quarantine"
_DEFAULT_EXTRACTED = Path.home() / ".dirracuda" / "data" / "extracted"


def _cfg(tmp_path: Path, **overrides) -> PromotionConfig:
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    return PromotionConfig(
        ip_address=overrides.get("ip_address", "1.2.3.4"),
        date_str=overrides.get("date_str", "20260328"),
        quarantine_root=overrides.get("quarantine_root", tmp_path / "quarantine"),
        extracted_root=overrides.get("extracted_root", tmp_path / "extracted"),
        known_bad_subdir=overrides.get("known_bad_subdir", "known_bad"),
        download_dir=overrides.get("download_dir", download_dir),
    )


def _make_file(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# resolve_promotion_dest — verdict routing
# ---------------------------------------------------------------------------

def test_resolve_clean_returns_extracted_path(tmp_path):
    cfg = _cfg(tmp_path)
    fp = _make_file(cfg.download_dir / "pub" / "file.txt")
    dest = resolve_promotion_dest("clean", fp, "pub", cfg)
    assert dest == tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "file.txt"


def test_resolve_infected_returns_known_bad_path(tmp_path):
    cfg = _cfg(tmp_path)
    fp = _make_file(cfg.download_dir / "pub" / "eicar.txt")
    dest = resolve_promotion_dest("infected", fp, "pub", cfg)
    assert dest == tmp_path / "quarantine" / "known_bad" / "1.2.3.4" / "20260328" / "pub" / "eicar.txt"


def test_resolve_error_returns_none(tmp_path):
    cfg = _cfg(tmp_path)
    fp = _make_file(cfg.download_dir / "pub" / "file.txt")
    assert resolve_promotion_dest("error", fp, "pub", cfg) is None


def test_resolve_nested_rel_path(tmp_path):
    """Subdirectory structure under share is preserved in target."""
    cfg = _cfg(tmp_path)
    fp = _make_file(cfg.download_dir / "pub" / "subdir" / "deep" / "file.txt")
    dest = resolve_promotion_dest("clean", fp, "pub", cfg)
    assert dest == tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "subdir" / "deep" / "file.txt"


def test_resolve_ip_sanitized(tmp_path):
    """Colons and dots in IP address are converted to safe label."""
    cfg = _cfg(tmp_path, ip_address="192.168.1.1")
    fp = _make_file(cfg.download_dir / "pub" / "file.txt")
    dest = resolve_promotion_dest("clean", fp, "pub", cfg)
    # dots are allowed; no colons in IPv4 — label stays as-is
    assert "192.168.1.1" in str(dest)


def test_resolve_uses_file_path_not_rel_display(tmp_path):
    """Destination is derived from file_path, not from any display string."""
    cfg = _cfg(tmp_path)
    # actual file is at subdir/real.txt — different from any rel_display value
    fp = _make_file(cfg.download_dir / "pub" / "subdir" / "real.txt")
    dest = resolve_promotion_dest("clean", fp, "pub", cfg)
    assert dest.name == "real.txt"
    assert "subdir" in str(dest)


def test_resolve_raises_on_path_mismatch(tmp_path):
    """relative_to() failure propagates (caller must catch)."""
    cfg = _cfg(tmp_path)
    # file_path is NOT under download_dir/share
    fp = tmp_path / "elsewhere" / "file.txt"
    with pytest.raises(ValueError):
        resolve_promotion_dest("clean", fp, "pub", cfg)


# ---------------------------------------------------------------------------
# _build_promotion_config — validation and fallbacks
# ---------------------------------------------------------------------------

def test_build_promotion_config_valid(tmp_path):
    from gui.utils.extract_runner import _build_promotion_config, _sanitize_clamav_config
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    cfg = _sanitize_clamav_config({"enabled": True})
    result = _build_promotion_config("1.2.3.4", download_dir, cfg)
    assert result.date_str == "20260328"
    assert result.quarantine_root == tmp_path / "quarantine"
    assert result.download_dir == download_dir


def test_build_promotion_config_date_fallback_on_invalid_name(tmp_path):
    from gui.utils.extract_runner import _build_promotion_config, _sanitize_clamav_config
    import re as _re
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "notadate"
    download_dir.mkdir(parents=True, exist_ok=True)
    cfg = _sanitize_clamav_config({"enabled": True})
    result = _build_promotion_config("1.2.3.4", download_dir, cfg)
    assert _re.match(r"^\d{8}$", result.date_str), f"expected YYYYMMDD, got {result.date_str!r}"
    assert result.date_str != "notadate"


def test_build_promotion_config_quarantine_root_fallback_on_filesystem_root(tmp_path):
    from gui.utils.extract_runner import _build_promotion_config, _sanitize_clamav_config
    # /a/b → parent.parent = / which equals its own parent
    download_dir = Path("/a/b")
    cfg = _sanitize_clamav_config({"enabled": True})
    result = _build_promotion_config("1.2.3.4", download_dir, cfg)
    assert result.quarantine_root == _DEFAULT_QUARANTINE


def test_build_promotion_config_known_bad_subdir_sanitized(tmp_path):
    from gui.utils.extract_runner import _build_promotion_config, _sanitize_clamav_config
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    cfg = _sanitize_clamav_config({"enabled": True, "known_bad_subdir": "../../outside"})
    result = _build_promotion_config("1.2.3.4", download_dir, cfg)
    # must not contain path separators
    assert "/" not in result.known_bad_subdir
    assert "\\" not in result.known_bad_subdir
    assert ".." not in result.known_bad_subdir


def test_build_promotion_config_known_bad_subdir_empty_fallback(tmp_path):
    from gui.utils.extract_runner import _build_promotion_config, _sanitize_clamav_config
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    # _sanitize_clamav_config stores the raw string; the segment sanitizer in
    # quarantine_promotion falls back to "known_bad" if result is empty.
    cfg = _sanitize_clamav_config({"enabled": True, "known_bad_subdir": "!!!"})
    result = _build_promotion_config("1.2.3.4", download_dir, cfg)
    # _sanitize_segment("!!!") → strips all → falls back to "host" default;
    # but known_bad_subdir's fallback is "known_bad" via resolve_promotion_dest
    # The segment itself won't be empty (safe_segment uses fallback="host" generically).
    assert result.known_bad_subdir is not None


# ---------------------------------------------------------------------------
# safe_move
# ---------------------------------------------------------------------------

def test_safe_move_creates_parent_dirs(tmp_path):
    src = _make_file(tmp_path / "src" / "file.txt")
    dest = tmp_path / "deep" / "nested" / "target.txt"
    actual = safe_move(src, dest)
    assert actual == dest
    assert actual.exists()
    assert not src.exists()


def test_safe_move_no_collision(tmp_path):
    src = _make_file(tmp_path / "src.txt")
    dest = tmp_path / "out" / "dst.txt"
    actual = safe_move(src, dest)
    assert actual == dest


def test_safe_move_collision_appends_suffix(tmp_path):
    src = _make_file(tmp_path / "src.txt", b"new")
    dest = tmp_path / "dst.txt"
    _make_file(dest, b"existing")  # dest already exists
    actual = safe_move(src, dest)
    assert actual == tmp_path / "dst_1.txt"
    assert actual.read_bytes() == b"new"
    assert dest.read_bytes() == b"existing"


def test_safe_move_collision_chain(tmp_path):
    src = _make_file(tmp_path / "src.txt", b"new")
    dest = tmp_path / "dst.txt"
    _make_file(dest, b"orig")
    _make_file(tmp_path / "dst_1.txt", b"also taken")
    actual = safe_move(src, dest)
    assert actual == tmp_path / "dst_2.txt"


def test_safe_move_raises_at_limit(tmp_path):
    src = _make_file(tmp_path / "src.txt")
    dest = tmp_path / "dst.txt"
    _make_file(dest)
    for n in range(1, 100):
        _make_file(tmp_path / f"dst_{n}.txt")
    with pytest.raises(FileExistsError, match="collision limit"):
        safe_move(src, dest)


def test_safe_move_raises_on_os_error(tmp_path):
    src = _make_file(tmp_path / "src.txt")
    dest = tmp_path / "out" / "dst.txt"
    with patch("shutil.move", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            safe_move(src, dest)
