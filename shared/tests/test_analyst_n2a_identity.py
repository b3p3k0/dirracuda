"""N2a: a run records an identity kind, and a reported identity is never verified.

Contract 6.1, erratum E18, decision D18.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from experimental.analyst import report_json
from experimental.analyst.db_schema import IDENTITY_KINDS, SCHEMA_VERSION
from experimental.analyst.report_json import (
    REPORT_SCHEMA_VERSION,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
    ReportValidationError,
    ReportVersionError,
    RunMeta,
    validate_report_json,
)
from experimental.analyst.store import initialize_database, open_connection

_DIGEST = "a" * 64


def _run(**overrides):
    base = dict(
        run_id="r" * 32, report_label="L", read_mode="quick",
        model_tag="qwen3.6:27b", model_digest=_DIGEST,
        created_at_utc="2026-09-22T12:00:00Z",
        files_read=1, files_total=2, flagged_files=0,
    )
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# RunMeta
# --------------------------------------------------------------------------

def test_a_digest_identity_is_verified():
    meta = RunMeta(**_run())
    assert meta.identity_kind == "digest"
    assert meta.is_verified_identity is True


def test_a_reported_identity_is_not_verified():
    meta = RunMeta(**_run(
        model_digest=None, identity_kind="reported",
        model_tag="qwen3.8-27b", model_path="/opt/llm/x.gguf",
        model_n_params=27_000_000_000, model_size_bytes=17_000_000_000,
        model_ftype="Q4_K - Medium", model_n_vocab=151_936,
        model_n_ctx=32768, model_n_ctx_train=32768,
        server_fingerprint="b1-f280b26",
    ))
    assert meta.identity_kind == "reported"
    assert meta.is_verified_identity is False
    assert meta.model_digest is None
    assert meta.server_fingerprint == "b1-f280b26"


def test_a_reported_identity_may_not_carry_a_digest():
    with pytest.raises(ReportValidationError, match="no model digest"):
        RunMeta(**_run(identity_kind="reported"))


def test_a_digest_identity_still_requires_64_hex():
    for bad in (None, "", "z" * 64, _DIGEST[:63], _DIGEST.upper()):
        with pytest.raises(ReportValidationError):
            RunMeta(**_run(model_digest=bad))


def test_an_unknown_identity_kind_is_refused():
    with pytest.raises(ReportValidationError):
        RunMeta(**_run(identity_kind="trusted"))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("model_path", ""), ("model_ftype", 7), ("server_fingerprint", ""),
        ("model_n_params", 0), ("model_size_bytes", -1), ("model_n_vocab", "x"),
        ("model_n_ctx", 0), ("model_n_ctx_train", -5),
    ],
)
def test_reported_fields_are_validated(field, bad):
    with pytest.raises(ReportValidationError):
        RunMeta(**_run(model_digest=None, identity_kind="reported", **{field: bad}))


def test_the_schema_and_report_kinds_agree():
    assert set(IDENTITY_KINDS) == set(report_json.IDENTITY_KINDS)


# --------------------------------------------------------------------------
# Payload, and the v1 reports already on disk
# --------------------------------------------------------------------------

def test_report_schema_version_is_four_and_older_ones_still_read():
    assert REPORT_SCHEMA_VERSION == 4
    assert SUPPORTED_REPORT_SCHEMA_VERSIONS == (1, 2, 3, 4)


def _v1_report():
    return {
        "report_schema_version": 1,
        "run": _run(),
        "read": {
            "unverified_notice": report_json.UNVERIFIED_NOTICE,
            "host_summary": "h", "likely_owner": None, "contacts": [],
            "risk_level": "LOW", "top_exposures": [],
        },
        "facts": [],
        "coverage": {
            "discovered": 2, "terminal": 2, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }


def test_a_v1_report_still_validates():
    """The reports already on disk must not be orphaned by the bump."""
    validate_report_json(_v1_report())


def test_a_v1_report_may_not_carry_v2_keys():
    report = _v1_report()
    report["run"]["identity_kind"] = "digest"
    with pytest.raises(ReportValidationError):
        validate_report_json(report)


def test_a_v2_report_requires_the_identity_keys():
    report = _v1_report()
    report["report_schema_version"] = 2
    with pytest.raises(ReportValidationError):
        validate_report_json(report)


def test_an_unsupported_version_is_refused():
    report = _v1_report()
    report["report_schema_version"] = 5
    with pytest.raises(ReportVersionError):
        validate_report_json(report)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "analyst.db"
    initialize_database(path)
    return path


def _insert_run(conn, **overrides):
    cols = dict(
        run_id="r" * 32, state="ready", created_at_utc="t", updated_at_utc="t",
        mode="fast", source_mode="unknown", source_root="/s", output_root="/o",
        source_identity_json="{}", source_identity_sha256="b" * 64,
        report_label="L", model_tag="m", model_digest=_DIGEST,
        worksheet_version="v2", prompt_sha256="c" * 64,
        response_schema_sha256="d" * 64, detector_rules_version="v1",
        detector_rules_sha256="e" * 64, parser_bundle_json="{}",
        parser_bundle_sha256="f" * 64, chunk_chars=8000, overlap_chars=256,
        num_ctx=8192, num_predict=1024, isolation_mode="strict",
        reduced_isolation_ack=0,
    )
    cols.update(overrides)
    names = ",".join(cols)
    conn.execute(
        f"INSERT INTO analyst_runs({names}) "
        f"VALUES({','.join('?' * len(cols))})",
        tuple(cols.values()),
    )


def test_a_reported_run_persists_without_a_digest(db: Path):
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _insert_run(
            conn, model_digest=None, identity_kind="reported",
            model_tag="qwen3.8-27b", model_path="/opt/llm/x.gguf",
            server_fingerprint="b1-f280b26",
        )
        row = conn.execute(
            "SELECT model_digest,identity_kind,model_path,server_fingerprint "
            "FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row == (None, "reported", "/opt/llm/x.gguf", "b1-f280b26")


def test_a_digest_run_may_not_have_a_null_digest(db: Path):
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, model_digest=None, identity_kind="digest")
    finally:
        conn.close()


def test_a_reported_run_may_not_have_a_digest(db: Path):
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, model_digest=_DIGEST, identity_kind="reported")
    finally:
        conn.close()


def test_a_pre_v8_row_keeps_its_digest_and_null_kind(db: Path):
    """Rows written before v8 carry no kind and must still be legal."""
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        _insert_run(conn)
        row = conn.execute(
            "SELECT model_digest,identity_kind FROM analyst_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row == (_DIGEST, None)
