# F3: User Data Path Migration (`~/.smbseek` → `~/.dirracuda`)

## Context

Card F3 of the Dirracuda rebrand sequence. All user data lives under `~/.smbseek/`; the canonical location must become `~/.dirracuda/`. Existing users must not lose data. F2 is complete. F4 (DB filename) is explicitly out of scope for this card.

Outcome: on every startup, `~/.dirracuda` is guaranteed to exist. Any prior user data that hasn't already been copied is retried. All path constants in source point to `~/.dirracuda`. Writes always go to canonical; reads fall back to `~/.smbseek` for per-item cache lookups and template listings.

---

## Migration Policy (exact)

The **marker file** (`~/.dirracuda/.migrated_from_smbseek`) is the primary idempotency gate — not directory existence, which can be unreliable after a partial copy.

1. Marker exists → return immediately (migration complete)
2. `~/.smbseek` exists, no marker → migrate (first attempt or retry of partial copy)
3. `~/.smbseek` absent, no marker → new install; ensure `~/.dirracuda` exists, return

Case 2 uses `shutil.copytree(..., dirs_exist_ok=True)` so it works for both fresh copies and partial-retry fill-in. Marker is written only after `copytree` succeeds.

On failure: log warning, no marker; next startup retries. Partial `~/.dirracuda` is left in place — dual-read fallbacks serve any still-missing files until retry succeeds.

`~/.smbseek` is NEVER deleted or moved.

---

## New Files

### `shared/path_migration.py`

```python
"""Idempotent user data root migration: ~/.smbseek → ~/.dirracuda."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)

_LEGACY_DIR = Path.home() / ".smbseek"
_CANONICAL_DIR = Path.home() / ".dirracuda"
_MARKER = ".migrated_from_smbseek"


def migrate_user_data_root() -> None:
    marker = _CANONICAL_DIR / _MARKER
    if marker.exists():
        return  # already migrated

    if not _LEGACY_DIR.exists():
        _CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
        return  # new install

    # Legacy present, no marker → first attempt or retry
    _copy_and_mark(_LEGACY_DIR, _CANONICAL_DIR)


def _copy_and_mark(src: Path, dst: Path) -> None:
    try:
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        (dst / _MARKER).write_text(
            f"migrated: {ts}\nsource: {src}\n", encoding="utf-8"
        )
        _logger.info("Migrated user data from %s to %s", src, dst)
    except Exception as exc:
        _logger.warning(
            "Migration copy failed (%s); partial %s left in place; will retry next startup",
            exc, dst,
        )
```

Note: uses `logging.getLogger(__name__)` — `shared/` must not import from `gui/`.

`dirs_exist_ok=True` requires Python 3.8+, which matches the project minimum.

### `shared/tests/test_path_migration.py`

Seven tests using `tmp_path` + monkeypatching `mod._LEGACY_DIR` / `mod._CANONICAL_DIR`:

1. `test_no_legacy_no_canonical` — canonical created, no marker
2. `test_legacy_exists_no_canonical` — full copy + marker written, legacy intact
3. `test_marker_exists_no_retry` — marker already present → no copy, no mtime change
4. `test_retry_partial_migration` — `~/.dirracuda` exists (partial), no marker, legacy present → `copytree` called, marker written
5. `test_no_retry_if_no_legacy` — `~/.dirracuda` exists, no marker, no legacy → no crash, no marker
6. `test_copy_failure_no_marker` — monkeypatch `shutil.copytree` to raise `OSError`; no marker, no raise
7. `test_marker_content` — marker text contains `"migrated:"` and source path

For test 4, verify that a file present in legacy but missing from canonical is present in canonical after the retry call.

---

## Files to Modify

### Migration call site

**`gui/utils/settings_manager.py`** — L38, L41–43

- Update docstring: `(default: ~/.smbseek)` → `(default: ~/.dirracuda)`
- Inside `if settings_dir is None:` block, before `self.settings_dir = ...`:

```python
        if settings_dir is None:
            from shared.path_migration import migrate_user_data_root
            migrate_user_data_root()
            home_dir = Path.home()
            self.settings_dir = home_dir / '.dirracuda'
```

Lazy import avoids any import-time circular dependency.

---

### Path constant updates

