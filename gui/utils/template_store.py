"""
Template storage utility for ScanDialog presets.

Provides lightweight save/load/delete helpers backed by JSON files stored
in the user's ~/.dirracuda/state/templates/scan directory. Seeds that directory with
curated defaults shipped in the repo and remembers the last-used template
via SettingsManager when available.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

from gui.utils.logging_config import get_logger
from shared.path_service import get_paths, get_legacy_paths

_logger = get_logger("template_store")
_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)


# Repo layout: gui/utils/template_store.py → ../../templates/default_scan_templates
DEFAULT_SEED_DIR = Path(__file__).resolve().parents[2] / "templates" / "default_scan_templates"


@dataclass
class ScanTemplate:
    """In-memory representation of a saved scan template."""
    name: str
    slug: str
    saved_at: Optional[str]
    form_state: Dict[str, Any]


class TemplateStore:
    """Manage scan templates persisted on disk plus last-used tracking."""

    def __init__(self,
                 settings_manager: Optional[Any] = None,
                 base_dir: Optional[Path] = None,
                 seed_dir: Optional[Path] = None) -> None:
        self.settings_manager = settings_manager
        self.templates_dir = self._resolve_dir(base_dir)
        self.seed_dir = seed_dir or DEFAULT_SEED_DIR
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self._seed_default_templates()

    def list_templates(self) -> List[ScanTemplate]:
        """Return all templates sorted by name (case-insensitive)."""
        templates: List[ScanTemplate] = []
        seen: set[str] = set()
        for path in sorted(self.templates_dir.glob("*.json"), key=lambda p: p.name.lower()):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                name = data.get("name") or path.stem
                seen.add(path.stem)
                templates.append(
                    ScanTemplate(
                        name=name,
                        slug=path.stem,
                        saved_at=data.get("saved_at"),
                        form_state=data.get("form_state") or {}
                    )
                )
            except Exception as exc:
                _logger.warning("Failed to load scan template %s: %s", path, exc)

        for legacy_dir in self._legacy_dirs():
            if not legacy_dir.exists():
                continue
            for path in sorted(legacy_dir.glob("*.json"), key=lambda p: p.name.lower()):
                if path.stem in seen:
                    continue
                try:
                    with path.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    name = data.get("name") or path.stem
                    templates.append(
                        ScanTemplate(
                            name=name,
                            slug=path.stem,
                            saved_at=data.get("saved_at"),
                            form_state=data.get("form_state") or {},
                        )
                    )
                except Exception as exc:
                    _logger.warning("Failed to load legacy scan template %s: %s", path, exc)
        templates.sort(key=lambda t: t.name.lower())
        return templates

    def load_template(self, slug: str) -> Optional[ScanTemplate]:
        """Load template by slug."""
        path = self.templates_dir / f"{slug}.json"
        if not path.exists():
            found = None
            for legacy_dir in self._legacy_dirs():
                candidate = legacy_dir / f"{slug}.json"
                if candidate.exists():
                    found = candidate
                    break
            if found is None:
                return None
            path = found
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return ScanTemplate(
                name=data.get("name") or slug,
                slug=slug,
                saved_at=data.get("saved_at"),
                form_state=data.get("form_state") or {}
            )
        except Exception as exc:
            _logger.warning("Failed to read scan template %s: %s", slug, exc)
            return None

    def save_template(self, name: str, form_state: Dict[str, Any]) -> ScanTemplate:
        """Save template to disk (overwrites existing slug)."""
        slug = self.slugify(name)
        path = self.templates_dir / f"{slug}.json"
        data = {
            "name": name,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "form_state": form_state
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        template = ScanTemplate(name=name, slug=slug, saved_at=data["saved_at"], form_state=form_state)
        self.set_last_used(slug)
        return template

    def delete_template(self, slug: str) -> bool:
        """Delete template; returns True if removed."""
        path = self.templates_dir / f"{slug}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            if self.get_last_used() == slug:
                self.set_last_used(None)
            return True
        except OSError as exc:
            _logger.warning("Failed to delete scan template %s: %s", slug, exc)
            return False

    def get_last_used(self) -> Optional[str]:
        """Read last-used template slug from settings, if available."""
        if not self.settings_manager:
            return None
        if hasattr(self.settings_manager, "get_last_template_slug"):
            return self.settings_manager.get_last_template_slug()
        return self.settings_manager.get_setting("templates.last_used", None)

    def set_last_used(self, slug: Optional[str]) -> None:
        """Persist last-used template slug."""
        if self.settings_manager:
            if hasattr(self.settings_manager, "set_last_template_slug"):
                self.settings_manager.set_last_template_slug(slug)
            else:
                self.settings_manager.set_setting("templates.last_used", slug)

    @staticmethod
    def _resolve_dir(base_dir: Optional[Path]) -> Path:
        if base_dir:
            return Path(base_dir).expanduser().resolve()
        return _PATHS.templates_scan_dir

    def _legacy_dirs(self) -> List[Path]:
        canonical = self.templates_dir.resolve(strict=False)
        if canonical == _PATHS.templates_scan_dir.resolve(strict=False):
            return [
                _LEGACY.flat_scan_templates_dir,
                _LEGACY.legacy_home_root / "templates",
            ]
        if canonical == _PATHS.templates_filter_dir.resolve(strict=False):
            return [
                _LEGACY.flat_filter_templates_dir,
                _LEGACY.legacy_home_root / "filter_templates",
            ]
        return []

    @staticmethod
    def slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            slug = f"template-{int(time.time())}"
        return slug

    def _seed_default_templates(self) -> None:
        """Copy bundled templates into the user directory if they're missing."""
        if not self.seed_dir or not self.seed_dir.exists():
            return

        for template_file in self.seed_dir.glob("*.json"):
            target = self.templates_dir / template_file.name
            if target.exists():
                continue
            try:
                shutil.copy(template_file, target)
            except Exception as exc:
                _logger.warning("Failed to seed template %s: %s", template_file.name, exc)
