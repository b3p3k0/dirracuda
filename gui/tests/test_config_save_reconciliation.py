import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_xsmbseek_config_class():
    loader = SourceFileLoader("xsmbseek_mod_cfg_reconcile", str(Path("dirracuda").resolve()))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.XSMBSeekConfig


def test_save_config_preserves_runtime_shodan_key_updates(tmp_path: Path) -> None:
    cfg_path = tmp_path / "conf" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "shodan": {"api_key": ""},
                "gui_app": {"backend_path": str(tmp_path)},
                "database": {"path": str(tmp_path / "dirracuda.db")},
            }
        ),
        encoding="utf-8",
    )

    XSMBSeekConfig = _load_xsmbseek_config_class()
    cfg = XSMBSeekConfig(str(cfg_path))

    # Simulate runtime writer (dashboard API key prompt persistence).
    runtime_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    runtime_data.setdefault("shodan", {})["api_key"] = "PROMPT_KEY"
    cfg_path.write_text(json.dumps(runtime_data, indent=2) + "\n", encoding="utf-8")

    assert cfg.save_config() is True
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert persisted["shodan"]["api_key"] == "PROMPT_KEY"


def test_save_config_merges_managed_fields_without_clobbering_other_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "conf" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "shodan": {"api_key": "ORIGINAL"},
                "ftp": {"shodan": {"query_components": {"base_query": "legacy ftp query"}}},
                "gui_app": {"backend_path": str(tmp_path), "database_path": str(tmp_path / "dirracuda.db")},
                "database": {"path": str(tmp_path / "dirracuda.db")},
            }
        ),
        encoding="utf-8",
    )

    XSMBSeekConfig = _load_xsmbseek_config_class()
    cfg = XSMBSeekConfig(str(cfg_path))

    # Managed updates (owned by XSMBSeekConfig).
    new_backend = tmp_path / "backend_new"
    new_backend.mkdir(parents=True, exist_ok=True)
    new_db = tmp_path / "db" / "next.db"
    cfg.set_smbseek_path(str(new_backend))
    cfg.set_database_path(str(new_db))

    # Simulate external runtime updates to non-managed keys.
    runtime_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    runtime_data.setdefault("shodan", {})["api_key"] = "RUNTIME_KEY"
    runtime_data.setdefault("ftp", {}).setdefault("shodan", {}).setdefault("query_components", {})[
        "base_query"
    ] = "runtime ftp query"
    runtime_data["runtime_marker"] = {"preserve": True}
    cfg_path.write_text(json.dumps(runtime_data, indent=2) + "\n", encoding="utf-8")

    assert cfg.save_config() is True
    persisted = json.loads(cfg_path.read_text(encoding="utf-8"))

    # External runtime keys must survive.
    assert persisted["shodan"]["api_key"] == "RUNTIME_KEY"
    assert persisted["ftp"]["shodan"]["query_components"]["base_query"] == "runtime ftp query"
    assert persisted["runtime_marker"] == {"preserve": True}

    # Managed keys must reflect in-memory overlay.
    assert persisted["gui_app"]["backend_path"] == str(new_backend)
    assert persisted["gui_app"]["database_path"] == str(new_db.resolve(strict=False))
    assert persisted["database"]["path"] == str(new_db.resolve(strict=False))
