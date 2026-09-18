"""Unsigned mergerfs identity and Analyst v3 migration regressions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from experimental.analyst.checkpoint import claim_next_file
from experimental.analyst.db_schema import (
    SCHEMA_VERSION,
    V2_SCHEMA_VERSION,
    validate_schema_v2,
)
from experimental.analyst.file_identity import (
    FileIdentityError,
    SQLITE_INTEGER_MAX,
    UINT64_MAX,
    join_unsigned_u64,
    split_unsigned_u64,
)
from experimental.analyst.inventory import InventoryFile, InventoryResult
from experimental.analyst.lease import claim_worker
from experimental.analyst.phase1_state import load_file_resume_snapshot
from experimental.analyst.process_identity import ProcessIdentity
from experimental.analyst.store import (
    RunSpec,
    create_run,
    initialize_database,
    open_connection,
)


_NOW = "2026-08-17T12:00:00Z"


def _spec(run_id: str = "a" * 32) -> RunSpec:
    return RunSpec(
        run_id=run_id, mode="fast", source_mode="unknown",
        source_root="/public/source", output_root="/public/output",
        source_identity={"kind": "public-synthetic", "version": 1},
        report_label="Public identity run", model_tag="qwen3.6:27b",
        model_digest="1" * 64, worksheet_version="v2",
        prompt_sha256="2" * 64, response_schema_sha256="3" * 64,
        detector_rules_version="rules-v1", detector_rules_sha256="4" * 64,
        parser_bundle={"bundle": "public-test"}, chunk_chars=8000,
        overlap_chars=256, num_ctx=8192, num_predict=1024,
        isolation_mode="strict", reduced_isolation_ack=False,
    )


def _inventory(device: int, inode: int) -> InventoryResult:
    return InventoryResult(
        root_device=device, root_inode=inode, root_mount_id=1,
        files=(InventoryFile(
            "public.bin", 8, 10, 11, device, inode, 0o600, "5" * 64,
        ),),
        exclusions=(),
    )


@pytest.mark.parametrize(
    "value", [0, 1, SQLITE_INTEGER_MAX, 1 << 63, UINT64_MAX],
)
def test_unsigned_identity_codec_round_trips_exact_uint64(value: int) -> None:
    low, high = split_unsigned_u64(value)
    assert 0 <= low <= SQLITE_INTEGER_MAX
    assert high in (0, 1)
    assert join_unsigned_u64(low, high) == value


@pytest.mark.parametrize("value", [-1, UINT64_MAX + 1, True, 1.0])
def test_unsigned_identity_codec_rejects_non_uint64(value: object) -> None:
    with pytest.raises(FileIdentityError):
        split_unsigned_u64(value)  # type: ignore[arg-type]


def test_high_bit_mergerfs_identity_persists_and_claims_losslessly(
    tmp_path: Path,
) -> None:
    device = (1 << 63) + 1_234
    inode = (1 << 63) + 5_678
    path = initialize_database(tmp_path / "state" / "analyst.db")
    create_run(_spec(), _inventory(device, inode), now_utc=_NOW, path=path)

    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT device,device_high_bit,inode,inode_high_bit "
            "FROM analyst_files"
        ).fetchone()
        assert tuple(row) == (1_234, 1, 5_678, 1)
    finally:
        conn.close()

    fence = claim_worker(
        "a" * 32,
        ProcessIdentity(1234, 5678, "00000000-0000-4000-8000-000000000001"),
        owner_token="6" * 64, heartbeat_monotonic_ns=10,
        now_utc=_NOW, path=path,
    )
    assert fence is not None
    claimed = claim_next_file(fence, now_utc=_NOW, path=path)
    assert claimed is not None
    assert (claimed.device, claimed.inode) == (device, inode)
    snapshot = load_file_resume_snapshot(fence, claimed.file_id, path=path)
    assert (
        snapshot.inventory_file.device, snapshot.inventory_file.inode,
    ) == (device, inode)


def test_populated_exact_v2_migrates_in_place_with_zero_high_bits(
    tmp_path: Path,
) -> None:
    path = initialize_database(tmp_path / "v2" / "analyst.db")
    create_run(_spec(), _inventory(7, 9), now_utc=_NOW, path=path)
    inode_before = path.stat().st_ino
    conn = sqlite3.connect(path, autocommit=True)
    try:
        conn.execute("DROP TABLE analyst_discovered_model")
        conn.execute("DROP TABLE analyst_discovery_contact")
        conn.execute("DROP TABLE analyst_read_contact")
        conn.execute("DROP TABLE analyst_read_exposures")
        conn.execute("DROP TABLE analyst_read")
        conn.execute("ALTER TABLE analyst_detector_hits DROP COLUMN fact_rank")
        conn.execute("ALTER TABLE analyst_model_findings DROP COLUMN fact_rank")
        conn.execute("ALTER TABLE analyst_files DROP COLUMN inode_high_bit")
        conn.execute("ALTER TABLE analyst_files DROP COLUMN device_high_bit")
        conn.execute(f"PRAGMA user_version={V2_SCHEMA_VERSION}")
        validate_schema_v2(conn)
    finally:
        conn.close()

    assert initialize_database(path) == path
    assert path.stat().st_ino == inode_before
    conn = open_connection(path, read_only=True)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = conn.execute(
            "SELECT device,device_high_bit,inode,inode_high_bit "
            "FROM analyst_files"
        ).fetchone()
        assert tuple(row) == (7, 0, 9, 0)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        conn.close()
