from __future__ import annotations

import json
from pathlib import Path

from shared.path_service import (
    bootstrap_layout_v2,
    get_legacy_paths,
    get_paths,
    read_layout_state,
    resolve_runtime_config_path,
    resolve_runtime_main_db_for_session,
    resolve_runtime_main_db_path,
    run_layout_v2_migration,
)


def _seed_repo_conf(repo_root: Path) -> None:
    conf = repo_root / "conf"
    (conf / "signatures" / "rce_smb").mkdir(parents=True, exist_ok=True)
    (conf / "wordlists").mkdir(parents=True, exist_ok=True)
    (conf / "config.json.example").write_text('{"database": {"path": "dirracuda.db"}}', encoding="utf-8")
    (conf / "exclusion_list.json").write_text('{"organizations": []}', encoding="utf-8")
    (conf / "ransomware_indicators.json").write_text('{"patterns": []}', encoding="utf-8")
    (conf / "signatures" / "rce_smb" / "sample.yaml").write_text("name: sample\n", encoding="utf-8")
    (conf / "wordlists" / ".gitkeep").write_text("", encoding="utf-8")


def test_bootstrap_layout_v2_creates_structure_and_seeds_conf(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    result = bootstrap_layout_v2(paths=paths, legacy=legacy)

    assert paths.conf_dir.exists()
    assert paths.data_dir.exists()
    assert paths.state_dir.exists()
    assert paths.logs_dir.exists()
    assert paths.config_file.exists()
    assert paths.exclusion_list_file.exists()
    assert paths.ransomware_indicators_file.exists()
    assert result["seeded"]["copied"] >= 1


def test_bootstrap_auto_enables_clamav_for_fresh_config_when_scanner_detected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    monkeypatch.setattr(
        "shared.path_service.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "clamscan" else None,
    )

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    result = bootstrap_layout_v2(paths=paths, legacy=legacy)
    seeded = json.loads(paths.config_file.read_text(encoding="utf-8"))

    assert seeded["clamav"]["enabled"] is True
    assert seeded["clamav"]["backend"] == "auto"
    assert any(
        item.get("action") == "auto_enable_clamav_if_detected"
        and item.get("status") == "ok"
        for item in result["seeded"]["items"]
    )


def test_bootstrap_preserves_existing_clamav_disabled_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    monkeypatch.setattr(
        "shared.path_service.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "clamscan" else None,
    )

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        json.dumps({"clamav": {"enabled": False, "backend": "auto"}}),
        encoding="utf-8",
    )
    legacy = get_legacy_paths(paths=paths)

    bootstrap_layout_v2(paths=paths, legacy=legacy)
    persisted = json.loads(paths.config_file.read_text(encoding="utf-8"))

    assert persisted["clamav"]["enabled"] is False


