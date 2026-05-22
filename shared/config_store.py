"""Config shard registry/store for modular runtime configuration.

This module owns:
- shard ownership mapping under ~/.dirracuda/conf.d
- dual-read migration from legacy config.json + gui_settings.json
- section-owner writes with atomic per-shard persistence
- compatibility materialization to ~/.dirracuda/conf/config.json for legacy readers
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from shared.path_service import (
    DirracudaPaths,
    LegacyPaths,
    ensure_layout_dirs,
    get_legacy_paths,
    get_paths,
)

logger = logging.getLogger(__name__)


EXPERIMENTAL_MODULES: Tuple[str, ...] = (
    "se_dork",
    "reddit_grab",
    "dorkbook",
    "keymaster",
    "webui",
)


@dataclass(frozen=True)
class ConfigShard:
    """Single shard definition with explicit top-level ownership."""

    name: str
    relative_path: str
    sections: Tuple[str, ...]


CORE_SHARDS: Tuple[ConfigShard, ...] = (
    ConfigShard(
        name="core.scan",
        relative_path="core/scan.json",
        sections=(
            "shodan",
            "workflow",
            "connection",
            "discovery",
            "access",
            "ftp",
            "http",
            "exclusion_progress_interval",
        ),
    ),
    ConfigShard(
        name="core.storage",
        relative_path="core/storage.json",
        sections=(
            "database",
            "file_collection",
            "file_browser",
            "ftp_browser",
            "http_browser",
            "quarantine",
            "clamav",
            "gui_app",
        ),
    ),
    ConfigShard(
        name="core.security",
        relative_path="core/security.json",
        sections=(
            "security",
            "censys",
        ),
    ),
    ConfigShard(
        name="core.output",
        relative_path="core/output.json",
        sections=(
            "output",
            "_note",
        ),
    ),
)

EXPERIMENTAL_SHARDS: Tuple[ConfigShard, ...] = tuple(
    ConfigShard(
        name=f"experimental.{module}",
        relative_path=f"experimental/{module}.json",
        sections=(module,),
    )
    for module in EXPERIMENTAL_MODULES
)

MAIN_SHARDS: Tuple[ConfigShard, ...] = CORE_SHARDS + EXPERIMENTAL_SHARDS

_SECTION_OWNER: Dict[str, ConfigShard] = {
    section: shard
    for shard in MAIN_SHARDS
    for section in shard.sections
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_read_json_object(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _pick_sections(source: Mapping[str, Any], sections: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for section in sections:
        if section in source:
            out[section] = copy.deepcopy(source[section])
    return out


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _load_repo_default_config(paths: DirracudaPaths, legacy: LegacyPaths) -> Dict[str, Any]:
    candidates = [
        legacy.repo_config_example_file,
        legacy.repo_config_file,
        paths.config_file,
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        loaded = _safe_read_json_object(candidate)
        if loaded:
            return loaded
    return {}


def _legacy_main_config_candidates(paths: DirracudaPaths, legacy: LegacyPaths) -> List[Path]:
    return [
        paths.config_file,
        legacy.flat_home_root / "conf" / "config.json",
        legacy.flat_config_file,
        legacy.legacy_home_root / "conf" / "config.json",
        legacy.legacy_home_root / "config.json",
        legacy.repo_config_file,
        legacy.repo_config_example_file,
    ]


def _legacy_prefs_candidates(paths: DirracudaPaths, legacy: LegacyPaths) -> List[Path]:
    return [
        paths.gui_settings_file,
        legacy.flat_gui_settings_file,
        legacy.legacy_home_root / "gui_settings.json",
    ]


@dataclass(frozen=True)
class MigrationResult:
    status: str
    report_file: Optional[str]
    warning: Optional[str]


class ConfigStore:
    """Central registry/store for modular config shards."""

    def __init__(
        self,
        *,
        paths: Optional[DirracudaPaths] = None,
        legacy: Optional[LegacyPaths] = None,
    ) -> None:
        self.paths = paths or get_paths()
        self.legacy = legacy or get_legacy_paths(paths=self.paths)
        self.conf_d_dir = self.paths.home_root / "conf.d"
        self.prefs_file = self.conf_d_dir / "prefs" / "user-prefs.json"

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def shard_path(self, shard: ConfigShard) -> Path:
        return self.conf_d_dir / shard.relative_path

    def all_main_shard_paths(self) -> Dict[str, Path]:
        return {shard.name: self.shard_path(shard) for shard in MAIN_SHARDS}

    def has_any_main_shard(self) -> bool:
        return any(path.exists() for path in self.all_main_shard_paths().values())

    def ensure_dirs(self) -> None:
        ensure_layout_dirs(paths=self.paths)
        for shard in MAIN_SHARDS:
            self.shard_path(shard).parent.mkdir(parents=True, exist_ok=True)
        self.prefs_file.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def ensure_migrated(self) -> MigrationResult:
        """Ensure modular shards exist; migrate once from legacy files when needed."""
        self.ensure_dirs()
        if self.has_any_main_shard() and self.prefs_file.exists():
            return MigrationResult(status="already_modular", report_file=None, warning=None)

        return self._run_migration()

    def _run_migration(self) -> MigrationResult:
        ts = _utc_stamp()
        report_path = self.paths.migration_reports_dir / f"config_modularization_{ts}.json"
        backup_dir = self.paths.migration_backups_dir / f"config_modularization_{ts}"
        report_items: List[Dict[str, Any]] = []

        def _add(action: str, status: str, detail: str, **extra: Any) -> None:
            item = {"action": action, "status": status, "detail": detail}
            if extra:
                item.update(extra)
            report_items.append(item)

        legacy_main = self._load_first_existing_json(_legacy_main_config_candidates(self.paths, self.legacy))
        legacy_prefs = self._load_first_existing_json(_legacy_prefs_candidates(self.paths, self.legacy))

        if legacy_main is None:
            legacy_main = {}
        if legacy_prefs is None:
            legacy_prefs = {}

        defaults = _load_repo_default_config(self.paths, self.legacy)
        if not isinstance(defaults, dict):
            defaults = {}

        # If there is no legacy data at all, seed defaults directly.
        source_main = _deep_merge(defaults, legacy_main)

        # Build shard payloads from explicit ownership map.
        shard_payloads: Dict[ConfigShard, Dict[str, Any]] = {}
        for shard in MAIN_SHARDS:
            payload = _pick_sections(source_main, shard.sections)
            shard_payloads[shard] = payload

        # GUI prefs live in prefs shard; module-specific prefs move into experimental shards.
        prefs_payload = copy.deepcopy(legacy_prefs if isinstance(legacy_prefs, dict) else {})
        for module in EXPERIMENTAL_MODULES:
            module_value = prefs_payload.pop(module, None)
            module_shard = _SECTION_OWNER[module]
            module_payload = shard_payloads.get(module_shard, {})
            existing_module = module_payload.get(module)
            if isinstance(module_value, dict):
                if isinstance(existing_module, dict):
                    module_payload[module] = _deep_merge(existing_module, module_value)
                else:
                    module_payload[module] = copy.deepcopy(module_value)
            elif module not in module_payload:
                module_payload[module] = {}
            shard_payloads[module_shard] = module_payload

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            # Backup known legacy sources if present.
            for candidate in (
                self.paths.config_file,
                self.paths.gui_settings_file,
                self.legacy.flat_config_file,
                self.legacy.flat_gui_settings_file,
                self.legacy.legacy_home_root / "config.json",
                self.legacy.legacy_home_root / "gui_settings.json",
            ):
                if not candidate.exists() or not candidate.is_file():
                    continue
                backup_target = backup_dir / candidate.name
                shutil.copy2(candidate, backup_target)
                _add("backup", "ok", "copied", source=str(candidate), target=str(backup_target))

            # Write shards atomically.
            for shard, payload in shard_payloads.items():
                shard_path = self.shard_path(shard)
                _atomic_write_json(shard_path, payload)
                _add("write_shard", "ok", "written", shard=shard.name, path=str(shard_path))

            _atomic_write_json(self.prefs_file, prefs_payload)
            _add("write_prefs", "ok", "written", path=str(self.prefs_file))

            # Materialize legacy config.json for compatibility readers that still use it.
            self.materialize_legacy_config(source_main)
            _add("materialize_legacy", "ok", "written", path=str(self.paths.config_file))

            report = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "status": "success",
                "report_type": "config_modularization",
                "backups_dir": str(backup_dir),
                "items": report_items,
            }
            _atomic_write_json(report_path, report)
            return MigrationResult(status="success", report_file=str(report_path), warning=None)
        except Exception as exc:
            warning = (
                "Config modularization migration failed; running legacy read path for this session. "
                "Check migration report and permissions under ~/.dirracuda/state/migrations/."
            )
            _add("migration", "error", str(exc))
            report = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "report_type": "config_modularization",
                "backups_dir": str(backup_dir),
                "items": report_items,
                "error": str(exc),
                "recovery_guidance": [
                    "Verify ~/.dirracuda is writable by the current user.",
                    "Inspect this report and backup files for partial writes.",
                    "Re-run Dirracuda to retry migration after fixing permissions.",
                ],
            }
            with contextlib.suppress(Exception):
                _atomic_write_json(report_path, report)
            logger.warning("%s", warning)
            logger.warning("Config modularization migration error: %s", exc)
            return MigrationResult(status="failed", report_file=str(report_path), warning=warning)

    def _load_first_existing_json(self, candidates: Iterable[Path]) -> Optional[Dict[str, Any]]:
        for candidate in candidates:
            if not candidate.exists() or not candidate.is_file():
                continue
            loaded = _safe_read_json_object(candidate)
            if loaded:
                return loaded
        return None

    # ------------------------------------------------------------------
    # Load/compose
    # ------------------------------------------------------------------

    def load_runtime_config(self) -> Tuple[Dict[str, Any], Optional[str]]:
        """Return composed runtime config + optional migration warning."""
        migration = self.ensure_migrated()

        defaults = _load_repo_default_config(self.paths, self.legacy)
        if not isinstance(defaults, dict):
            defaults = {}

        if self.has_any_main_shard():
            composed = copy.deepcopy(defaults)
            for shard in CORE_SHARDS:
                shard_data = _safe_read_json_object(self.shard_path(shard))
                composed = _deep_merge(composed, shard_data)
            # Experimental shards are merged when present.
            for shard in EXPERIMENTAL_SHARDS:
                shard_data = _safe_read_json_object(self.shard_path(shard))
                if shard_data:
                    composed = _deep_merge(composed, shard_data)
            # Keep compatibility file updated for legacy readers.
            self.materialize_legacy_config(composed)
            return composed, migration.warning

        # Legacy fallback if migration failed and no shards are present.
        legacy = self._load_first_existing_json(_legacy_main_config_candidates(self.paths, self.legacy))
        if legacy is None:
            legacy = {}
        return _deep_merge(defaults, legacy), migration.warning

    def materialize_legacy_config(self, composed: Dict[str, Any]) -> None:
        """Write composed config to canonical conf/config.json for legacy readers."""
        _atomic_write_json(self.paths.config_file, composed)

    # ------------------------------------------------------------------
    # Owner-scoped writes
    # ------------------------------------------------------------------

    def update_sections(self, updates: Mapping[str, Any]) -> Dict[str, str]:
        """Apply top-level section updates to owning shard files atomically per shard.

        Args:
            updates: {section_name: section_value}

        Returns:
            {section_name: shard_name}
        """
        if not isinstance(updates, Mapping) or not updates:
            return {}

        # Ensure shard layout is ready. Keep legacy fallback warning at caller level.
        self.ensure_migrated()

        unknown = [section for section in updates.keys() if section not in _SECTION_OWNER]
        if unknown:
            raise KeyError(f"Unknown config section owner(s): {sorted(unknown)}")

        touched_shards = {_SECTION_OWNER[section] for section in updates.keys()}
        shard_state: Dict[ConfigShard, Dict[str, Any]] = {
            shard: _safe_read_json_object(self.shard_path(shard))
            for shard in touched_shards
        }

        section_to_shard: Dict[str, str] = {}
        for section, value in updates.items():
            owner = _SECTION_OWNER[section]
            payload = shard_state[owner]
            payload[section] = copy.deepcopy(value)
            section_to_shard[section] = owner.name

        for shard, payload in shard_state.items():
            _atomic_write_json(self.shard_path(shard), payload)

        composed, _warning = self.load_runtime_config()
        self.materialize_legacy_config(composed)
        return section_to_shard

    def read_section(self, section: str, default: Any = None) -> Any:
        if section not in _SECTION_OWNER:
            return default
        composed, _warning = self.load_runtime_config()
        return copy.deepcopy(composed.get(section, default))

    # ------------------------------------------------------------------
    # Preferences / module settings
    # ------------------------------------------------------------------

    def load_user_prefs(self) -> Dict[str, Any]:
        self.ensure_dirs()
        return _safe_read_json_object(self.prefs_file)

    def save_user_prefs(self, prefs: Mapping[str, Any]) -> None:
        payload = dict(prefs) if isinstance(prefs, Mapping) else {}
        _atomic_write_json(self.prefs_file, payload)

    def module_prefs_path(self, module_name: str) -> Path:
        if module_name not in EXPERIMENTAL_MODULES:
            raise KeyError(f"Unsupported module prefs shard: {module_name}")
        return self.conf_d_dir / "experimental" / f"{module_name}.json"

    def load_module_prefs(self, module_name: str) -> Dict[str, Any]:
        path = self.module_prefs_path(module_name)
        root = _safe_read_json_object(path)
        module_cfg = root.get(module_name)
        if isinstance(module_cfg, dict):
            return copy.deepcopy(module_cfg)
        return {}

    def save_module_prefs(self, module_name: str, module_prefs: Mapping[str, Any]) -> None:
        path = self.module_prefs_path(module_name)
        payload = {
            module_name: dict(module_prefs) if isinstance(module_prefs, Mapping) else {}
        }
        _atomic_write_json(path, payload)


_CONFIG_STORE_CACHE: Dict[str, ConfigStore] = {}


def get_config_store(
    *,
    paths: Optional[DirracudaPaths] = None,
    legacy: Optional[LegacyPaths] = None,
) -> ConfigStore:
    resolved_paths = paths or get_paths()
    cache_key = str(resolved_paths.home_root.resolve(strict=False))
    cached = _CONFIG_STORE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    store = ConfigStore(paths=resolved_paths, legacy=legacy)
    _CONFIG_STORE_CACHE[cache_key] = store
    return store


def clear_config_store_cache() -> None:
    _CONFIG_STORE_CACHE.clear()


__all__ = [
    "ConfigShard",
    "CORE_SHARDS",
    "EXPERIMENTAL_MODULES",
    "EXPERIMENTAL_SHARDS",
    "MAIN_SHARDS",
    "MigrationResult",
    "ConfigStore",
    "get_config_store",
    "clear_config_store_cache",
]
