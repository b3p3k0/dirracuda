"""Saved defaults must survive restarts and unrelated preference saves."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from experimental.dorkbook import defaults
from shared.config_store import ConfigStore
from shared.path_service import get_paths, get_legacy_paths


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "shodan": {"api_key": "keep", "query_components": {"base_query": "original smb"}},
        "ftp": {"shodan": {"query_components": {"base_query": "original ftp"}}},
        "http": {"shodan": {"query_components": {"base_query": "original http"}}},
        "se_dork": {"instance_url": "https://search.example", "max_results": 25},
    }))
    return path


def test_four_destinations_roundtrip_without_overwriting_neighbors(config_path):
    before = defaults.read_defaults(config_path)
    for provider, protocol, key in (
        ("shodan", "SMB", "shodan:SMB"), ("shodan", "FTP", "shodan:FTP"),
        ("shodan", "HTTP", "shodan:HTTP"), ("self_hosted", None, "self_hosted"),
    ):
        query = f"query for {key}"
        assert defaults.apply_default(provider, protocol, f" {query} ", config_path) == query
        after = defaults.read_defaults(config_path)
        assert after == {**before, key: query}
        before = after
    saved = json.loads(config_path.read_text())
    assert saved["shodan"]["api_key"] == "keep"
    assert saved["se_dork"]["instance_url"] == "https://search.example"
    assert saved["se_dork"]["max_results"] == 25


def test_legacy_import_once_and_explicit_blank_wins(config_path):
    assert defaults.read_defaults(config_path, legacy_query=" old query ")["self_hosted"] == "old query"
    assert defaults.read_defaults(config_path, legacy_query="stale")["self_hosted"] == "old query"
    defaults.apply_default("self_hosted", None, "new", config_path)
    assert defaults.read_defaults(config_path, legacy_query="stale")["self_hosted"] == "new"
    data = json.loads(config_path.read_text())
    data["se_dork"]["default_query"] = ""
    config_path.write_text(json.dumps(data))
    assert defaults.read_defaults(config_path, legacy_query="stale")["self_hosted"] == ""


def test_no_new_default_or_foreign_legacy_import(config_path, monkeypatch):
    monkeypatch.setattr(defaults, "_legacy_query", lambda: pytest.fail("foreign profile import"))
    before = config_path.read_bytes()
    assert defaults.read_defaults(config_path)["self_hosted"] == ""
    assert config_path.read_bytes() == before


@pytest.mark.parametrize("provider,protocol,query", [
    ("self_hosted", "HTTP", "query"), ("shodan", None, "query"),
    ("searxng", None, "query"), ("shodan", "SMB", " "),
    ("shodan", "SMB", 123), ("self_hosted", None, None),
])
def test_invalid_input_never_writes(config_path, provider, protocol, query):
    before = config_path.read_bytes()
    with pytest.raises(ValueError):
        defaults.apply_default(provider, protocol, query, config_path)
    assert config_path.read_bytes() == before


def test_save_failure_is_not_success(monkeypatch):
    config = SimpleNamespace(get=lambda section, default=None: {}, update_sections=lambda updates: False)
    monkeypatch.setattr(defaults, "_load", lambda path: config)
    with pytest.raises(OSError, match="not applied"):
        defaults.apply_default("self_hosted", None, "query")
    with pytest.raises(OSError, match="import"):
        defaults.read_defaults(legacy_query="legacy")


@pytest.mark.parametrize("section", [[], {"default_query": 3}, {"default_query": None}])
def test_malformed_selfhost_default_is_not_coerced(monkeypatch, section):
    config = SimpleNamespace(get=lambda key, default=None: section if key == "se_dork" else {})
    monkeypatch.setattr(defaults, "_load", lambda path: config)
    with pytest.raises(ValueError):
        defaults.read_defaults()


def test_invalid_nested_state_is_not_silently_replaced(monkeypatch):
    config = SimpleNamespace(get=lambda section, default=None: {"shodan": []})
    monkeypatch.setattr(defaults, "_load", lambda path: config)
    with pytest.raises(ValueError, match="object"):
        defaults.apply_default("shodan", "FTP", "query")


def test_canonical_filename_uses_shards(monkeypatch, tmp_path):
    canonical = tmp_path / "config.json"
    monkeypatch.setattr(defaults, "get_paths", lambda: SimpleNamespace(config_file=canonical))
    calls = []
    monkeypatch.setattr(defaults, "load_config", lambda path, strict: calls.append(path) if strict else pytest.fail("not strict"))
    defaults._load(canonical)
    defaults._load(None)
    defaults._load(tmp_path / "other.json")
    assert calls == [None, None, str(tmp_path / "other.json")]


def test_canonical_import_reads_legacy_preference(monkeypatch):
    data = {"se_dork": {"instance_url": "keep"}}
    def update(updates):
        data.update(deepcopy(updates))
        return True
    config = SimpleNamespace(get=lambda section, default=None: data.get(section, default), update_sections=update)
    monkeypatch.setattr(defaults, "_load", lambda path: config)
    monkeypatch.setattr(defaults, "get_config_store", lambda: SimpleNamespace(
        load_user_prefs=lambda: {"unified_scan_dialog": {"searxng_query": "saved old"}}))
    assert defaults.read_defaults()["self_hosted"] == "saved old"
    assert data["se_dork"] == {"instance_url": "keep", "default_query": "saved old"}


def test_refresh_preserves_manual_query():
    assert defaults.reconcile_query("old", "old", "new") == "new"
    assert defaults.reconcile_query("manual", "old", "new") == "manual"
    assert defaults.reconcile_query("", "old", "new") == ""


def test_stale_gui_snapshot_cannot_undo_applied_default(tmp_path):
    from gui.utils.settings_manager import SettingsManager
    paths = get_paths(home_root=tmp_path / "home", repo_root=tmp_path / "repo")
    store = ConfigStore(paths=paths, legacy=get_legacy_paths(paths=paths))
    store.save_module_prefs("se_dork", {"default_query": "applied", "max_results": 25})
    manager = SettingsManager.__new__(SettingsManager)
    manager._config_store = store
    manager._save_modular_settings({"se_dork": {"default_query": "stale", "max_results": 50}})
    assert store.load_module_prefs("se_dork") == {"default_query": "applied", "max_results": 50}
    manager._save_modular_settings({"se_dork": {"max_results": 60}})
    assert store.load_module_prefs("se_dork")["default_query"] == "applied"


@pytest.mark.parametrize("payload", ['{"shodan":', '[]', 'null'])
def test_corrupt_explicit_config_is_never_replaced(tmp_path, payload):
    path = tmp_path / "broken.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        defaults.apply_default("shodan", "HTTP", "query", path)
    assert path.read_text() == payload


def test_corrupt_canonical_shard_is_never_replaced(tmp_path, monkeypatch):
    import shared.config as config_module
    paths = get_paths(home_root=tmp_path / "home", repo_root=tmp_path / "repo")
    store = ConfigStore(paths=paths, legacy=get_legacy_paths(paths=paths))
    store.ensure_dirs()
    shard = store.module_prefs_path("se_dork")
    shard.write_text('{"se_dork":')
    monkeypatch.setattr(config_module, "_PATHS", paths)
    monkeypatch.setattr(config_module, "_LEGACY", get_legacy_paths(paths=paths))
    monkeypatch.setattr(config_module, "get_config_store", lambda **kwargs: store)
    monkeypatch.setattr(defaults, "get_paths", lambda: paths)
    with pytest.raises(ValueError):
        defaults.apply_default("self_hosted", None, "query")
    assert shard.read_text() == '{"se_dork":'
    assert not paths.config_file.exists()


def test_selfhost_apply_respects_existing_web_query_length_limit(config_path):
    before = config_path.read_bytes()
    with pytest.raises(ValueError, match="500"):
        defaults.apply_default("self_hosted", None, "q" * 501, config_path)
    assert config_path.read_bytes() == before


@pytest.mark.parametrize("query", [123, [], None, {"unexpected": "data"}])
def test_shodan_query_type_is_not_silently_coerced(config_path, query):
    data = json.loads(config_path.read_text())
    data["shodan"]["query_components"]["base_query"] = query
    config_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="text"):
        defaults.read_defaults(config_path)


def test_gui_save_keeps_corrupt_selfhost_shard_for_recovery(tmp_path):
    from gui.utils.settings_manager import SettingsManager
    paths = get_paths(home_root=tmp_path / "home", repo_root=tmp_path / "repo")
    store = ConfigStore(paths=paths, legacy=get_legacy_paths(paths=paths))
    store.ensure_dirs()
    path = store.module_prefs_path("se_dork")
    path.write_text('{"se_dork":')
    manager = SettingsManager.__new__(SettingsManager)
    manager._config_store = store
    with pytest.raises(ValueError):
        manager._save_modular_settings({"se_dork": {"max_results": 50}})
    assert path.read_text() == '{"se_dork":'


def test_canonical_shards_restart_and_cached_gui_save(tmp_path, monkeypatch):
    import shared.config as config_module
    from gui.utils.settings_manager import SettingsManager
    paths = get_paths(home_root=tmp_path / "home", repo_root=tmp_path / "repo")
    store = ConfigStore(paths=paths, legacy=get_legacy_paths(paths=paths))
    store.update_sections({"se_dork": {"instance_url": "https://owned.example"}})
    store.save_user_prefs({"unified_scan_dialog": {"searxng_query": "legacy web query"}})
    monkeypatch.setattr(config_module, "_PATHS", paths)
    monkeypatch.setattr(config_module, "_LEGACY", get_legacy_paths(paths=paths))
    monkeypatch.setattr(config_module, "get_config_store", lambda **kwargs: store)
    monkeypatch.setattr(defaults, "get_config_store", lambda: store)
    monkeypatch.setattr(defaults, "get_paths", lambda: paths)
    assert defaults.read_defaults(paths.config_file)["self_hosted"] == "legacy web query"
    snapshot = store.load_module_prefs("se_dork")
    defaults.apply_default("self_hosted", None, "applied web query", paths.config_file)
    defaults.apply_default("shodan", "HTTP", "applied http query", paths.config_file)
    manager = SettingsManager.__new__(SettingsManager)
    manager._config_store = store
    manager._save_modular_settings({"se_dork": snapshot})
    fresh = defaults.read_defaults(paths.config_file)
    assert fresh["self_hosted"] == "applied web query"
    assert fresh["shodan:HTTP"] == "applied http query"
    assert store.load_module_prefs("se_dork")["instance_url"] == "https://owned.example"
    assert json.loads(paths.config_file.read_text())["se_dork"]["default_query"] == "applied web query"
