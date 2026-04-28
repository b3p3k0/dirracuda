"""Utilities for deterministic database path resolution.

Contract:
- All relative candidates are resolved against backend_path, never CWD.
- The resolver returns an absolute normalized ``Path``.
- Non-existent persisted paths are allowed when their parent directory exists,
  so first-run/custom targets are preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from shared.path_service import get_legacy_paths, get_paths

CANONICAL_DB_FILENAME = "dirracuda.db"
LEGACY_DB_FILENAME = "smbseek.db"
KNOWN_DB_FILENAMES = (CANONICAL_DB_FILENAME, LEGACY_DB_FILENAME)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalize_backend_path(backend_path: Path | str) -> Path:
    """Return absolute normalized backend path."""
    try:
        return Path(backend_path).expanduser().resolve(strict=False)
    except Exception:
        # Keep fallback deterministic and independent of process CWD.
        return _PROJECT_ROOT


def normalize_database_path(raw_path: Optional[str | Path], backend_path: Path | str) -> Optional[Path]:
    """Resolve raw path to absolute normalized path relative to backend_path."""
    if raw_path is None:
        return None

    value = str(raw_path).strip()
    if not value:
        return None

    backend = normalize_backend_path(backend_path)

    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = backend / candidate
        return candidate.resolve(strict=False)
    except Exception:
        return None


def is_usable_database_path(path: Optional[Path]) -> bool:
    """True if path is usable for DB read/write intent.

    Rules:
    - reject empty/None
    - reject directory targets
    - accept existing files
    - accept non-existent files when parent directory exists
    """
    if path is None:
        return False

    try:
        if path.exists():
            return path.is_file()

        parent = path.parent
        return parent.exists() and parent.is_dir()
    except Exception:
        return False


def _known_legacy_or_repo_db_targets(*, backend: Path) -> set[Path]:
    """Return known legacy/repo-local DB targets that may drift back into config."""
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)

    known = {
        legacy.flat_main_db_file.resolve(strict=False),
        legacy.flat_legacy_db_file.resolve(strict=False),
        (legacy.legacy_home_root / CANONICAL_DB_FILENAME).resolve(strict=False),
        (legacy.legacy_home_root / LEGACY_DB_FILENAME).resolve(strict=False),
        legacy.repo_main_db_file.resolve(strict=False),
        legacy.repo_legacy_db_file.resolve(strict=False),
        (backend / CANONICAL_DB_FILENAME).resolve(strict=False),
        (backend / LEGACY_DB_FILENAME).resolve(strict=False),
    }

    # Canonical home DB remains strict even when missing.
    known.discard(paths.main_db_file.resolve(strict=False))
    return known


def _is_stale_known_legacy_persisted_path(
    candidate: Optional[Path],
    *,
    known_legacy_targets: set[Path],
) -> bool:
    """True when a persisted path is a missing known legacy/repo target."""
    if candidate is None or candidate not in known_legacy_targets:
        return False

    try:
        return not candidate.exists()
    except Exception:
        return False


def auto_detect_database_path(backend_path: Path | str) -> Path:
    """Detect existing DB path with home-first precedence.

    Order:
    1) canonical home data DB (`~/.dirracuda/data/dirracuda.db`)
    2) legacy home DBs (`~/.dirracuda/{dirracuda,smbseek}.db`, `~/.smbseek/{...}.db`)
    3) backend-local DBs (`<backend>/{dirracuda,smbseek}.db`)
    4) canonical home data DB default
    """
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)

    home_candidates = (
        paths.main_db_file,
        legacy.flat_main_db_file,
        legacy.flat_legacy_db_file,
        legacy.legacy_home_root / CANONICAL_DB_FILENAME,
        legacy.legacy_home_root / LEGACY_DB_FILENAME,
    )

    for candidate in home_candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=False)
            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            continue

    backend = normalize_backend_path(backend_path)

    for filename in KNOWN_DB_FILENAMES:
        candidate = (backend / filename).resolve(strict=False)
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue

    return paths.main_db_file.resolve(strict=False)


def resolve_database_path(
    backend_path: Path | str,
    cli_database_path: Optional[str | Path] = None,
    persisted_paths: Optional[Iterable[Optional[str | Path]]] = None,
) -> Path:
    """Resolve effective database path using deterministic precedence.

    Order:
    1) explicit CLI path
    2) persisted paths in provided order
    3) home/backend auto-detect (home canonical first, then legacy/backend)
    4) default ~/.dirracuda/data/dirracuda.db
    """
    backend = normalize_backend_path(backend_path)
    known_legacy_targets = _known_legacy_or_repo_db_targets(backend=backend)

    cli_candidate = normalize_database_path(cli_database_path, backend)
    if is_usable_database_path(cli_candidate):
        return cli_candidate

    for raw in persisted_paths or ():
        candidate = normalize_database_path(raw, backend)
        if _is_stale_known_legacy_persisted_path(
            candidate,
            known_legacy_targets=known_legacy_targets,
        ):
            continue
        if is_usable_database_path(candidate):
            return candidate

    return auto_detect_database_path(backend)