| File | Location | Change |
|------|----------|--------|
| `gui/utils/probe_cache.py` | L14 | `CACHE_DIR = Path.home() / ".dirracuda" / "probes"` |
| `gui/utils/ftp_probe_cache.py` | L12 | `FTP_CACHE_DIR = Path.home() / ".dirracuda" / "ftp_probes"` |
| `gui/utils/http_probe_cache.py` | L14 | `HTTP_CACHE_DIR = Path.home() / ".dirracuda" / "http_probes"` |
| `gui/utils/extract_runner.py` | L286 | `Path.home() / ".dirracuda" / "extract_logs"` |
| `gui/utils/template_store.py` | L25 | `TEMPLATE_DIRNAME = ".dirracuda/templates"` |
| `gui/components/server_list_window/window.py` | L172 | `Path.home() / ".dirracuda" / "filter_templates"` |
| `gui/components/server_list_window/details.py` | L1168 | `Path.home() / ".dirracuda" / "quarantine"` |
| `gui/components/server_list_window/actions/batch.py` | L437 | default fallback `~/.dirracuda/quarantine` |
| `shared/quarantine.py` | L9 | `_DEFAULT_ROOT = Path.home() / ".dirracuda" / "quarantine"` |
| `shared/config.py` | L192 | `"quarantine_root": "~/.dirracuda/quarantine"` |
| `shared/config.py` | L561, L597 | `"~/.dirracuda/logs/rce_analysis.jsonl"` |
| `shared/rce_scanner/logger.py` | L34 | `"~/.dirracuda/logs/rce_analysis.jsonl"` |
| `gui/components/unified_browser_window.py` | L100, L123 | `"quarantine_base": "~/.dirracuda/quarantine"` |
| `gui/components/unified_browser_window.py` | L153 | `"quarantine_root": "~/.dirracuda/quarantine"` |
| `gui/components/app_config_dialog.py` | L86 | `self.quarantine_path = "~/.dirracuda/quarantine"` |
| `conf/config.json.example` | L133, L156 | `~/.dirracuda/quarantine` |
| `conf/config.json.example` | L230 | `~/.dirracuda/logs/rce_analysis.jsonl` |

---

### Dual-read fallbacks

#### Probe caches — `probe_cache.py`, `ftp_probe_cache.py`, `http_probe_cache.py`

Same pattern for all three. Example for `probe_cache.py`:

```python
# Add after CACHE_DIR line:
_LEGACY_CACHE_DIR = Path.home() / ".smbseek" / "probes"
```

In `load_probe_result`, replace `if not cache_path.exists(): return None` with:

```python
    if not cache_path.exists():
        legacy = _LEGACY_CACHE_DIR / f"{_sanitize_ip(ip_address)}.json"
        if legacy.exists():
            cache_path = legacy
        else:
            return None
```

For `http_probe_cache.py`: the legacy fallback filename must be reconstructed inline (mirrors `get_http_cache_path` port logic) since the helper now targets canonical dir.

#### Template store — `gui/utils/template_store.py`

Both `list_templates` (UI discovery) and `load_template` need fallback. Filter templates use the same `TemplateStore` class with a different `base_dir`, so the fallback must be generic — derived from `self.templates_dir` rather than hardcoded to `~/.smbseek/templates`.

Add a `_legacy_dir` helper to `TemplateStore`:

```python
def _legacy_dir(self) -> Optional[Path]:
    canonical_root = Path.home() / ".dirracuda"
    try:
        rel = self.templates_dir.relative_to(canonical_root)
        return Path.home() / ".smbseek" / rel
    except ValueError:
        return None
```

This handles both `~/.dirracuda/templates` → `~/.smbseek/templates` and `~/.dirracuda/filter_templates` → `~/.smbseek/filter_templates`. Returns `None` when `templates_dir` is not under `~/.dirracuda` (e.g., test-injected path), disabling fallback cleanly.

Update `list_templates` to append legacy-only entries after the canonical loop:

```python
def list_templates(self) -> List[ScanTemplate]:
    templates: List[ScanTemplate] = []
    seen: set = set()
    for path in sorted(self.templates_dir.glob("*.json"), key=lambda p: p.name.lower()):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            name = data.get("name") or path.stem
            templates.append(ScanTemplate(name=name, slug=path.stem,
                                          saved_at=data.get("saved_at"),
                                          form_state=data.get("form_state") or {}))
            seen.add(path.stem)
        except Exception as exc:
            _logger.warning("Failed to load scan template %s: %s", path, exc)

    legacy = self._legacy_dir()
    if legacy and legacy.exists():
        for path in sorted(legacy.glob("*.json"), key=lambda p: p.name.lower()):
            if path.stem in seen:
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                name = data.get("name") or path.stem
                templates.append(ScanTemplate(name=name, slug=path.stem,
                                              saved_at=data.get("saved_at"),
                                              form_state=data.get("form_state") or {}))
            except Exception as exc:
                _logger.warning("Failed to load legacy scan template %s: %s", path, exc)

    templates.sort(key=lambda t: t.name.lower())
    return templates
```

Update `load_template` to fall back to legacy when slug not found in canonical:

