from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from experimental.analyst.manifest import (
    ManifestError,
    list_extraction_manifests,
    load_extraction_manifest,
)
from experimental.analyst.service import create_manifest_run
from shared.extract_manifest import ExtractSummaryReference, ExtractSummarySource


RUN_ID = "d" * 32


def _summary(paths: list[Path], *, ip: str = "203.0.113.7") -> dict:
    return {
        "ip_address": ip,
        "started_at": "2026-08-16T12:00:00Z",
        "finished_at": "2026-08-16T12:01:00Z",
        "files": [{"saved_to": str(path)} for path in paths],
        "totals": {
            "files_downloaded": len(paths),
            "bytes_downloaded": sum(path.stat().st_size for path in paths),
        },
    }


def _main_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE extract_run_summaries("
            "id INTEGER PRIMARY KEY,ip_address TEXT NOT NULL,host_type TEXT,"
            "protocol_server_id INTEGER,port INTEGER,summary_json TEXT NOT NULL,"
            "source TEXT,created_at TEXT,files_downloaded INTEGER)"
        )
        conn.executemany(
            "INSERT INTO extract_run_summaries VALUES(?,?,?,?,?,?,?,?,?)", rows,
        )
        conn.commit()
    finally:
        conn.close()


def _db_row(row_id: int, summary: dict, *, ip: str = "203.0.113.7") -> tuple:
    return (
        row_id, ip, "S", 41, 445,
        json.dumps(summary, separators=(",", ":")),
        "extract_runner", f"2026-08-16 12:0{row_id}:00", len(summary["files"]),
    )


def test_structured_reference_is_exact_and_hides_fallback_path(tmp_path: Path) -> None:
    primary = ExtractSummaryReference(9, None, ExtractSummarySource.PRIMARY_DB)
    fallback = ExtractSummaryReference(
        None, (tmp_path / "private-marker.json").absolute(),
        ExtractSummarySource.FALLBACK_JSON,
    )
    assert primary.display_token == "extract summary row 9"
    assert "private-marker" not in repr(fallback)
    assert fallback.display_token == "extract summary file private-marker.json"
    with pytest.raises(ValueError):
        ExtractSummaryReference(9, tmp_path / "x", ExtractSummarySource.PRIMARY_DB)
    with pytest.raises(ValueError):
        ExtractSummaryReference(None, Path("relative"), ExtractSummarySource.FALLBACK_JSON)
    with pytest.raises(ValueError):
        ExtractSummaryReference(
            None, Path("/tmp/../private.json"), ExtractSummarySource.FALLBACK_JSON,
        )


def test_database_reference_uses_exact_row_and_only_saved_paths(tmp_path: Path) -> None:
    root = tmp_path / "quarantine"
    root.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    unrelated = root / "unrelated.txt"
    first.write_text("first public body", encoding="utf-8")
    second.write_text("second public body", encoding="utf-8")
    unrelated.write_text("must not enter inventory", encoding="utf-8")
    db = tmp_path / "main.db"
    _main_db(db, [
        _db_row(1, _summary([first])),
        _db_row(2, _summary([second])),
    ])

    manifest = load_extraction_manifest(
        ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB),
        main_db_path=db.absolute(),
    )
    assert manifest.reference.db_row_id == 1
    assert [item.relative_path for item in manifest.inventory.files] == ["first.txt"]
    assert not manifest.inventory.exclusions
    assert "first.txt" not in repr(manifest)
    assert str(root) not in repr(manifest)
    assert "unrelated.txt" not in {
        item.relative_path for item in manifest.inventory.files
    }


def test_manifest_list_retains_ordered_exact_row_references(tmp_path: Path) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    db = tmp_path / "main.db"
    _main_db(db, [
        _db_row(4, _summary([item])),
        _db_row(8, _summary([item])),
    ])
    choices = list_extraction_manifests(db.absolute())
    assert [choice.reference.db_row_id for choice in choices] == [8, 4]
    assert all("203.0.113.7" not in repr(choice) for choice in choices)
    assert "row 8" in choices[0].display_label


def test_database_schema_and_identity_drift_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "bad.db"
    sqlite3.connect(db).close()
    reference = ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB)
    with pytest.raises(ManifestError):
        load_extraction_manifest(reference, main_db_path=db.absolute())

    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    db.unlink()
    _main_db(db, [_db_row(1, _summary([item], ip="198.51.100.5"))])
    with pytest.raises(ManifestError):
        load_extraction_manifest(reference, main_db_path=db.absolute())


