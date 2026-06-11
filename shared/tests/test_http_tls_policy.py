"""C3 — canonical HTTP TLS policy: accessor, coercion, resolver, and migration.

No network access; uses tmp ConfigStore/SMBSeekConfig instances. Mirrors the
shard-seeding pattern in test_config_store.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import config as config_mod
from shared.config import (
    SMBSeekConfig,
    _coerce_tls_bool,
    ensure_http_tls_policy_migrated,
    resolve_http_allow_insecure_tls,
)
from shared.config_store import ConfigStore
from shared.path_service import get_legacy_paths, get_paths

_MIGRATION = config_mod._HTTP_TLS_MIGRATION


def _seed_repo_defaults(repo_root: Path) -> None:
    conf = repo_root / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    payload = {
        "shodan": {"api_key": ""},
        "http": {
            "shodan": {"query_components": {"base_query": "http.title:\"Index of /\""}},
            "verification": {
                "connect_timeout": 5,
                "request_timeout": 10,
                "subdir_timeout": 8,
                "allow_insecure_tls": True,
                "verify_http": True,
                "verify_https": True,
            },
        },
        "database": {"path": "~/.dirracuda/data/dirracuda.db"},
    }
    (conf / "config.json.example").write_text(json.dumps(payload), encoding="utf-8")


def _store(tmp_path: Path) -> ConfigStore:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_defaults(repo_root)
    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)
    return ConfigStore(paths=paths, legacy=legacy)


def _scan_shard(store: ConfigStore) -> dict:
    return json.loads((store.conf_d_dir / "core" / "scan.json").read_text(encoding="utf-8"))


def _set_shard_tls(store: ConfigStore, value) -> None:
    raw = store.read_shard_payload("core.scan") or {}
    raw.setdefault("http", {}).setdefault("verification", {})["allow_insecure_tls"] = value
    store.update_sections({"http": raw["http"]})


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,default,expected",
    [
        (True, True, True),
        (False, True, False),
        (None, True, True),
        (None, False, False),
        ("true", False, True),
        ("FALSE", True, False),
        ("1", False, True),
        ("0", True, False),
        ("yes", False, True),
        ("off", True, False),
        ("maybe", True, True),    # malformed -> default
        ("garbage", False, False),
        (object(), True, True),
    ],
)
def test_coerce_tls_bool(value, default, expected):
    assert _coerce_tls_bool(value, default) is expected


# ---------------------------------------------------------------------------
# Accessor
# ---------------------------------------------------------------------------

def test_accessor_reads_explicit_file_value(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"http": {"verification": {"allow_insecure_tls": False}}}),
        encoding="utf-8",
    )
    cfg = SMBSeekConfig(config_file=str(cfg_file))
    assert cfg.get_http_allow_insecure_tls() is False


def test_accessor_defaults_true_when_absent(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"http": {"verification": {}}}), encoding="utf-8")
    cfg = SMBSeekConfig(config_file=str(cfg_file))
    assert cfg.get_http_allow_insecure_tls() is True


def test_accessor_handles_non_dict_verification(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"http": {"verification": "oops"}}), encoding="utf-8")
    cfg = SMBSeekConfig(config_file=str(cfg_file))
    assert cfg.get_http_allow_insecure_tls() is True


def test_accessor_coerces_string(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"http": {"verification": {"allow_insecure_tls": "false"}}}),
        encoding="utf-8",
    )
    cfg = SMBSeekConfig(config_file=str(cfg_file))
    assert cfg.get_http_allow_insecure_tls() is False


# ---------------------------------------------------------------------------
# read_shard_payload
# ---------------------------------------------------------------------------

def test_read_shard_payload_absent_vs_malformed(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    # Valid after migration.
    assert isinstance(store.read_shard_payload("core.scan"), dict)
    # Malformed -> None.
    (store.conf_d_dir / "core" / "scan.json").write_text("{not json", encoding="utf-8")
    assert store.read_shard_payload("core.scan") is None
    # Unknown shard -> {}.
    assert store.read_shard_payload("core.nope") == {}


# ---------------------------------------------------------------------------
# Migration precedence (canonical-present wins; absent -> migrate + persist)
# ---------------------------------------------------------------------------

def test_present_true_canonical_wins_over_legacy_false(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()  # shard now carries synthesized allow_insecure_tls: true
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is True
    assert store.is_migration_done(_MIGRATION)


def test_pre_modular_upgrade_does_not_treat_repo_default_as_explicit(tmp_path):
    store = _store(tmp_path)
    store.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    store.paths.config_file.write_text(
        json.dumps({"http": {"verification": {}}}),
        encoding="utf-8",
    )
    store.paths.gui_settings_file.parent.mkdir(parents=True, exist_ok=True)
    store.paths.gui_settings_file.write_text(
        json.dumps({"unified_scan_dialog": {"allow_insecure_tls": False}}),
        encoding="utf-8",
    )

    ensure_http_tls_policy_migrated(store)

    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False
    assert store.is_migration_done(_MIGRATION)


def test_pre_modular_upgrade_without_runtime_config_uses_legacy_preference(tmp_path):
    store = _store(tmp_path)
    store.paths.gui_settings_file.parent.mkdir(parents=True, exist_ok=True)
    store.paths.gui_settings_file.write_text(
        json.dumps({"unified_scan_dialog": {"allow_insecure_tls": False}}),
        encoding="utf-8",
    )

    ensure_http_tls_policy_migrated(store)

    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False


def test_present_false_canonical_wins(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls(store, False)
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": True}})
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False


def test_absent_migrates_from_unified(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False


def test_absent_migrates_from_http_when_unified_missing(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    store.save_user_prefs({"http_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False


def test_absent_no_legacy_persists_true_fallback(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    ensure_http_tls_policy_migrated(store)
    verif = _scan_shard(store)["http"]["verification"]
    assert "allow_insecure_tls" in verif
    assert verif["allow_insecure_tls"] is True


def test_absent_non_dict_verification_normalized(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    raw = store.read_shard_payload("core.scan")
    raw["http"]["verification"] = "oops"  # malformed, no allow_insecure_tls key
    store.update_sections({"http": raw["http"]})
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    verif = _scan_shard(store)["http"]["verification"]
    assert isinstance(verif, dict)
    assert verif["allow_insecure_tls"] is False


def test_idempotent_marker_blocks_re_migration(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is False
    # Later App Config sets true; stale legacy false must not re-override.
    _set_shard_tls(store, True)
    ensure_http_tls_policy_migrated(store)
    assert _scan_shard(store)["http"]["verification"]["allow_insecure_tls"] is True


def test_malformed_shard_aborts_without_marker(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    (store.conf_d_dir / "core" / "scan.json").write_text("{bad", encoding="utf-8")
    ensure_http_tls_policy_migrated(store)
    assert not store.is_migration_done(_MIGRATION)


def test_failed_modularization_does_not_mark(tmp_path, monkeypatch):
    store = _store(tmp_path)

    class _Res:
        status = "failed"

    monkeypatch.setattr(store, "ensure_migrated", lambda: _Res())
    ensure_http_tls_policy_migrated(store)
    assert not store.is_migration_done(_MIGRATION)


def test_legacy_prefs_keys_remain_on_disk(tmp_path):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    ensure_http_tls_policy_migrated(store)
    prefs = store.load_user_prefs()
    assert prefs["unified_scan_dialog"]["allow_insecure_tls"] is False


# ---------------------------------------------------------------------------
# Store-mode wiring + resolver dispatch
# ---------------------------------------------------------------------------

def test_store_mode_smbseekconfig_runs_migration(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.ensure_migrated()
    _set_shard_tls_absent(store)
    store.save_user_prefs({"unified_scan_dialog": {"allow_insecure_tls": False}})
    monkeypatch.setattr(config_mod, "get_config_store", lambda *a, **k: store)
    cfg = SMBSeekConfig()  # store mode (no explicit file)
    assert cfg.get_http_allow_insecure_tls() is False
    assert "allow_insecure_tls" in _scan_shard(store)["http"]["verification"]


def test_resolver_dispatch_modes(tmp_path, monkeypatch):
    seen = {}

    class _Fake:
        def __init__(self, config_file=None):
            seen["config_file"] = config_file

        def get_http_allow_insecure_tls(self):
            return True

    monkeypatch.setattr(config_mod, "SMBSeekConfig", _Fake)
    canonical = str(config_mod._PATHS.config_file)
    resolve_http_allow_insecure_tls(canonical)
    assert seen["config_file"] is None
    resolve_http_allow_insecure_tls(None)
    assert seen["config_file"] is None
    other = str(tmp_path / "custom.json")
    resolve_http_allow_insecure_tls(other)
    assert seen["config_file"] == str(Path(other).expanduser().resolve(strict=False))


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _set_shard_tls_absent(store: ConfigStore) -> None:
    raw = store.read_shard_payload("core.scan") or {}
    http = raw.get("http") or {}
    verif = http.get("verification") or {}
    verif.pop("allow_insecure_tls", None)
    http["verification"] = verif
    raw["http"] = http
    store.update_sections({"http": raw["http"]})
