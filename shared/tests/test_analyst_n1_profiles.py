"""N1: schema v7 server profiles, additive migration, and CRUD round-trips.

Covers `N1_CARD.md` acceptance 5 (CRUD round-trips; migration is additive and
old rows still read) and acceptance 7 (a remote profile stores but refuses to
connect).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import db_schema
from experimental.analyst.db_schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    V6_SCHEMA_VERSION,
    AnalystSchemaError,
    validate_schema,
    validate_migration_candidate,
)
from experimental.analyst.endpoint import RemoteNotEnabledError
from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.profiles import (
    DEFAULT_PROFILE_NAME,
    BackendKind,
    ProfileError,
    create_profile,
    delete_profile,
    ensure_default_profile,
    get_profile,
    list_profiles,
    touch_last_used,
    update_profile,
)
from experimental.analyst.store import initialize_database, open_connection

_NOW = "2026-09-22T09:00:00Z"
_RUN_ID = "r" * 32
_REMOTE = "http://100.125.197.36:9292"
_FINGERPRINT = "ab" * 32


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "analyst.db"
    initialize_database(path)
    return path


# --------------------------------------------------------------------------
# Schema v7
# --------------------------------------------------------------------------

def test_schema_version_is_current_and_the_prior_one_is_previous(db: Path):
    conn = open_connection(db, read_only=True)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
    finally:
        conn.close()
    assert db_schema.PREVIOUS_SCHEMA_VERSION == SCHEMA_VERSION - 1
    assert V6_SCHEMA_VERSION in db_schema.KNOWN_SCHEMA_VERSIONS


def test_v7_adds_only_the_profile_table_and_its_index():
    added = {o[1] for o in db_schema._expected_snapshot(SCHEMA_VERSION).objects} - {
        o[1] for o in db_schema._expected_snapshot(V6_SCHEMA_VERSION).objects
    }
    assert added == {"analyst_llm_profile", "idx_analyst_runs_profile"}


def test_analyst_runs_gains_two_nullable_columns(db: Path):
    conn = open_connection(db, read_only=True)
    try:
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(analyst_runs)")
        }
    finally:
        conn.close()
    for name in ("profile_id", "backend_kind"):
        assert name in columns
        assert int(columns[name][3]) == 0, f"{name} must be nullable"
        assert columns[name][4] is None, f"{name} must have no default"


# --------------------------------------------------------------------------
# Additive migration from v6
# --------------------------------------------------------------------------

def _build_v6(path: Path, *, populated: bool) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for statement in (
            *db_schema._V1_TABLE_DDL,
            *db_schema._V1_INDEX_DDL,
            *db_schema._V2_ADDITIONAL_TABLE_DDL,
            *db_schema._V2_ADDITIONAL_INDEX_DDL,
            *db_schema._V3_ADDITIONAL_DDL,
            *db_schema._V4_ADDITIONAL_DDL,
            *db_schema._V5_ADDITIONAL_DDL,
            *db_schema._V6_ADDITIONAL_DDL,
        ):
            conn.execute(statement)
        conn.execute("INSERT INTO analyst_gpu_lease(slot,generation) VALUES(1,0)")
        if populated:
            conn.execute(
                "INSERT INTO analyst_runs("
                "run_id,state,created_at_utc,updated_at_utc,mode,source_mode,"
                "source_root,output_root,source_identity_json,"
                "source_identity_sha256,report_label,model_tag,model_digest,"
                "worksheet_version,prompt_sha256,response_schema_sha256,"
                "detector_rules_version,detector_rules_sha256,parser_bundle_json,"
                "parser_bundle_sha256,chunk_chars,overlap_chars,num_ctx,"
                "num_predict,isolation_mode,reduced_isolation_ack) "
                "VALUES(?,'ready',?,?,'fast','unknown','/source','/output','{}',?,"
                "'Existing run','qwen3.6:27b',?,'v2',?,?,'rules-v1',?,'{}',?,"
                "8000,256,8192,1024,'strict',0)",
                (
                    _RUN_ID, _NOW, _NOW, _sha("identity"), _sha("model"),
                    _sha("prompt"), _sha("response"), _sha("rules"), _sha("parser"),
                ),
            )
            conn.execute(
                "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
                "VALUES(?,?)",
                (_RUN_ID, _NOW),
            )
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={V6_SCHEMA_VERSION}")
        conn.execute("COMMIT")
    finally:
        conn.close()
    path.chmod(0o600)


def test_v6_database_migrates_to_v7(tmp_path: Path):
    path = tmp_path / "v6.db"
    _build_v6(path, populated=False)
    initialize_database(path)
    conn = open_connection(path, read_only=True)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        validate_schema(conn)
    finally:
        conn.close()


def test_an_existing_run_row_still_reads_after_migration(tmp_path: Path):
    """Acceptance 5: the migration is additive; old rows keep working."""
    path = tmp_path / "v6.db"
    _build_v6(path, populated=True)
    initialize_database(path)
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT run_id,report_label,profile_id,backend_kind "
            "FROM analyst_runs WHERE run_id=?",
            (_RUN_ID,),
        ).fetchone()
    finally:
        conn.close()
    assert row["run_id"] == _RUN_ID
    assert row["report_label"] == "Existing run"
    assert row["profile_id"] is None
    assert row["backend_kind"] is None


def test_v6_candidate_refuses_a_database_with_work_in_flight(tmp_path: Path):
    path = tmp_path / "v6.db"
    _build_v6(path, populated=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "UPDATE analyst_runs SET state='running' WHERE run_id=?", (_RUN_ID,)
        )
        with pytest.raises(AnalystSchemaError):
            validate_migration_candidate(conn, V6_SCHEMA_VERSION)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CRUD round-trips
# --------------------------------------------------------------------------

def test_default_profile_is_loopback_ollama_and_idempotent(db: Path):
    first = ensure_default_profile(path=db)
    second = ensure_default_profile(path=db)
    assert first == second
    assert first.name == DEFAULT_PROFILE_NAME
    assert first.endpoint_url == "http://127.0.0.1:11434"
    assert first.backend_kind is BackendKind.OLLAMA
    assert first.is_loopback


def test_create_read_update_delete_round_trip(db: Path):
    created = create_profile(
        "mimir", _REMOTE, backend_kind="openai", now_utc=_NOW, path=db,
    )
    assert created.profile_id > 0
    assert created.endpoint_url == _REMOTE
    assert created.backend_kind is BackendKind.OPENAI_COMPAT
    assert created.created_at_utc == _NOW
    assert created.last_used_utc is None

    assert get_profile(created.profile_id, path=db) == created
    assert list_profiles(path=db) == (created,)

    updated = update_profile(
        created.profile_id,
        name="mimir-tailscale",
        plaintext_ack=True,
        cert_fingerprint=_FINGERPRINT,
        path=db,
    )
    assert updated.name == "mimir-tailscale"
    assert updated.plaintext_ack is True
    assert updated.cert_fingerprint == _FINGERPRINT
    assert updated.endpoint_url == _REMOTE

    touched = touch_last_used(created.profile_id, now_utc=_NOW, path=db)
    assert touched.last_used_utc == _NOW

    delete_profile(created.profile_id, path=db)
    assert list_profiles(path=db) == ()
    assert get_profile(created.profile_id, path=db) is None


def test_update_can_move_the_endpoint(db: Path):
    profile = create_profile("box", "http://192.168.1.242:11434", path=db)
    moved = update_profile(profile.profile_id, endpoint=_REMOTE, path=db)
    assert moved.endpoint_url == _REMOTE
    assert moved.host == "100.125.197.36"


def test_boolean_and_optional_columns_round_trip_exactly(db: Path):
    profile = create_profile(
        "full",
        _REMOTE,
        backend_kind=BackendKind.OPENAI_COMPAT,
        backend_detected=True,
        keymaster_key_id=7,
        cert_fingerprint=_FINGERPRINT.upper(),
        plaintext_ack=True,
        consent_muted=True,
        path=db,
    )
    stored = get_profile(profile.profile_id, path=db)
    assert stored.backend_detected is True
    assert stored.keymaster_key_id == 7
    assert stored.cert_fingerprint == _FINGERPRINT
    assert stored.plaintext_ack is True
    assert stored.consent_muted is True


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------

def test_duplicate_name_is_refused(db: Path):
    create_profile("dupe", _REMOTE, path=db)
    with pytest.raises(ProfileError):
        create_profile("dupe", "http://192.168.1.9:11434", path=db)


def test_duplicate_endpoint_is_refused(db: Path):
    create_profile("one", _REMOTE, path=db)
    with pytest.raises(ProfileError):
        create_profile("two", _REMOTE, path=db)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "x" * 65},
        {"backend_kind": "llamacpp"},
        {"cert_fingerprint": "nothex"},
        {"keymaster_key_id": 0},
        {"keymaster_key_id": True},
    ],
)
def test_create_refuses_invalid_values(db: Path, kwargs):
    base = {"name": "ok", "endpoint": _REMOTE}
    base.update(kwargs)
    with pytest.raises(ProfileError):
        create_profile(base.pop("name"), base.pop("endpoint"), path=db, **base)


def test_update_refuses_an_unknown_field(db: Path):
    profile = create_profile("p", _REMOTE, path=db)
    with pytest.raises(ProfileError):
        update_profile(profile.profile_id, host="elsewhere", path=db)


def test_update_needs_at_least_one_field(db: Path):
    profile = create_profile("p", _REMOTE, path=db)
    with pytest.raises(ProfileError):
        update_profile(profile.profile_id, path=db)


def test_operations_on_a_missing_profile_are_refused(db: Path):
    assert get_profile(999, path=db) is None
    with pytest.raises(ProfileError):
        update_profile(999, name="nope", path=db)
    with pytest.raises(ProfileError):
        delete_profile(999, path=db)
    with pytest.raises(ProfileError):
        touch_last_used(999, path=db)


def test_a_profile_used_by_a_run_cannot_be_deleted(db: Path, tmp_path: Path):
    profile = create_profile("pinned", _REMOTE, path=db)
    conn = sqlite3.connect(db, isolation_level=None)
    try:
        conn.execute(
            "INSERT INTO analyst_runs("
            "run_id,state,created_at_utc,updated_at_utc,mode,source_mode,"
            "source_root,output_root,source_identity_json,source_identity_sha256,"
            "report_label,model_tag,model_digest,worksheet_version,prompt_sha256,"
            "response_schema_sha256,detector_rules_version,detector_rules_sha256,"
            "parser_bundle_json,parser_bundle_sha256,chunk_chars,overlap_chars,"
            "num_ctx,num_predict,isolation_mode,reduced_isolation_ack,profile_id) "
            "VALUES(?,'ready',?,?,'fast','unknown','/s','/o','{}',?,"
            "'Pinned run','qwen3.6:27b',?,'v2',?,?,'rules-v1',?,'{}',?,"
            "8000,256,8192,1024,'strict',0,?)",
            (
                _RUN_ID, _NOW, _NOW, _sha("identity"), _sha("model"),
                _sha("prompt"), _sha("response"), _sha("rules"), _sha("parser"),
                profile.profile_id,
            ),
        )
        conn.execute(
            "INSERT INTO analyst_ollama_schedule(run_id,updated_at_utc) "
            "VALUES(?,?)",
            (_RUN_ID, _NOW),
        )
    finally:
        conn.close()
    with pytest.raises(ProfileError):
        delete_profile(profile.profile_id, path=db)


# --------------------------------------------------------------------------
# Acceptance 7 — stored, but not reachable
# --------------------------------------------------------------------------

def test_a_remote_profile_stores_but_reports_itself_unreachable(db: Path):
    profile = create_profile("mimir", _REMOTE, path=db)
    assert get_profile(profile.profile_id, path=db) is not None
    assert profile.is_loopback is False
    assert profile.is_reachable_now is False
    assert profile.address_class.value == "cgnat"


def test_the_client_refuses_a_stored_remote_profile(db: Path):
    """The guard is in the client, so a stored row cannot bypass it."""
    profile = create_profile("mimir", _REMOTE, path=db)
    with pytest.raises(RemoteNotEnabledError):
        OllamaClient(endpoint=profile.endpoint_url)


def test_a_loopback_profile_is_reachable(db: Path):
    profile = ensure_default_profile(path=db)
    assert profile.is_reachable_now is True
    assert OllamaClient(endpoint=profile.endpoint_url).endpoint.is_loopback


# --------------------------------------------------------------------------
# The service surfaces D17 honestly and leaves no dangling contact
# --------------------------------------------------------------------------

def test_discover_models_against_a_remote_endpoint_raises_and_closes_the_contact(
    tmp_path: Path,
):
    """Acceptance 7: the refusal comes from the client, not the UI."""
    from experimental.analyst import service

    path = tmp_path / "analyst.db"
    with pytest.raises(RemoteNotEnabledError):
        service.discover_models(endpoint=_REMOTE, path=path)

    conn = open_connection(path, read_only=True)
    try:
        rows = conn.execute(
            "SELECT endpoint,state FROM analyst_discovery_contact"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["endpoint"] == _REMOTE
    assert rows[0]["state"] != "dispatching", "the charge must be closed out"


def test_discover_models_does_not_collapse_d17_into_a_service_error(tmp_path: Path):
    """A generic AnalystServiceError would read as 'the server is down'."""
    from experimental.analyst.service import AnalystServiceError

    path = tmp_path / "analyst.db"
    try:
        __import__(
            "experimental.analyst.service", fromlist=["discover_models"]
        ).discover_models(endpoint=_REMOTE, path=path)
    except AnalystServiceError:  # pragma: no cover - would be the bug
        pytest.fail("D17 was reported as a generic discovery failure")
    except RemoteNotEnabledError as exc:
        assert "not enabled yet" in str(exc)


def test_discover_models_persists_per_endpoint(tmp_path: Path, monkeypatch):
    """Two endpoints keep separate model lists."""
    from experimental.analyst import service
    from experimental.analyst.ollama_contract import DiscoveredModel

    path = tmp_path / "analyst.db"
    digest = "c" * 64

    class _FakeClient:
        def __init__(self, *, endpoint=None, **kwargs):
            self.endpoint = endpoint

        def list_models(self):
            return (DiscoveredModel("alpha:3b", digest),)

    monkeypatch.setattr(service, "OllamaClient", _FakeClient)
    service.discover_models(path=path)
    assert len(service.list_discovered_models(path=path)) == 1
    assert service.list_discovered_models(endpoint=_REMOTE, path=path) == ()