def test_database_row_file_count_contradiction_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    db = tmp_path / "main.db"
    row = list(_db_row(1, _summary([item])))
    row[-1] = 2
    _main_db(db, [tuple(row)])
    with pytest.raises(ManifestError):
        load_extraction_manifest(
            ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB),
            main_db_path=db.absolute(),
        )


@pytest.mark.parametrize(
    "raw_json",
    [
        '{"files":[],"files":[]}',
        '{"files":[],"value":NaN}',
        '[{"files":[]}]',
    ],
)
def test_database_summary_json_is_strict(tmp_path: Path, raw_json: str) -> None:
    db = tmp_path / "strict.db"
    _main_db(db, [(
        1, "203.0.113.7", "S", 41, 445, raw_json,
        "extract_runner", "2026-08-16 12:00:00", 1,
    )])
    with pytest.raises(ManifestError):
        load_extraction_manifest(
            ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB),
            main_db_path=db.absolute(),
        )


def test_fallback_envelope_round_trip_and_legacy_or_mode_refusal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    envelope = {
        "host": {
            "host_type": "F", "ip_address": "198.51.100.8",
            "port": 21, "protocol_server_id": 17,
        },
        "schema": "dirracuda-extract-summary-v1",
        "summary": _summary([item], ip="198.51.100.8"),
    }
    fallback = tmp_path / "manifest.json"
    fallback.write_text(json.dumps(envelope), encoding="utf-8")
    fallback.chmod(0o600)
    reference = ExtractSummaryReference(
        None, fallback.absolute(), ExtractSummarySource.FALLBACK_JSON,
    )
    manifest = load_extraction_manifest(reference)
    assert (manifest.host_type, manifest.port, manifest.protocol_server_id) == (
        "F", 21, 17,
    )

    fallback.write_text(json.dumps(envelope["summary"]), encoding="utf-8")
    with pytest.raises(ManifestError):
        load_extraction_manifest(reference)
    fallback.write_text(json.dumps(envelope), encoding="utf-8")
    fallback.chmod(0o644)
    with pytest.raises(ManifestError):
        load_extraction_manifest(reference)


def test_fallback_parent_symlink_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    fallback = real / "summary.json"
    fallback.write_text("{}", encoding="utf-8")
    fallback.chmod(0o600)
    reference = ExtractSummaryReference(
        None, (link / "summary.json").absolute(), ExtractSummarySource.FALLBACK_JSON,
    )
    with pytest.raises(ManifestError):
        load_extraction_manifest(reference)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda summary: summary["files"].append(dict(summary["files"][0])),
        lambda summary: summary["files"].append({"saved_to": "relative.txt"}),
        lambda summary: summary["files"].append({"saved_to": "/tmp/bad\\name"}),
        lambda summary: summary["files"].append({"saved_to": "/tmp/../bad"}),
    ],
)
def test_saved_path_hostility_is_rejected(tmp_path: Path, mutator) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    summary = _summary([item])
    mutator(summary)
    summary["totals"]["files_downloaded"] = len(summary["files"])
    db = tmp_path / "main.db"
    _main_db(db, [_db_row(1, summary)])
    with pytest.raises(ManifestError):
        load_extraction_manifest(
            ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB),
            main_db_path=db.absolute(),
        )


def test_missing_symlink_and_special_manifest_paths_are_closed_exclusions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("public", encoding="utf-8")
    link = root / "link.txt"
    link.symlink_to(real)
    fifo = root / "fifo"
    os.mkfifo(fifo)
    missing = root / "missing.txt"
    summary = {
        "ip_address": "203.0.113.7",
        "files": [
            {"saved_to": str(real)}, {"saved_to": str(link)},
            {"saved_to": str(fifo)}, {"saved_to": str(missing)},
        ],
        "totals": {"files_downloaded": 4},
    }
    db = tmp_path / "main.db"
    _main_db(db, [_db_row(1, summary)])
    manifest = load_extraction_manifest(
        ExtractSummaryReference(1, None, ExtractSummarySource.PRIMARY_DB),
        main_db_path=db.absolute(),
    )
    assert [item.relative_path for item in manifest.inventory.files] == ["real.txt"]
    assert [item.reason for item in manifest.inventory.exclusions] == [
        "symlink", "special_file", "entry_unreadable",
    ]


