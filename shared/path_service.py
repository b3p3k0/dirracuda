"""Canonical user-data path and migration service for Dirracuda Layout v2.

Layout v2 keeps runtime artifacts under ~/.dirracuda with strict top-level
separation:
- conf/
- data/
- state/
- logs/

This module also provides one-time migration helpers from legacy layouts:
- ~/.smbseek
- flat ~/.dirracuda paths from pre-v2 releases
- checkout-local conf/config.json and dirracuda.db
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


LAYOUT_VERSION = 2
HOME_DIRNAME = ".dirracuda"


@dataclass(frozen=True)
class DirracudaPaths:
    home_root: Path
    repo_root: Path

    conf_dir: Path
    config_file: Path
    exclusion_list_file: Path
    ransomware_indicators_file: Path
    signatures_dir: Path
    signatures_rce_dir: Path
    wordlists_dir: Path

    data_dir: Path
    main_db_file: Path
    experimental_dir: Path
    se_dork_db_file: Path
    reddit_od_db_file: Path
    dorkbook_db_file: Path
    keymaster_db_file: Path
    quarantine_dir: Path
    extracted_dir: Path
    tmpfs_quarantine_dir: Path
    cache_dir: Path
    cache_probe_dir: Path
    cache_probe_smb_dir: Path
    cache_probe_ftp_dir: Path
    cache_probe_http_dir: Path

    state_dir: Path
    gui_settings_file: Path
    templates_dir: Path
    templates_scan_dir: Path
    templates_filter_dir: Path
    migrations_dir: Path
    migration_state_file: Path
    migration_reports_dir: Path
    migration_backups_dir: Path

    logs_dir: Path
    rce_analysis_log_file: Path
    extract_logs_dir: Path
    app_logs_dir: Path


@dataclass(frozen=True)
class LegacyPaths:
    legacy_home_root: Path  # ~/.smbseek
    flat_home_root: Path    # ~/.dirracuda (pre-v2 flat paths)

    flat_gui_settings_file: Path
    flat_scan_templates_dir: Path
    flat_filter_templates_dir: Path
    flat_config_file: Path
    flat_exclusion_list_file: Path
    flat_ransomware_indicators_file: Path

    flat_main_db_file: Path
    flat_legacy_db_file: Path

    flat_sidecar_se_dork_file: Path
    flat_sidecar_reddit_od_file: Path
    flat_sidecar_dorkbook_file: Path
    flat_sidecar_keymaster_file: Path

    flat_probe_smb_dir: Path
    flat_probe_ftp_dir: Path
    flat_probe_http_dir: Path

    flat_quarantine_dir: Path
    flat_extracted_dir: Path
    flat_tmpfs_quarantine_dir: Path

    flat_extract_logs_dir: Path
    flat_rce_analysis_log_file: Path

    repo_config_file: Path
    repo_config_example_file: Path
    repo_exclusion_list_file: Path
    repo_ransomware_indicators_file: Path
    repo_signatures_dir: Path
    repo_wordlists_dir: Path

    repo_main_db_file: Path
    repo_legacy_db_file: Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_paths(*, home_root: Optional[Path] = None, repo_root: Optional[Path] = None) -> DirracudaPaths:
    repo = (repo_root or get_repo_root()).expanduser().resolve(strict=False)
    home = (home_root or (Path.home() / HOME_DIRNAME)).expanduser().resolve(strict=False)

    conf_dir = home / "conf"
    data_dir = home / "data"
    state_dir = home / "state"
    logs_dir = home / "logs"

    experimental_dir = data_dir / "experimental"
    cache_dir = data_dir / "cache"
    cache_probe_dir = cache_dir / "probes"

    templates_dir = state_dir / "templates"
    migrations_dir = state_dir / "migrations"

    return DirracudaPaths(
        home_root=home,
        repo_root=repo,
        conf_dir=conf_dir,
        config_file=conf_dir / "config.json",
        exclusion_list_file=conf_dir / "exclusion_list.json",
        ransomware_indicators_file=conf_dir / "ransomware_indicators.json",
        signatures_dir=conf_dir / "signatures",
        signatures_rce_dir=conf_dir / "signatures" / "rce_smb",
        wordlists_dir=conf_dir / "wordlists",
        data_dir=data_dir,
        main_db_file=data_dir / "dirracuda.db",
        experimental_dir=experimental_dir,
        se_dork_db_file=experimental_dir / "se_dork.db",
        reddit_od_db_file=experimental_dir / "reddit_od.db",
        dorkbook_db_file=experimental_dir / "dorkbook.db",
        keymaster_db_file=experimental_dir / "keymaster.db",
        quarantine_dir=data_dir / "quarantine",
        extracted_dir=data_dir / "extracted",
        tmpfs_quarantine_dir=data_dir / "tmpfs_quarantine",
        cache_dir=cache_dir,
        cache_probe_dir=cache_probe_dir,
        cache_probe_smb_dir=cache_probe_dir / "smb",
        cache_probe_ftp_dir=cache_probe_dir / "ftp",
        cache_probe_http_dir=cache_probe_dir / "http",
        state_dir=state_dir,
        gui_settings_file=state_dir / "gui_settings.json",
        templates_dir=templates_dir,
        templates_scan_dir=templates_dir / "scan",
        templates_filter_dir=templates_dir / "filter",
        migrations_dir=migrations_dir,
        migration_state_file=migrations_dir / "state.json",
        migration_reports_dir=migrations_dir / "reports",
        migration_backups_dir=migrations_dir / "backups",
        logs_dir=logs_dir,
        rce_analysis_log_file=logs_dir / "rce_analysis.jsonl",
        extract_logs_dir=logs_dir / "extract",
        app_logs_dir=logs_dir / "app",
    )


def get_legacy_paths(*, paths: Optional[DirracudaPaths] = None) -> LegacyPaths:
    p = paths or get_paths()
    flat_root = p.home_root
    legacy_root = p.home_root.parent / ".smbseek"

    repo_conf = p.repo_root / "conf"

    return LegacyPaths(
        legacy_home_root=legacy_root,
        flat_home_root=flat_root,
        flat_gui_settings_file=flat_root / "gui_settings.json",
        flat_scan_templates_dir=flat_root / "templates",
        flat_filter_templates_dir=flat_root / "filter_templates",
        flat_config_file=flat_root / "config.json",
        flat_exclusion_list_file=flat_root / "exclusion_list.json",
        flat_ransomware_indicators_file=flat_root / "ransomware_indicators.json",
        flat_main_db_file=flat_root / "dirracuda.db",
        flat_legacy_db_file=flat_root / "smbseek.db",
        flat_sidecar_se_dork_file=flat_root / "se_dork.db",
        flat_sidecar_reddit_od_file=flat_root / "reddit_od.db",
        flat_sidecar_dorkbook_file=flat_root / "dorkbook.db",
        flat_sidecar_keymaster_file=flat_root / "keymaster.db",
        flat_probe_smb_dir=flat_root / "probes",
        flat_probe_ftp_dir=flat_root / "ftp_probes",
        flat_probe_http_dir=flat_root / "http_probes",
        flat_quarantine_dir=flat_root / "quarantine",
        flat_extracted_dir=flat_root / "extracted",
        flat_tmpfs_quarantine_dir=flat_root / "quarantine_tmpfs",
        flat_extract_logs_dir=flat_root / "extract_logs",
        flat_rce_analysis_log_file=flat_root / "rce_analysis.jsonl",
        repo_config_file=repo_conf / "config.json",
        repo_config_example_file=repo_conf / "config.json.example",
        repo_exclusion_list_file=repo_conf / "exclusion_list.json",
        repo_ransomware_indicators_file=repo_conf / "ransomware_indicators.json",
        repo_signatures_dir=repo_conf / "signatures",
        repo_wordlists_dir=repo_conf / "wordlists",
        repo_main_db_file=p.repo_root / "dirracuda.db",
        repo_legacy_db_file=p.repo_root / "smbseek.db",
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _safe_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_layout_state(*, paths: Optional[DirracudaPaths] = None) -> Dict[str, Any]:
    p = paths or get_paths()
    if not p.migration_state_file.exists():
        return {}
    return _safe_read_json(p.migration_state_file)


def write_layout_state(payload: Dict[str, Any], *, paths: Optional[DirracudaPaths] = None) -> None:
    p = paths or get_paths()
    _safe_write_json(p.migration_state_file, payload)


def is_layout_v2_complete(*, paths: Optional[DirracudaPaths] = None) -> bool:
    state = read_layout_state(paths=paths)
    return int(state.get("layout_version", 0) or 0) >= LAYOUT_VERSION and state.get("status") == "success"


def select_existing_path(canonical: Path, legacy_candidates: Iterable[Path]) -> Path:
    if canonical.exists():
        return canonical
    for candidate in legacy_candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return canonical


def resolve_runtime_config_path(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Path:
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)
    return select_existing_path(
        p.config_file,
        [
            l.flat_home_root / "conf" / "config.json",
            l.repo_config_file,
        ],
    )


def resolve_runtime_main_db_path(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Path:
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)
    return select_existing_path(
        p.main_db_file,
        [
            l.flat_main_db_file,
            l.flat_legacy_db_file,
            l.repo_main_db_file,
            l.repo_legacy_db_file,
        ],
    )


def get_runtime_main_db_fallback_candidates(
    *,
    paths: Optional[DirracudaPaths] = None,
    legacy: Optional[LegacyPaths] = None,
) -> List[Path]:
    """Return ordered legacy DB candidates for session-only fallback/recovery."""
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)
    raw_candidates = [
        l.flat_main_db_file,
        l.flat_legacy_db_file,
        l.legacy_home_root / "dirracuda.db",
        l.legacy_home_root / "smbseek.db",
        l.repo_main_db_file,
        l.repo_legacy_db_file,
    ]
    ordered: List[Path] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(resolved)
    return ordered


def resolve_runtime_main_db_for_session(
    preferred_path: Path | str,
    *,
    migration_result: Optional[Dict[str, Any]] = None,
    paths: Optional[DirracudaPaths] = None,
    legacy: Optional[LegacyPaths] = None,
) -> Tuple[Path, Optional[str]]:
    """Resolve effective DB path for current session with migration-aware fallback.

    Strict persisted-path precedence remains unchanged; this helper only applies when
    preferred DB path is missing and migration reports DB recovery issues.
    """
    preferred = Path(preferred_path).expanduser().resolve(strict=False)
    try:
        if preferred.exists() and preferred.is_file():
            return preferred, None
    except Exception:
        pass

    result = migration_result if isinstance(migration_result, dict) else {}
    recovery_status = str(result.get("db_recovery_status", "")).strip().lower()
    migration_status = str(result.get("status", "")).strip().lower()
    recovery_attempted = bool(result.get("db_recovery_attempted"))
    recovery_incomplete = recovery_status in {"partial", "failed"}
    migration_incomplete = migration_status in {"partial", "failed"}

    if not recovery_incomplete and not migration_incomplete and not (recovery_attempted and migration_incomplete):
        return preferred, None

    candidates: List[Path] = []
    raw_candidates = result.get("db_fallback_candidates")
    if isinstance(raw_candidates, list):
        for raw in raw_candidates:
            try:
                candidates.append(Path(str(raw)).expanduser().resolve(strict=False))
            except Exception:
                continue
    if not candidates:
        candidates = get_runtime_main_db_fallback_candidates(paths=paths, legacy=legacy)

    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                warning = (
                    "Database migration is incomplete; using legacy database for this session: "
                    f"{candidate}\nDirracuda will retry migration on next startup."
                )
                return candidate, warning
        except Exception:
            continue

    return preferred, None


def resolve_runtime_gui_settings_path(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Path:
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)
    return select_existing_path(
        p.gui_settings_file,
        [
            l.flat_gui_settings_file,
            l.legacy_home_root / "gui_settings.json",
        ],
    )


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _copy_asset_if_missing(src: Path, dst: Path) -> Dict[str, Any]:
    item = {
        "source": str(src),
        "target": str(dst),
        "action": "copy_if_missing",
        "status": "skipped",
        "detail": "source_missing",
    }
    if not src.exists():
        return item

    if dst.exists():
        item["detail"] = "target_exists"
        return item

    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

    item["status"] = "ok"
    item["detail"] = "copied"
    return item


def _read_json_object(path: Path) -> Dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("json_root_not_object")
    return raw


def _json_get(data: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _json_set(data: Dict[str, Any], keys: Tuple[str, ...], value: Any) -> None:
    cur: Dict[str, Any] = data
    for key in keys[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[keys[-1]] = value


def _resolve_path_like(raw: Any, *, base: Path) -> Optional[Path]:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        return candidate.resolve(strict=False)
    except Exception:
        return None


def _known_legacy_db_targets(*, paths: DirracudaPaths, legacy: LegacyPaths, backend: Path) -> set[Path]:
    known = {
        legacy.flat_main_db_file.resolve(strict=False),
        legacy.flat_legacy_db_file.resolve(strict=False),
        (legacy.legacy_home_root / "dirracuda.db").resolve(strict=False),
        (legacy.legacy_home_root / "smbseek.db").resolve(strict=False),
        legacy.repo_main_db_file.resolve(strict=False),
        legacy.repo_legacy_db_file.resolve(strict=False),
        (backend / "dirracuda.db").resolve(strict=False),
        (backend / "smbseek.db").resolve(strict=False),
    }
    known.discard(paths.main_db_file.resolve(strict=False))
    return known


def _known_legacy_config_targets(*, paths: DirracudaPaths, legacy: LegacyPaths, backend: Path) -> set[Path]:
    known = {
        legacy.flat_config_file.resolve(strict=False),
        (legacy.flat_home_root / "conf" / "config.json").resolve(strict=False),
        (legacy.legacy_home_root / "config.json").resolve(strict=False),
        (legacy.legacy_home_root / "conf" / "config.json").resolve(strict=False),
        legacy.repo_config_file.resolve(strict=False),
        (backend / "conf" / "config.json").resolve(strict=False),
    }
    known.discard(paths.config_file.resolve(strict=False))
    return known


def _sanitize_db_field(
    data: Dict[str, Any],
    *,
    scope: str,
    keys: Tuple[str, ...],
    backend: Path,
    canonical_db: Path,
    known_legacy_db_targets: set[Path],
) -> Tuple[Dict[str, Any], bool]:
    field = ".".join(keys)
    raw = _json_get(data, keys)
    item: Dict[str, Any] = {
        "scope": scope,
        "field": field,
        "source": raw,
        "status": "skipped",
        "detail": "value_missing",
    }

    resolved = _resolve_path_like(raw, base=backend)
    if resolved is None:
        return item, False
    item["resolved"] = str(resolved)

    if resolved == canonical_db:
        item["detail"] = "already_canonical"
        return item, False

    try:
        exists = resolved.exists()
    except Exception:
        exists = False

    if resolved in known_legacy_db_targets and not exists:
        _json_set(data, keys, str(canonical_db))
        item["status"] = "ok"
        item["detail"] = "stale_legacy_path_reset_to_canonical"
        item["target"] = str(canonical_db)
        return item, True

    item["detail"] = "preserved_explicit_or_existing"
    return item, False


def _sanitize_config_field(
    data: Dict[str, Any],
    *,
    scope: str,
    keys: Tuple[str, ...],
    backend: Path,
    canonical_config: Path,
    known_legacy_config_targets: set[Path],
) -> Tuple[Dict[str, Any], bool]:
    field = ".".join(keys)
    raw = _json_get(data, keys)
    item: Dict[str, Any] = {
        "scope": scope,
        "field": field,
        "source": raw,
        "status": "skipped",
        "detail": "value_missing",
    }

    resolved = _resolve_path_like(raw, base=backend)
    if resolved is None:
        return item, False
    item["resolved"] = str(resolved)

    if resolved == canonical_config:
        item["detail"] = "already_canonical"
        return item, False

    try:
        exists = resolved.exists()
    except Exception:
        exists = False

    if resolved in known_legacy_config_targets and not exists:
        _json_set(data, keys, str(canonical_config))
        item["status"] = "ok"
        item["detail"] = "stale_legacy_path_reset_to_canonical"
        item["target"] = str(canonical_config)
        return item, True

    item["detail"] = "preserved_explicit_or_existing"
    return item, False


def sanitize_layout_v2_paths(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Dict[str, Any]:
    """Self-heal stale legacy/repo-local DB/config fields in canonical config/state files."""
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)

    items: List[Dict[str, Any]] = []

    def _append(item: Dict[str, Any]) -> None:
        items.append(item)

    # Canonical app config file.
    if p.config_file.exists():
        try:
            cfg = _read_json_object(p.config_file)
            changed = False

            backend = _resolve_path_like(_json_get(cfg, ("gui_app", "backend_path")), base=p.repo_root)
            backend = backend or p.repo_root.resolve(strict=False)
            canonical_db = p.main_db_file.resolve(strict=False)
            known_db = _known_legacy_db_targets(paths=p, legacy=l, backend=backend)

            for field in (("database", "path"), ("gui_app", "database_path")):
                item, did_change = _sanitize_db_field(
                    cfg,
                    scope="config",
                    keys=field,
                    backend=backend,
                    canonical_db=canonical_db,
                    known_legacy_db_targets=known_db,
                )
                _append(item)
                changed = changed or did_change

            if changed:
                _safe_write_json(p.config_file, cfg)
        except Exception as exc:
            _append(
                {
                    "scope": "config",
                    "field": "*",
                    "status": "error",
                    "detail": f"sanitize_failed: {exc}",
                }
            )
    else:
        _append({"scope": "config", "field": "*", "status": "skipped", "detail": "file_missing"})

    # GUI settings state file.
    if p.gui_settings_file.exists():
        try:
            settings = _read_json_object(p.gui_settings_file)
            changed = False

            backend = _resolve_path_like(_json_get(settings, ("backend", "backend_path")), base=p.repo_root)
            backend = backend or p.repo_root.resolve(strict=False)
            canonical_db = p.main_db_file.resolve(strict=False)
            canonical_config = p.config_file.resolve(strict=False)
            known_db = _known_legacy_db_targets(paths=p, legacy=l, backend=backend)
            known_config = _known_legacy_config_targets(paths=p, legacy=l, backend=backend)

            for field in (("backend", "database_path"), ("backend", "last_database_path")):
                item, did_change = _sanitize_db_field(
                    settings,
                    scope="state",
                    keys=field,
                    backend=backend,
                    canonical_db=canonical_db,
                    known_legacy_db_targets=known_db,
                )
                _append(item)
                changed = changed or did_change

            item, did_change = _sanitize_config_field(
                settings,
                scope="state",
                keys=("backend", "config_path"),
                backend=backend,
                canonical_config=canonical_config,
                known_legacy_config_targets=known_config,
            )
            _append(item)
            changed = changed or did_change

            if changed:
                _safe_write_json(p.gui_settings_file, settings)
        except Exception as exc:
            _append(
                {
                    "scope": "state",
                    "field": "*",
                    "status": "error",
                    "detail": f"sanitize_failed: {exc}",
                }
            )
    else:
        _append({"scope": "state", "field": "*", "status": "skipped", "detail": "file_missing"})

    return {
        "items": items,
        "changed": sum(1 for i in items if i.get("status") == "ok"),
        "errors": sum(1 for i in items if i.get("status") == "error"),
        "skipped": sum(1 for i in items if i.get("status") == "skipped"),
    }


def ensure_layout_dirs(*, paths: Optional[DirracudaPaths] = None) -> Dict[str, Any]:
    p = paths or get_paths()
    created = []
    required = [
        p.home_root,
        p.conf_dir,
        p.data_dir,
        p.experimental_dir,
        p.quarantine_dir,
        p.extracted_dir,
        p.tmpfs_quarantine_dir,
        p.cache_probe_smb_dir,
        p.cache_probe_ftp_dir,
        p.cache_probe_http_dir,
        p.state_dir,
        p.templates_scan_dir,
        p.templates_filter_dir,
        p.migrations_dir,
        p.migration_reports_dir,
        p.migration_backups_dir,
        p.logs_dir,
        p.extract_logs_dir,
        p.app_logs_dir,
        p.signatures_rce_dir,
        p.wordlists_dir,
    ]
    for d in required:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
    return {"created": created, "required_count": len(required)}


def seed_conf_assets(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Dict[str, Any]:
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)

    results: List[Dict[str, Any]] = []

    # Canonical config is seeded from the example first to avoid carrying local
    # repo edits into user runtime state.
    if not p.config_file.exists():
        seed_src = l.repo_config_example_file if l.repo_config_example_file.exists() else l.repo_config_file
        results.append(_copy_asset_if_missing(seed_src, p.config_file))

    results.append(_copy_asset_if_missing(l.repo_exclusion_list_file, p.exclusion_list_file))
    results.append(_copy_asset_if_missing(l.repo_ransomware_indicators_file, p.ransomware_indicators_file))

    if l.repo_signatures_dir.exists() and l.repo_signatures_dir.is_dir():
        for file_path in sorted(l.repo_signatures_dir.rglob("*")):
            rel = file_path.relative_to(l.repo_signatures_dir)
            dst = p.signatures_dir / rel
            if file_path.is_dir():
                if not dst.exists():
                    dst.mkdir(parents=True, exist_ok=True)
                continue
            results.append(_copy_asset_if_missing(file_path, dst))

    if l.repo_wordlists_dir.exists() and l.repo_wordlists_dir.is_dir():
        for file_path in sorted(l.repo_wordlists_dir.rglob("*")):
            rel = file_path.relative_to(l.repo_wordlists_dir)
            dst = p.wordlists_dir / rel
            if file_path.is_dir():
                if not dst.exists():
                    dst.mkdir(parents=True, exist_ok=True)
                continue
            results.append(_copy_asset_if_missing(file_path, dst))

    return {
        "items": results,
        "copied": sum(1 for r in results if r.get("status") == "ok"),
        "skipped": sum(1 for r in results if r.get("status") != "ok"),
    }


def bootstrap_layout_v2(*, paths: Optional[DirracudaPaths] = None, legacy: Optional[LegacyPaths] = None) -> Dict[str, Any]:
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)
    dirs = ensure_layout_dirs(paths=p)
    seeded = seed_conf_assets(paths=p, legacy=l)
    sanitized = sanitize_layout_v2_paths(paths=p, legacy=l)
    return {"dirs": dirs, "seeded": seeded, "sanitized": sanitized}


def _backup_item(src: Path, backup_target: Path) -> None:
    backup_target.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, backup_target)
    else:
        shutil.copy2(src, backup_target)


def _move_or_copy(src: Path, dst: Path) -> Tuple[str, str]:
    """Return (mode, detail). mode in {moved, copied}."""
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        src_dev = src.stat().st_dev
        dst_dev = dst.parent.stat().st_dev
    except Exception:
        src_dev = dst_dev = None

    if src_dev is not None and dst_dev is not None and src_dev == dst_dev:
        shutil.move(str(src), str(dst))
        return ("moved", "same_filesystem_move")

    if src.is_dir():
        shutil.copytree(src, dst)
        if not dst.exists():
            raise RuntimeError("copytree verification failed")
        shutil.rmtree(src)
    else:
        shutil.copy2(src, dst)
        if not dst.exists() or _hash_file(src) != _hash_file(dst):
            raise RuntimeError("file copy verification failed")
        src.unlink(missing_ok=True)

    return ("copied", "cross_filesystem_copy_then_remove")


def _migration_operation(
    *,
    src: Path,
    dst: Path,
    action: str,
    report: List[Dict[str, Any]],
    backup_root: Path,
    backup_label: str,
) -> None:
    item = {
        "source": str(src),
        "target": str(dst),
        "action": action,
        "status": "skipped",
        "detail": "source_missing",
    }

    if not src.exists():
        report.append(item)
        return

    if dst.exists():
        try:
            if src.resolve(strict=False) == dst.resolve(strict=False):
                item["detail"] = "source_is_target"
                report.append(item)
                return
        except Exception:
            pass

        if src.is_dir() and dst.is_dir():
            backup_target = backup_root / backup_label
            try:
                _backup_item(src, backup_target)
            except Exception as exc:
                item["status"] = "error"
                item["detail"] = f"backup_failed: {exc}"
                report.append(item)
                return

            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                if action == "move":
                    shutil.rmtree(src)
                    item["mode"] = "merged_dir_move"
                else:
                    item["mode"] = "merged_dir_copy"
                item["status"] = "ok"
                item["detail"] = "merged_existing_dir"
                report.append(item)
                return
            except Exception as exc:
                item["status"] = "error"
                item["detail"] = f"migration_failed: {exc}"
                report.append(item)
                return

        item["detail"] = "target_exists"
        report.append(item)
        return

    backup_target = backup_root / backup_label
    try:
        _backup_item(src, backup_target)
    except Exception as exc:
        item["status"] = "error"
        item["detail"] = f"backup_failed: {exc}"
        report.append(item)
        return

    try:
        if action == "copy":
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            item["status"] = "ok"
            item["detail"] = "copied"
        else:
            mode, detail = _move_or_copy(src, dst)
            item["status"] = "ok"
            item["detail"] = detail
            item["mode"] = mode
    except Exception as exc:
        item["status"] = "error"
        item["detail"] = f"migration_failed: {exc}"

    report.append(item)


def _required_layout_ready(paths: DirracudaPaths) -> Tuple[bool, List[str]]:
    required_paths: List[Tuple[Path, str]] = [
        (paths.conf_dir, "dir"),
        (paths.data_dir, "dir"),
        (paths.state_dir, "dir"),
        (paths.logs_dir, "dir"),
        (paths.config_file, "file"),
        (paths.templates_scan_dir, "dir"),
        (paths.templates_filter_dir, "dir"),
        (paths.cache_probe_smb_dir, "dir"),
        (paths.cache_probe_ftp_dir, "dir"),
        (paths.cache_probe_http_dir, "dir"),
        (paths.migration_reports_dir, "dir"),
        (paths.migration_backups_dir, "dir"),
    ]
    missing = []
    for p, kind in required_paths:
        if not p.exists():
            missing.append(f"{p} (missing)")
            continue
        try:
            if kind == "file":
                if not p.is_file():
                    missing.append(f"{p} (not_file)")
                    continue
                if not os.access(p, os.R_OK | os.W_OK):
                    missing.append(f"{p} (not_rw)")
                    continue
            else:
                if not p.is_dir():
                    missing.append(f"{p} (not_dir)")
                    continue
                if not os.access(p, os.R_OK | os.W_OK | os.X_OK):
                    missing.append(f"{p} (not_rwx)")
                    continue
        except Exception:
            missing.append(f"{p} (error)")
    return (len(missing) == 0, missing)


def _main_db_migration_operations(paths: DirracudaPaths, legacy: LegacyPaths) -> List[Tuple[Path, Path, str, str]]:
    """Main DB migration ops in canonical fallback priority order."""
    return [
        (legacy.flat_main_db_file, paths.main_db_file, "move", "home_flat/dirracuda.db"),
        (legacy.flat_legacy_db_file, paths.main_db_file, "move", "home_flat/smbseek.db"),
        (legacy.legacy_home_root / "dirracuda.db", paths.main_db_file, "move", "legacy_home/dirracuda.db"),
        (legacy.legacy_home_root / "smbseek.db", paths.main_db_file, "move", "legacy_home/smbseek.db"),
        (legacy.repo_main_db_file, paths.main_db_file, "move", "repo/dirracuda.db"),
        (legacy.repo_legacy_db_file, paths.main_db_file, "move", "repo/smbseek.db"),
    ]


def run_layout_v2_migration(
    *,
    paths: Optional[DirracudaPaths] = None,
    legacy: Optional[LegacyPaths] = None,
) -> Dict[str, Any]:
    """Run one-time migration to layout v2.

    Idempotency key: state/migrations/state.json -> layout_version == 2 and status == success.
    """
    p = paths or get_paths()
    l = legacy or get_legacy_paths(paths=p)

    bootstrap_result = bootstrap_layout_v2(paths=p, legacy=l)
    sanitized_summary = (
        bootstrap_result.get("sanitized")
        if isinstance(bootstrap_result.get("sanitized"), dict)
        else {"items": [], "changed": 0, "errors": 0, "skipped": 0}
    )

    state = read_layout_state(paths=p)
    state_success = int(state.get("layout_version", 0) or 0) >= LAYOUT_VERSION and state.get("status") == "success"
    canonical_db_ready = p.main_db_file.exists() and p.main_db_file.is_file()
    db_fallback_candidates = get_runtime_main_db_fallback_candidates(paths=p, legacy=l)
    existing_db_fallback_candidates = [
        str(candidate)
        for candidate in db_fallback_candidates
        if candidate.exists() and candidate.is_file()
    ]

    db_recovery_needed = state_success and not canonical_db_ready and bool(existing_db_fallback_candidates)
    if state_success and not db_recovery_needed:
        report_file: str | Path | None = state.get("report_file")
        sanitized_changed = int(sanitized_summary.get("changed", 0) or 0)
        sanitized_errors = int(sanitized_summary.get("errors", 0) or 0)
        if sanitized_changed > 0 or sanitized_errors > 0:
            run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            report_payload = {
                "run_id": run_id,
                "layout_version": LAYOUT_VERSION,
                "status": "already_done",
                "timestamp": _utc_now_iso(),
                "migrated": {"ok": 0, "skipped": 0, "errors": 0},
                "missing_required": [],
                "fallback_paths": [],
                "db_recovery_attempted": False,
                "db_recovery_status": "not_needed",
                "db_fallback_candidates": existing_db_fallback_candidates,
                "bootstrap": bootstrap_result,
                "sanitized": sanitized_summary,
                "entries": [],
            }
            report_path = p.migration_reports_dir / f"layout_v2_{run_id}.json"
            _safe_write_json(report_path, report_payload)
            report_file = str(report_path)

            state_payload = {
                "layout_version": LAYOUT_VERSION,
                "status": "success",
                "last_run_at": _utc_now_iso(),
                "run_id": run_id,
                "report_file": report_file,
                "missing_required": [],
            }
            write_layout_state(state_payload, paths=p)

        return {
            "status": "already_done",
            "layout_version": LAYOUT_VERSION,
            "report_file": str(report_file or ""),
            "fallback_paths": [],
            "migrated": {"ok": 0, "skipped": 0, "errors": 0},
            "db_recovery_attempted": False,
            "db_recovery_status": "not_needed",
            "db_fallback_candidates": existing_db_fallback_candidates,
            "missing_required": [],
            "sanitized": sanitized_summary,
        }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_root = p.migration_backups_dir / run_id
    report_entries: List[Dict[str, Any]] = []
    db_recovery_attempted = db_recovery_needed

    # Stage order: conf assets -> main DB -> experimental DBs -> settings/templates/cache/logs
    ops: List[Tuple[Path, Path, str, str]] = [
        # conf assets
        (l.flat_config_file, p.config_file, "move", "home_flat/config.json"),
        (l.flat_home_root / "conf" / "config.json", p.config_file, "move", "home_flat/conf/config.json"),
        (l.legacy_home_root / "config.json", p.config_file, "move", "legacy_home/config.json"),
        (l.legacy_home_root / "conf" / "config.json", p.config_file, "move", "legacy_home/conf/config.json"),
        (l.repo_config_file, p.config_file, "copy", f"repo/conf/config.json"),

        (l.flat_exclusion_list_file, p.exclusion_list_file, "move", "home_flat/exclusion_list.json"),
        (l.flat_home_root / "conf" / "exclusion_list.json", p.exclusion_list_file, "move", "home_flat/conf/exclusion_list.json"),
        (l.legacy_home_root / "exclusion_list.json", p.exclusion_list_file, "move", "legacy_home/exclusion_list.json"),
        (l.legacy_home_root / "conf" / "exclusion_list.json", p.exclusion_list_file, "move", "legacy_home/conf/exclusion_list.json"),

        (l.flat_ransomware_indicators_file, p.ransomware_indicators_file, "move", "home_flat/ransomware_indicators.json"),
        (l.flat_home_root / "conf" / "ransomware_indicators.json", p.ransomware_indicators_file, "move", "home_flat/conf/ransomware_indicators.json"),
        (l.legacy_home_root / "ransomware_indicators.json", p.ransomware_indicators_file, "move", "legacy_home/ransomware_indicators.json"),
        (l.legacy_home_root / "conf" / "ransomware_indicators.json", p.ransomware_indicators_file, "move", "legacy_home/conf/ransomware_indicators.json"),

        (l.flat_home_root / "signatures", p.signatures_dir, "move", "home_flat/signatures"),
        (l.flat_home_root / "conf" / "signatures", p.signatures_dir, "move", "home_flat/conf/signatures"),
        (l.legacy_home_root / "signatures", p.signatures_dir, "move", "legacy_home/signatures"),
        (l.legacy_home_root / "conf" / "signatures", p.signatures_dir, "move", "legacy_home/conf/signatures"),
        (l.flat_home_root / "wordlists", p.wordlists_dir, "move", "home_flat/wordlists"),
        (l.flat_home_root / "conf" / "wordlists", p.wordlists_dir, "move", "home_flat/conf/wordlists"),
        (l.legacy_home_root / "wordlists", p.wordlists_dir, "move", "legacy_home/wordlists"),
        (l.legacy_home_root / "conf" / "wordlists", p.wordlists_dir, "move", "legacy_home/conf/wordlists"),

        # main DB
        *_main_db_migration_operations(p, l),

        # experimental sidecar DBs
        (l.flat_sidecar_se_dork_file, p.se_dork_db_file, "move", f"home_flat/se_dork.db"),
        (l.flat_sidecar_reddit_od_file, p.reddit_od_db_file, "move", f"home_flat/reddit_od.db"),
        (l.flat_sidecar_dorkbook_file, p.dorkbook_db_file, "move", f"home_flat/dorkbook.db"),
        (l.flat_sidecar_keymaster_file, p.keymaster_db_file, "move", f"home_flat/keymaster.db"),

        # settings/templates/cache/log paths
        (l.flat_gui_settings_file, p.gui_settings_file, "move", f"home_flat/gui_settings.json"),
        (l.flat_scan_templates_dir, p.templates_scan_dir, "move", f"home_flat/templates"),
        (l.flat_filter_templates_dir, p.templates_filter_dir, "move", f"home_flat/filter_templates"),

        (l.flat_probe_smb_dir, p.cache_probe_smb_dir, "move", f"home_flat/probes"),
        (l.flat_probe_ftp_dir, p.cache_probe_ftp_dir, "move", f"home_flat/ftp_probes"),
        (l.flat_probe_http_dir, p.cache_probe_http_dir, "move", f"home_flat/http_probes"),

        (l.flat_quarantine_dir, p.quarantine_dir, "move", f"home_flat/quarantine"),
        (l.flat_extracted_dir, p.extracted_dir, "move", f"home_flat/extracted"),
        (l.flat_tmpfs_quarantine_dir, p.tmpfs_quarantine_dir, "move", f"home_flat/quarantine_tmpfs"),

        (l.flat_extract_logs_dir, p.extract_logs_dir, "move", f"home_flat/extract_logs"),
        (l.flat_rce_analysis_log_file, p.rce_analysis_log_file, "move", f"home_flat/rce_analysis.jsonl"),
    ]

    # Legacy ~/.smbseek support (known old layout).
    if l.legacy_home_root.exists():
        legacy_items = [
            (l.legacy_home_root / "gui_settings.json", p.gui_settings_file, "move", "legacy_home/gui_settings.json"),
            (l.legacy_home_root / "templates", p.templates_scan_dir, "move", "legacy_home/templates"),
            (l.legacy_home_root / "filter_templates", p.templates_filter_dir, "move", "legacy_home/filter_templates"),
            (l.legacy_home_root / "probes", p.cache_probe_smb_dir, "move", "legacy_home/probes"),
            (l.legacy_home_root / "ftp_probes", p.cache_probe_ftp_dir, "move", "legacy_home/ftp_probes"),
            (l.legacy_home_root / "http_probes", p.cache_probe_http_dir, "move", "legacy_home/http_probes"),
            (l.legacy_home_root / "se_dork.db", p.se_dork_db_file, "move", "legacy_home/se_dork.db"),
            (l.legacy_home_root / "reddit_od.db", p.reddit_od_db_file, "move", "legacy_home/reddit_od.db"),
            (l.legacy_home_root / "dorkbook.db", p.dorkbook_db_file, "move", "legacy_home/dorkbook.db"),
            (l.legacy_home_root / "keymaster.db", p.keymaster_db_file, "move", "legacy_home/keymaster.db"),
            (l.legacy_home_root / "quarantine", p.quarantine_dir, "move", "legacy_home/quarantine"),
            (l.legacy_home_root / "extracted", p.extracted_dir, "move", "legacy_home/extracted"),
            (l.legacy_home_root / "quarantine_tmpfs", p.tmpfs_quarantine_dir, "move", "legacy_home/quarantine_tmpfs"),
            (l.legacy_home_root / "extract_logs", p.extract_logs_dir, "move", "legacy_home/extract_logs"),
            (l.legacy_home_root / "rce_analysis.jsonl", p.rce_analysis_log_file, "move", "legacy_home/rce_analysis.jsonl"),
        ]
        ops.extend(legacy_items)

    if db_recovery_attempted:
        ops = _main_db_migration_operations(p, l)

    for src, dst, action, label in ops:
        _migration_operation(
            src=src,
            dst=dst,
            action=action,
            report=report_entries,
            backup_root=backup_root,
            backup_label=label,
        )

    bootstrap_result = bootstrap_layout_v2(paths=p, legacy=l)
    sanitized_summary = (
        bootstrap_result.get("sanitized")
        if isinstance(bootstrap_result.get("sanitized"), dict)
        else {"items": [], "changed": 0, "errors": 0, "skipped": 0}
    )
    ready, missing_required = _required_layout_ready(p)
    canonical_db_ready = p.main_db_file.exists() and p.main_db_file.is_file()
    if canonical_db_ready:
        try:
            canonical_db_ready = os.access(p.main_db_file, os.R_OK | os.W_OK)
        except Exception:
            canonical_db_ready = False

    ok_count = sum(1 for r in report_entries if r.get("status") == "ok")
    err_count = sum(1 for r in report_entries if r.get("status") == "error")
    skipped_count = sum(1 for r in report_entries if r.get("status") == "skipped")

    fallback_paths: List[str] = []
    for item in report_entries:
        if item.get("status") == "error":
            src = Path(item.get("source", ""))
            if src.exists():
                fallback_paths.append(str(src))

    if not ready:
        for miss in missing_required:
            fallback_paths.append(miss)

    existing_db_fallback_candidates = [
        str(candidate)
        for candidate in db_fallback_candidates
        if candidate.exists() and candidate.is_file()
    ]
    if not canonical_db_ready:
        fallback_paths.extend(existing_db_fallback_candidates)

    status = "success"
    if err_count > 0 or not ready:
        status = "partial" if ok_count > 0 else "failed"
    if db_recovery_attempted and not canonical_db_ready:
        status = "partial" if status == "success" else status

    if db_recovery_attempted:
        if status == "success" and canonical_db_ready:
            db_recovery_status = "success"
        elif status == "failed":
            db_recovery_status = "failed"
        else:
            db_recovery_status = "partial"
    else:
        db_recovery_status = "not_attempted"

    report_payload = {
        "run_id": run_id,
        "layout_version": LAYOUT_VERSION,
        "status": status,
        "timestamp": _utc_now_iso(),
        "migrated": {
            "ok": ok_count,
            "skipped": skipped_count,
            "errors": err_count,
        },
        "missing_required": missing_required,
        "fallback_paths": sorted(set(fallback_paths)),
        "db_recovery_attempted": db_recovery_attempted,
        "db_recovery_status": db_recovery_status,
        "db_fallback_candidates": existing_db_fallback_candidates,
        "bootstrap": bootstrap_result,
        "sanitized": sanitized_summary,
        "entries": report_entries,
    }

    report_file = p.migration_reports_dir / f"layout_v2_{run_id}.json"
    _safe_write_json(report_file, report_payload)

    state_payload = {
        "layout_version": LAYOUT_VERSION if status == "success" and ready else 0,
        "status": status,
        "last_run_at": _utc_now_iso(),
        "run_id": run_id,
        "report_file": str(report_file),
        "missing_required": missing_required,
    }
    write_layout_state(state_payload, paths=p)

    return {
        "status": status,
        "layout_version": state_payload["layout_version"],
        "report_file": str(report_file),
        "fallback_paths": sorted(set(fallback_paths)),
        "missing_required": missing_required,
        "migrated": report_payload["migrated"],
        "db_recovery_attempted": db_recovery_attempted,
        "db_recovery_status": db_recovery_status,
        "db_fallback_candidates": existing_db_fallback_candidates,
        "sanitized": sanitized_summary,
    }


def summary_message_for_migration_result(result: Dict[str, Any]) -> Tuple[str, str]:
    """Return (title, message) for startup UI notification."""
    status = str(result.get("status", "unknown"))
    canonical_root = str(get_paths().home_root)
    migrated = result.get("migrated", {}) if isinstance(result.get("migrated"), dict) else {}
    ok = int(migrated.get("ok", 0) or 0)
    skipped = int(migrated.get("skipped", 0) or 0)
    errors = int(migrated.get("errors", 0) or 0)
    report_file = str(result.get("report_file", "")).strip()
    fallback_paths = list(result.get("fallback_paths", []) or [])
    db_recovery_attempted = bool(result.get("db_recovery_attempted"))
    db_recovery_status = str(result.get("db_recovery_status", "")).strip()
    db_runtime_fallback_path = str(result.get("db_runtime_fallback_path", "")).strip()
    sanitized = result.get("sanitized", {}) if isinstance(result.get("sanitized"), dict) else {}
    sanitized_changed = int(sanitized.get("changed", 0) or 0)
    sanitized_errors = int(sanitized.get("errors", 0) or 0)

    header = (
        "Dirracuda data layout migration completed."
        if status == "success"
        else "Dirracuda data layout migration completed with issues."
    )
    if status == "already_done":
        message = "Dirracuda data layout v2 is already active."
        if sanitized_changed > 0:
            message = f"{message}\nPath fields self-healed: {sanitized_changed}."
        if sanitized_errors > 0:
            message = f"{message}\nPath self-heal warnings: {sanitized_errors}."
        return (
            "Data Layout",
            message,
        )

    lines = [
        header,
        "",
        f"Canonical root: {canonical_root}",
        "",
        f"Moved/Copied: {ok}",
        f"Skipped: {skipped}",
        f"Errors: {errors}",
    ]

    if db_recovery_attempted:
        lines.extend(["", f"Main DB recovery: {db_recovery_status or 'unknown'}"])
    if sanitized_changed > 0 or sanitized_errors > 0:
        lines.extend(
            [
                "",
                f"Path self-heal changed: {sanitized_changed}",
                f"Path self-heal warnings: {sanitized_errors}",
            ]
        )
    if db_runtime_fallback_path:
        lines.extend(
            [
                "",
                "Session fallback database in use:",
                f"- {db_runtime_fallback_path}",
                "",
                "Dirracuda will retry migration on next startup.",
            ]
        )

    if fallback_paths:
        lines.extend(["", "Runtime fallback paths in use:"])
        for p in fallback_paths[:8]:
            lines.append(f"- {p}")
        if len(fallback_paths) > 8:
            lines.append(f"- ... {len(fallback_paths) - 8} more")

    if report_file:
        lines.extend(["", f"Report: {report_file}"])

    title = "Data Layout Migration"
    return title, "\n".join(lines)


__all__ = [
    "DirracudaPaths",
    "LegacyPaths",
    "LAYOUT_VERSION",
    "bootstrap_layout_v2",
    "ensure_layout_dirs",
    "get_legacy_paths",
    "get_paths",
    "is_layout_v2_complete",
    "read_layout_state",
    "resolve_runtime_config_path",
    "resolve_runtime_gui_settings_path",
    "resolve_runtime_main_db_path",
    "resolve_runtime_main_db_for_session",
    "run_layout_v2_migration",
    "sanitize_layout_v2_paths",
    "select_existing_path",
    "get_runtime_main_db_fallback_candidates",
    "summary_message_for_migration_result",
    "write_layout_state",
]
