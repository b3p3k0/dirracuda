"""Guardrail: one schema ladder, and every known version still upgrades.

Card S1. Adding an Analyst schema version must be a single-place edit in
`db_schema.py`. These tests fail if a version is added to the ladder but missed
by one of the sites that consume it — the class of bug that, during N1, would
have made the app refuse to open every existing v6 database.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    KNOWN_SCHEMA_VERSIONS,
    MIGRATABLE_VERSIONS,
    SCHEMA_VERSION,
    validate_schema,
)
from experimental.analyst.store import initialize_database, open_connection


# Captured from the tree before the S1 refactor. A change here means a
# historical schema moved, which no refactor may ever do.
_GOLDEN_SNAPSHOT_SHA256 = {
    1: "0764499ca12ad5a8cbaa51e81a03b6a89134b9dad5a057aa6c86a53e4f359f6f",
    2: "08cca9a0e0cd0c6621a91693fbc62cd1b805418cfe8bb6e2e281b5bdce21cc6c",
    3: "ea9e198b68072779c64db19ae5210b13b9f0c76a4e586bd168fb2fbfd15b90d0",
    4: "25f1e24e5e40fc91baa478944677c7801ae7207409886f7aa34d043ddbe6734e",
    5: "8b78ef1a9679ba65eae6b7c3f16df2618fbbbb8daaa3e199fc0a6aa64cfd94b4",
    6: "8dbfc02f1222777d4c5a533bff8e517b4ff663571e20841dc24f0ddff2df117d",
    7: "7475975e3aab5c4dcc8f9ff0a1a0a93962e6d44d5856e4fe114d9c0c94955c18",
}


def _snapshot_digest(snapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "objects": snapshot.objects,
                "table_list": snapshot.table_list,
                "columns": snapshot.columns,
                "indexes": snapshot.indexes,
                "index_columns": snapshot.index_columns,
                "foreign_keys": snapshot.foreign_keys,
            },
            sort_keys=True,
            default=list,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _build_version(path: Path, version: int) -> None:
    """Create a database at one historical version, straight from the ladder."""
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for statement in (*db_schema._V1_TABLE_DDL, *db_schema._V1_INDEX_DDL):
            conn.execute(statement)
        for statement in db_schema._ddl_through(version):
            conn.execute(statement)
        conn.execute("INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)")
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("COMMIT")
    finally:
        conn.close()
    path.chmod(0o600)


# --------------------------------------------------------------------------
# The ladder itself
# --------------------------------------------------------------------------

def test_ladder_covers_every_known_version_in_order():
    versions = tuple(step.version for step in db_schema._LADDER)
    assert versions == tuple(KNOWN_SCHEMA_VERSIONS)
    assert versions == tuple(sorted(versions))
    assert versions[-1] == SCHEMA_VERSION
    assert len(set(versions)) == len(versions)


def test_migratable_versions_is_every_version_but_the_current_one():
    assert MIGRATABLE_VERSIONS == tuple(
        v for v in KNOWN_SCHEMA_VERSIONS if v != SCHEMA_VERSION
    )
    assert SCHEMA_VERSION not in MIGRATABLE_VERSIONS


def test_only_the_first_step_carries_no_ddl():
    """Every version after v1 must actually add something."""
    for step in db_schema._LADDER[1:]:
        assert step.ddl, f"v{step.version} adds no DDL"
    assert db_schema._LADDER[0].ddl == ()


@pytest.mark.parametrize("version", KNOWN_SCHEMA_VERSIONS)
def test_expected_snapshot_matches_its_frozen_digest(version):
    """No refactor may move a historical schema."""
    digest = _snapshot_digest(db_schema._expected_snapshot(version))
    assert digest == _GOLDEN_SNAPSHOT_SHA256[version]


def test_every_known_version_has_a_frozen_digest():
    """A new version must add its digest here deliberately."""
    assert set(_GOLDEN_SNAPSHOT_SHA256) == set(KNOWN_SCHEMA_VERSIONS)


@pytest.mark.parametrize("version", KNOWN_SCHEMA_VERSIONS)
def test_ddl_through_rebuilds_that_version_exactly(version):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        for statement in (*db_schema._V1_TABLE_DDL, *db_schema._V1_INDEX_DDL):
            conn.execute(statement)
        for statement in db_schema._ddl_through(version):
            conn.execute(statement)
        assert _snapshot_digest(
            db_schema._schema_snapshot(conn)
        ) == _GOLDEN_SNAPSHOT_SHA256[version]
    finally:
        conn.close()


@pytest.mark.parametrize("version", MIGRATABLE_VERSIONS)
def test_ddl_through_plus_ddl_after_is_the_whole_schema(version):
    assert (
        db_schema._ddl_through(version) + db_schema._ddl_after(version)
        == db_schema._ddl_through(SCHEMA_VERSION)
    )


def test_unknown_versions_are_refused():
    for bad in (0, SCHEMA_VERSION + 1, -1):
        with pytest.raises(ValueError):
            db_schema._ddl_through(bad)


# --------------------------------------------------------------------------
# The check that would have caught the N1 bug
# --------------------------------------------------------------------------

@pytest.mark.parametrize("version", MIGRATABLE_VERSIONS)
def test_every_migratable_version_upgrades_to_current(tmp_path, version):
    """A database at any known older version must open and reach current.

    During N1 this would have failed for v6: `store._audit_existing_database`
    had no v6 branch, so it refused the file instead of upgrading it.
    """
    path = tmp_path / f"v{version}.db"
    _build_version(path, version)

    initialize_database(path)

    conn = open_connection(path, read_only=True)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        validate_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("version", MIGRATABLE_VERSIONS)
def test_migrating_twice_is_idempotent(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    _build_version(path, version)
    initialize_database(path)
    first = hashlib.sha256(path.read_bytes()).hexdigest()
    initialize_database(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == first
# --------------------------------------------------------------------------
# The ladder is not re-duplicated elsewhere
# --------------------------------------------------------------------------

_STORE = Path(db_schema.__file__).with_name("store.py")


def test_store_has_no_per_version_ladder():
    """store.py must derive from MIGRATABLE_VERSIONS, never branch per version.

    A hand-written ladder here is exactly what went stale during N1.
    """
    source = _STORE.read_text(encoding="utf-8")
    offenders = [
        name
        for name in (
            "V1_SCHEMA_VERSION",
            "V2_SCHEMA_VERSION",
            "V3_SCHEMA_VERSION",
            "V4_SCHEMA_VERSION",
            "V5_SCHEMA_VERSION",
            "V6_SCHEMA_VERSION",
        )
        if name in source
    ]
    assert offenders == [], (
        f"store.py names individual schema versions {offenders}; it should use "
        "MIGRATABLE_VERSIONS so a new version needs no edit here"
    )


def test_store_imports_no_per_version_validator():
    tree = ast.parse(_STORE.read_text(encoding="utf-8"), filename=str(_STORE))
    imported = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    offenders = [
        name
        for name in imported
        if name.startswith("validate_v") and name.endswith("_migration_candidate")
    ]
    assert offenders == [], f"store.py imports per-version validators {offenders}"


def test_db_schema_exports_the_generic_candidate_validator():
    assert "validate_migration_candidate" in db_schema.__all__
    assert not any(
        name.startswith("validate_v") and name.endswith("_migration_candidate")
        for name in db_schema.__all__
    )