def test_manifest_hash_race_becomes_closed_exclusion(
    tmp_path: Path, monkeypatch,
) -> None:
    from experimental.analyst import inventory

    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    original = inventory._hash_fd

    def changing_hash(fd, cancel_check):
        digest = original(fd, cancel_check)
        item.write_text("changed", encoding="utf-8")
        return digest

    monkeypatch.setattr(inventory, "_hash_fd", changing_hash)
    result = inventory.inventory_selected_paths(root, ("one.txt",))
    assert not result.files
    assert result.exclusions[0].reason == "changed_during_inventory"


def test_write_extract_log_fallback_is_private_versioned_and_loadable(
    tmp_path: Path, monkeypatch,
) -> None:
    from gui.utils import extract_runner

    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    logs = tmp_path / "logs"
    monkeypatch.setattr(extract_runner, "select_existing_path", lambda *_a: logs)
    reference = extract_runner.write_extract_log(
        _summary([item]), ip_address="203.0.113.7", host_type="H", port=80,
    )
    assert reference.source is ExtractSummarySource.FALLBACK_JSON
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700
    assert stat.S_IMODE(reference.fallback_log_path.stat().st_mode) == 0o600
    assert "203.0.113.7" not in reference.fallback_log_path.name
    assert load_extraction_manifest(reference).host_type == "H"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ip_address": ""},
        {"ip_address": "203.0.113.7/escape"},
        {"ip_address": "203.0.113.7", "host_type": "X"},
        {"ip_address": "203.0.113.7", "protocol_server_id": True},
        {"ip_address": "203.0.113.7", "port": 0},
    ],
)
def test_extract_reference_persistence_rejects_invalid_identity_before_file(
    tmp_path: Path, monkeypatch, kwargs,
) -> None:
    from gui.utils import extract_runner

    logs = tmp_path / "logs"
    monkeypatch.setattr(extract_runner, "select_existing_path", lambda *_a: logs)
    summary = {"ip_address": kwargs.get("ip_address"), "files": [], "totals": {}}
    with pytest.raises(ValueError):
        extract_runner.write_extract_log(summary, **kwargs)
    assert not logs.exists()


def test_manifest_run_copies_identity_and_defaults_output_to_common_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    db = tmp_path / "main.db"
    _main_db(db, [_db_row(7, _summary([item]))])
    analyst_db = tmp_path / "analyst.db"
    run_id, manifest = create_manifest_run(
        ExtractSummaryReference(7, None, ExtractSummarySource.PRIMARY_DB),
        main_db_path=db.absolute(),
        output_base=None,
        report_label="Confirmed public host",
        mode="fast",
        path=analyst_db.absolute(),
        run_id_factory=lambda _size: RUN_ID,
    )
    assert run_id == RUN_ID
    assert manifest.source_root == root
    conn = sqlite3.connect(analyst_db)
    try:
        row = conn.execute(
            "SELECT source_mode,source_root,output_root,host_type,"
            "protocol_server_id,ip_address,port,extract_summary_row_id "
            "FROM analyst_runs WHERE run_id=?", (RUN_ID,),
        ).fetchone()
    finally:
        conn.close()
    assert row[:2] == ("extraction_manifest", str(root))
    assert Path(row[2]).parent == root / "_analyst"
    assert row[3:] == ("S", 41, "203.0.113.7", 445, 7)


def test_fallback_manifest_run_keeps_row_id_null(tmp_path: Path) -> None:
    root = tmp_path / "saved"
    root.mkdir()
    item = root / "one.txt"
    item.write_text("public", encoding="utf-8")
    fallback = tmp_path / "summary.json"
    fallback.write_text(json.dumps({
        "host": {
            "host_type": "H", "ip_address": "2001:db8::7",
            "port": 8080, "protocol_server_id": 29,
        },
        "schema": "dirracuda-extract-summary-v1",
        "summary": _summary([item], ip="2001:db8::7"),
    }), encoding="utf-8")
    fallback.chmod(0o600)
    analyst_db = tmp_path / "analyst.db"
    run_id, _manifest = create_manifest_run(
        ExtractSummaryReference(
            None, fallback.absolute(), ExtractSummarySource.FALLBACK_JSON,
        ),
        main_db_path=None,
        output_base=None,
        report_label="Confirmed IPv6 host",
        path=analyst_db.absolute(),
        run_id_factory=lambda _size: RUN_ID,
    )
    conn = sqlite3.connect(analyst_db)
    try:
        row = conn.execute(
            "SELECT host_type,protocol_server_id,ip_address,port,"
            "extract_summary_row_id FROM analyst_runs WHERE run_id=?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("H", 29, "2001:db8::7", 8080, None)
