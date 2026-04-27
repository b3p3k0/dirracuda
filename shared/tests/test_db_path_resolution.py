from pathlib import Path

from shared.db_path_resolution import (
    CANONICAL_DB_FILENAME,
    LEGACY_DB_FILENAME,
    resolve_database_path,
)
from shared.path_service import get_paths


def test_new_install_defaults_to_canonical_filename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[],
    )
    assert resolved == (tmp_path / ".dirracuda" / "data" / "dirracuda.db").resolve(strict=False)


def test_legacy_only_autodetect_picks_smbseek_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / "smbseek.db"
    legacy.touch()

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[],
    )
    assert resolved == legacy.resolve(strict=False)


def test_both_files_autodetect_prefers_dirracuda_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / "smbseek.db"
    canonical = tmp_path / "dirracuda.db"
    legacy.touch()
    canonical.touch()

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[],
    )
    assert resolved == canonical.resolve(strict=False)


def test_cli_override_wins_over_persisted_candidates(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "dirracuda.db").touch()
    (tmp_path / "smbseek.db").touch()

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path="custom.db",
        persisted_paths=["smbseek.db", "dirracuda.db"],
    )
    assert resolved == (tmp_path / "custom.db").resolve(strict=False)


def test_relative_persisted_path_is_backend_rooted_exact(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=["nested/my.db"],
    )
    assert resolved == (tmp_path / "nested" / "my.db").resolve(strict=False)


def test_stale_persisted_missing_parent_falls_through_to_autodetect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    canonical = tmp_path / "dirracuda.db"
    canonical.touch()
    stale = tmp_path / "missing_parent" / "stale.db"

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[str(stale)],
    )
    assert resolved == canonical.resolve(strict=False)


def test_persisted_canonical_missing_stays_strict_even_when_legacy_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    persisted_canonical = tmp_path / ".dirracuda" / "data" / "dirracuda.db"
    persisted_canonical.parent.mkdir(parents=True, exist_ok=True)
    legacy_existing = tmp_path / ".dirracuda" / "dirracuda.db"
    legacy_existing.parent.mkdir(parents=True, exist_ok=True)
    legacy_existing.touch()

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[str(persisted_canonical)],
    )
    assert resolved == persisted_canonical.resolve(strict=False)
    assert not resolved.exists()
    assert legacy_existing.exists()


def test_missing_persisted_legacy_target_falls_through_to_canonical(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    canonical = tmp_path / ".dirracuda" / "data" / "dirracuda.db"
    legacy = tmp_path / ".dirracuda" / "dirracuda.db"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    legacy.parent.mkdir(parents=True, exist_ok=True)

    resolved = resolve_database_path(
        backend_path=tmp_path,
        cli_database_path=None,
        persisted_paths=[str(legacy)],
    )
    assert resolved == canonical.resolve(strict=False)


def test_missing_explicit_custom_path_remains_strict(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    custom = tmp_path / "custom" / "my.db"
    custom.parent.mkdir(parents=True, exist_ok=True)

    resolved = resolve_database_path(
        backend_path=backend,
        cli_database_path=None,
        persisted_paths=[str(custom)],
    )
    assert resolved == custom.resolve(strict=False)


def test_invalid_backend_path_does_not_fallback_to_cwd() -> None:
    resolved = resolve_database_path(
        backend_path=None,  # type: ignore[arg-type]
        cli_database_path=None,
        persisted_paths=[],
    )
    assert resolved == get_paths().main_db_file.resolve(strict=False)
    assert resolved.name in {CANONICAL_DB_FILENAME, LEGACY_DB_FILENAME}
