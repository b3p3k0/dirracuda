from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import shared.config as shared_config


class _DummyConfig:
    def __init__(self, db_path: str):
        self._db_path = db_path

    def get_database_path(self) -> str:
        return self._db_path


def _load_cli_module(module_name: str, rel_path: str):
    loader = SourceFileLoader(module_name, str(Path(rel_path).resolve()))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_cli_preflight_db_override_is_backend_rooted_and_consistent(monkeypatch):
    monkeypatch.setattr(shared_config, "load_config", lambda *_args, **_kwargs: _DummyConfig("dirracuda.db"))

    modules = [
        _load_cli_module("smbseek_cli_test", "cli/smbseek.py"),
        _load_cli_module("ftpseek_cli_test", "cli/ftpseek.py"),
        _load_cli_module("httpseek_cli_test", "cli/httpseek.py"),
    ]

    overrides = []
    for mod in modules:
        monkeypatch.setattr(mod, "run_layout_v2_migration", lambda **_kwargs: {"status": "already_done"})
        monkeypatch.setattr(
            mod,
            "resolve_runtime_main_db_for_session",
            lambda preferred_path, **_kwargs: (Path(preferred_path).resolve(strict=False), None),
        )
        overrides.append(mod._prepare_runtime_db_override(None))

    expected = str((modules[0]._PATHS.repo_root / "dirracuda.db").resolve(strict=False))
    assert overrides == [expected, expected, expected]
