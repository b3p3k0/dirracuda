from __future__ import annotations

import json
from pathlib import Path

from shared.config_store import ConfigStore
from shared.path_service import get_legacy_paths, get_paths


def _seed_repo_defaults(repo_root: Path) -> None:
    conf = repo_root / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    payload = {
        "shodan": {"api_key": ""},
        "workflow": {"rescan_after_days": 30},
        "connection": {"timeout": 30},
        "discovery": {"max_concurrent_hosts": 10},
        "access": {"max_concurrent_hosts": 1},
        "ftp": {"shodan": {"query_components": {"base_query": "port:21"}}},
        "http": {"shodan": {"query_components": {"base_query": "http.title:\"Index of /\""}}},
        "database": {"path": "~/.dirracuda/data/dirracuda.db"},
        "file_collection": {"max_files_per_target": 3},
        "file_browser": {"allow_smb1": True},
        "ftp_browser": {"enabled": True},
        "http_browser": {"enabled": True},
        "quarantine": {"use_tmpfs": False},
        "clamav": {"enabled": False},
        "security": {"exclusion_file": "~/.dirracuda/conf/exclusion_list.json"},
        "censys": {"personal_access_token": ""},
        "output": {"verbose_by_default": False},
        "se_dork": {"instance_url": "http://example"},
        "reddit_grab": {"mode": "feed"},
        "dorkbook": {"active_protocol_tab": "SMB"},
        "keymaster": {"auto_check_query_credits": True},
        "webui": {"enabled": False},
    }
    (conf / "config.json.example").write_text(json.dumps(payload), encoding="utf-8")


def test_migration_splits_legacy_main_and_prefs_into_shards(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_defaults(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)
    store = ConfigStore(paths=paths, legacy=legacy)

    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        json.dumps(
            {
                "shodan": {"api_key": "ABC123"},
                "database": {"path": str(home_root / "data" / "custom.db")},
                "se_dork": {"instance_url": "https://search.local"},
            }
        ),
        encoding="utf-8",
    )
    paths.gui_settings_file.parent.mkdir(parents=True, exist_ok=True)
    paths.gui_settings_file.write_text(
        json.dumps(
            {
                "interface": {"mode": "advanced"},
                "se_dork": {"max_results": 88},
            }
        ),
        encoding="utf-8",
    )

    result = store.ensure_migrated()
    assert result.status in {"success", "already_modular"}

    scan_shard = json.loads((store.conf_d_dir / "core" / "scan.json").read_text(encoding="utf-8"))
    storage_shard = json.loads((store.conf_d_dir / "core" / "storage.json").read_text(encoding="utf-8"))
    se_dork_shard = json.loads((store.conf_d_dir / "experimental" / "se_dork.json").read_text(encoding="utf-8"))
    prefs_shard = json.loads((store.conf_d_dir / "prefs" / "user-prefs.json").read_text(encoding="utf-8"))

    assert scan_shard["shodan"]["api_key"] == "ABC123"
    assert storage_shard["database"]["path"] == str(home_root / "data" / "custom.db")
    assert se_dork_shard["se_dork"]["instance_url"] == "https://search.local"
    assert se_dork_shard["se_dork"]["max_results"] == 88
    assert prefs_shard["interface"]["mode"] == "advanced"
    assert "se_dork" not in prefs_shard


def test_update_sections_writes_owner_shard_and_composed_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_defaults(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)
    store = ConfigStore(paths=paths, legacy=legacy)

    store.ensure_migrated()
    owners = store.update_sections({"shodan": {"api_key": "XYZ"}})
    assert owners["shodan"] == "core.scan"

    scan_shard = json.loads((store.conf_d_dir / "core" / "scan.json").read_text(encoding="utf-8"))
    assert scan_shard["shodan"]["api_key"] == "XYZ"

    composed = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert composed["shodan"]["api_key"] == "XYZ"


def test_load_runtime_config_prefers_shards_over_legacy_file(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_defaults(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)
    store = ConfigStore(paths=paths, legacy=legacy)

    store.ensure_migrated()
    (store.conf_d_dir / "core" / "scan.json").write_text(
        json.dumps({"shodan": {"api_key": "FROM_SHARD"}}),
        encoding="utf-8",
    )
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        json.dumps({"shodan": {"api_key": "FROM_LEGACY"}}),
        encoding="utf-8",
    )

    cfg, warning = store.load_runtime_config()
    assert warning is None or isinstance(warning, str)
    assert cfg["shodan"]["api_key"] == "FROM_SHARD"


def test_user_prefs_and_module_prefs_helpers_round_trip(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_defaults(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)
    store = ConfigStore(paths=paths, legacy=legacy)

    store.save_user_prefs({"interface": {"mode": "simple"}})
    store.save_module_prefs("webui", {"enabled": True, "port": 2600})

    assert store.load_user_prefs()["interface"]["mode"] == "simple"
    webui = store.load_module_prefs("webui")
    assert webui["enabled"] is True
    assert webui["port"] == 2600