def test_bootstrap_prefers_example_over_repo_runtime_config(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    # Simulate a locally edited runtime config in checkout that should not seed users.
    repo_config = repo_root / "conf" / "config.json"
    repo_config.write_text(
        json.dumps({"database": {"path": str(repo_root / "dirracuda.db")}}),
        encoding="utf-8",
    )

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    result = bootstrap_layout_v2(paths=paths, legacy=legacy)
    seeded = json.loads(paths.config_file.read_text(encoding="utf-8"))

    assert seeded.get("database", {}).get("path") == str(paths.main_db_file.resolve(strict=False))
    assert int(result["sanitized"]["changed"]) >= 1


def test_runtime_resolvers_fall_back_when_canonical_missing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    # Canonical config missing, repo-local fallback present.
    legacy.repo_config_file.parent.mkdir(parents=True, exist_ok=True)
    legacy.repo_config_file.write_text("{}", encoding="utf-8")
    assert resolve_runtime_config_path(paths=paths, legacy=legacy) == legacy.repo_config_file

    # Canonical DB missing, flat-home fallback present.
    legacy.flat_home_root.mkdir(parents=True, exist_ok=True)
    legacy.flat_main_db_file.write_text("sqlite", encoding="utf-8")
    assert resolve_runtime_main_db_path(paths=paths, legacy=legacy) == legacy.flat_main_db_file


def test_layout_v2_migration_moves_flat_sources_and_marks_complete(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    legacy.flat_home_root.mkdir(parents=True, exist_ok=True)
    legacy.flat_main_db_file.write_text("main", encoding="utf-8")
    legacy.flat_sidecar_se_dork_file.write_text("se", encoding="utf-8")
    legacy.flat_gui_settings_file.write_text(json.dumps({"k": "v"}), encoding="utf-8")
    legacy.flat_scan_templates_dir.mkdir(parents=True, exist_ok=True)
    (legacy.flat_scan_templates_dir / "scan.json").write_text("{}", encoding="utf-8")

    result = run_layout_v2_migration(paths=paths, legacy=legacy)

    assert result["status"] == "success"
    assert paths.main_db_file.exists()
    assert paths.se_dork_db_file.exists()
    assert paths.gui_settings_file.exists()
    assert (paths.templates_scan_dir / "scan.json").exists()
    assert not legacy.flat_main_db_file.exists()

    state = read_layout_state(paths=paths)
    assert state.get("layout_version") == 2
    assert state.get("status") == "success"
    assert Path(state.get("report_file", "")).exists()


def test_layout_v2_late_legacy_db_triggers_targeted_recovery(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    first = run_layout_v2_migration(paths=paths, legacy=legacy)
    assert first["status"] == "success"
    assert first["layout_version"] == 2
    assert not paths.main_db_file.exists()

    legacy.flat_home_root.mkdir(parents=True, exist_ok=True)
    legacy.flat_main_db_file.write_text("legacy-main-db", encoding="utf-8")
    assert legacy.flat_main_db_file.exists()

    second = run_layout_v2_migration(paths=paths, legacy=legacy)
    assert second["status"] == "success"
    assert second["db_recovery_attempted"] is True
    assert second["db_recovery_status"] == "success"
    assert paths.main_db_file.exists()
    assert not legacy.flat_main_db_file.exists()

    state = read_layout_state(paths=paths)
    assert state.get("layout_version") == 2
    assert state.get("status") == "success"
    assert Path(state.get("report_file", "")).exists()

    third = run_layout_v2_migration(paths=paths, legacy=legacy)
    assert third["status"] == "already_done"
    assert third["db_recovery_attempted"] is False
    assert third["db_recovery_status"] == "not_needed"


def test_layout_v2_already_done_self_heals_stale_repo_local_db_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    first = run_layout_v2_migration(paths=paths, legacy=legacy)
    assert first["status"] == "success"

    stale_repo_db = legacy.repo_main_db_file.resolve(strict=False)
    cfg = {
        "gui_app": {"backend_path": str(repo_root), "database_path": str(stale_repo_db)},
        "database": {"path": str(stale_repo_db)},
    }
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(json.dumps(cfg), encoding="utf-8")
    assert not stale_repo_db.exists()

    second = run_layout_v2_migration(paths=paths, legacy=legacy)
    repaired = json.loads(paths.config_file.read_text(encoding="utf-8"))

    assert second["status"] == "already_done"
    assert int(second.get("sanitized", {}).get("changed", 0)) >= 1
    assert repaired.get("database", {}).get("path") == str(paths.main_db_file.resolve(strict=False))
    assert repaired.get("gui_app", {}).get("database_path") == str(paths.main_db_file.resolve(strict=False))

    third = run_layout_v2_migration(paths=paths, legacy=legacy)
    assert third["status"] == "already_done"
    assert int(third.get("sanitized", {}).get("changed", 0)) == 0


def test_session_db_fallback_uses_legacy_when_recovery_incomplete(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_root = tmp_path / ".dirracuda"
    _seed_repo_conf(repo_root)

    paths = get_paths(home_root=home_root, repo_root=repo_root)
    legacy = get_legacy_paths(paths=paths)

    legacy.flat_home_root.mkdir(parents=True, exist_ok=True)
    legacy.flat_main_db_file.write_text("legacy-main-db", encoding="utf-8")

    effective, warning = resolve_runtime_main_db_for_session(
        paths.main_db_file,
        migration_result={
            "status": "partial",
            "db_recovery_attempted": True,
            "db_recovery_status": "failed",
            "db_fallback_candidates": [str(legacy.flat_main_db_file)],
        },
        paths=paths,
        legacy=legacy,
    )

    assert effective == legacy.flat_main_db_file.resolve(strict=False)
    assert warning is not None
    assert "retry migration on next startup" in warning.lower()

    effective2, warning2 = resolve_runtime_main_db_for_session(
        paths.main_db_file,
        migration_result={
            "status": "partial",
            "db_recovery_attempted": False,
            "db_recovery_status": "not_attempted",
            "db_fallback_candidates": [str(legacy.flat_main_db_file)],
        },
        paths=paths,
        legacy=legacy,
    )
    assert effective2 == legacy.flat_main_db_file.resolve(strict=False)
    assert warning2 is not None