```python
def load_template(self, slug: str) -> Optional[ScanTemplate]:
    path = self.templates_dir / f"{slug}.json"
    if not path.exists():
        legacy = self._legacy_dir()
        if legacy:
            candidate = legacy / f"{slug}.json"
            if candidate.exists():
                path = candidate
            else:
                return None
        else:
            return None
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
```

#### `shared/db_migrations.py` — L508 dual-read for `gui_settings.json`

```python
        settings_path = Path.home() / ".dirracuda" / "gui_settings.json"
        if not settings_path.exists():
            settings_path = Path.home() / ".smbseek" / "gui_settings.json"
        if not settings_path.exists():
            return
```

---

## Execution Order

1. `shared/path_migration.py` (new)
2. `shared/tests/test_path_migration.py` (new)
3. `gui/utils/settings_manager.py` (migration call site)
4. `gui/utils/template_store.py` (most complex; includes `_legacy_dir` helper + both list/load fallbacks)
5. Probe caches: `probe_cache.py`, `ftp_probe_cache.py`, `http_probe_cache.py`
6. All remaining path constant updates (independent of each other)

---

## Validation

```bash
# Compile-check all touched files
./venv/bin/python -m py_compile shared/path_migration.py
./venv/bin/python -m py_compile gui/utils/settings_manager.py
./venv/bin/python -m py_compile gui/utils/probe_cache.py
./venv/bin/python -m py_compile gui/utils/ftp_probe_cache.py
./venv/bin/python -m py_compile gui/utils/http_probe_cache.py
./venv/bin/python -m py_compile gui/utils/extract_runner.py
./venv/bin/python -m py_compile gui/utils/template_store.py
./venv/bin/python -m py_compile shared/quarantine.py
./venv/bin/python -m py_compile shared/db_migrations.py
./venv/bin/python -m py_compile shared/config.py
./venv/bin/python -m py_compile shared/rce_scanner/logger.py
./venv/bin/python -m py_compile gui/components/unified_browser_window.py
./venv/bin/python -m py_compile gui/components/app_config_dialog.py
./venv/bin/python -m py_compile gui/components/server_list_window/window.py
./venv/bin/python -m py_compile gui/components/server_list_window/details.py
./venv/bin/python -m py_compile gui/components/server_list_window/actions/batch.py

# Constant smoke tests
./venv/bin/python -c "from gui.utils.probe_cache import CACHE_DIR; assert '.dirracuda' in str(CACHE_DIR)"
./venv/bin/python -c "from gui.utils.ftp_probe_cache import FTP_CACHE_DIR; assert '.dirracuda' in str(FTP_CACHE_DIR)"
./venv/bin/python -c "from gui.utils.http_probe_cache import HTTP_CACHE_DIR; assert '.dirracuda' in str(HTTP_CACHE_DIR)"
./venv/bin/python -c "from shared.quarantine import _DEFAULT_ROOT; assert '.dirracuda' in str(_DEFAULT_ROOT)"
./venv/bin/python -c "from gui.utils.template_store import TEMPLATE_DIRNAME; assert '.dirracuda' in TEMPLATE_DIRNAME"

# Unit tests for migration logic
xvfb-run -a ./venv/bin/python -m pytest shared/tests/test_path_migration.py -v

# Full regression
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```

**HI tests (manual):**

1. **Existing user:** populate `~/.smbseek` with a probe JSON and a scan template; no `~/.dirracuda`; launch app → confirm `~/.dirracuda` created, `.migrated_from_smbseek` present, `~/.smbseek` intact, probe loadable and template visible in scan dialog
2. **Partial retry:** create empty `~/.dirracuda` (no marker), populate `~/.smbseek/probes/` with a file; launch → confirm copytree fills in the probe file, marker written
3. **New install:** neither dir present; launch → `~/.dirracuda` created, no marker
4. **Idempotency:** launch again after marker written → no second copy (marker mtime unchanged)
5. **Filter templates:** save a filter in the server list window; restart → filter visible in filter-template menu
6. **Template persistence:** save a scan template; restart → template visible in scan dialog

---

## Risks / Notes

- **`http_probe_cache.py` dual-read:** port-aware filename is reconstructed inline (mirrors `get_http_cache_path` logic). Keeping the helper canonical-only is intentional — writes always go to canonical.
- **User's live `conf/config.json` still has `~/.smbseek/quarantine`:** their override wins over the updated default. Quarantine data was copied by migration so the path still works. No F3 action needed.
- **Template `list_templates` re-sort:** after appending legacy entries, re-sort the full list so legacy-only templates appear in alphabetical order, not appended at the end.
- **`_legacy_dir` returns `None` for test-injected paths:** any `TemplateStore` constructed with an explicit `base_dir` outside `~/.dirracuda` silently disables fallback. Test fixtures that inject a tmp path work correctly — no legacy lookups happen.
