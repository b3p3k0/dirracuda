import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

from gui.utils.settings_manager import SettingsManager
from shared.db_path_resolution import resolve_database_path
from shared.path_service import get_paths


def _load_xsmbseek_config_class():
    loader = SourceFileLoader("xsmbseek_mod_test", str(Path("dirracuda").resolve()))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.XSMBSeekConfig


def test_db_resolution_precedence_and_absolute_sync(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    (backend / "conf").mkdir(parents=True)
    (backend / "nested").mkdir(parents=True)
    (backend / "dirracuda.db").touch()
    (backend / "smbseek.db").touch()

    cfg_path = backend / "conf" / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "gui_app": {"backend_path": str(backend), "database_path": None},
                "database": {"path": "dirracuda.db"},
            }
        ),
        encoding="utf-8",
    )

    XSMBSeekConfig = _load_xsmbseek_config_class()
    cfg = XSMBSeekConfig(str(cfg_path))
    sm = SettingsManager(settings_dir=str(tmp_path / "settings"))
    sm.set_backend_path(str(backend), validate=False)
    sm.set_setting("backend.config_path", str(cfg_path))
    sm.set_setting("backend.last_database_path", str(backend / "stale_parent" / "stale.db"))
    sm.set_setting("backend.database_path", "nested/my.db")

    resolved = resolve_database_path(
        backend_path=backend,
        cli_database_path="custom.db",
        persisted_paths=[
            sm.get_setting("backend.last_database_path"),
            sm.get_setting("backend.database_path"),
            cfg.config.get("gui_app", {}).get("database_path"),
            cfg.config.get("database", {}).get("path"),
        ],
    )
    expected_cli = (backend / "custom.db").resolve(strict=False)
    assert resolved == expected_cli

    resolved_str = str(resolved)
    cfg.set_database_path(resolved_str)
    assert cfg.save_config() is True
    assert sm.set_database_path(resolved_str, validate=False) is True

    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted["gui_app"]["database_path"] == resolved_str
    assert persisted["database"]["path"] == resolved_str
    assert sm.get_setting("backend.database_path") == resolved_str
    assert sm.get_setting("backend.last_database_path") == resolved_str


def test_relative_and_stale_persisted_rules(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir(parents=True)
    (backend / "nested").mkdir()
    (backend / "dirracuda.db").touch()

    # Relative persisted path must be backend-rooted and exact.
    relative = resolve_database_path(
        backend_path=backend,
        cli_database_path=None,
        persisted_paths=["nested/my.db"],
    )
    assert relative == (backend / "nested" / "my.db").resolve(strict=False)

    # Stale persisted path (missing parent) must fall through to canonical auto-detect.
    stale = resolve_database_path(
        backend_path=backend,
        cli_database_path=None,
        persisted_paths=[str(backend / "missing_parent" / "stale.db")],
    )
    assert stale == get_paths().main_db_file.resolve(strict=False)
