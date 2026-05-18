"""
Unit tests for experimental.censys_discovery.service — SMB protocol path.

CensysClient is mocked throughout — no network I/O.
DB tests use tmp_path for isolation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from experimental.censys_discovery.models import (
    ApiError,
    AUTH_FORBIDDEN,
    AUTH_UNAUTHORIZED,
    CensysRunOptions,
    ClientResult,
    CreditBalance,
    NETWORK_ERROR,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
    SearchPage,
    SearchResultItem,
)
from experimental.censys_discovery.query_builder import get_extra_fields
from experimental.censys_discovery.service import run_smb_discovery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _options(**kwargs) -> CensysRunOptions:
    defaults = dict(
        pat="testpat-secret",
        protocol="SMB",
        max_pages=3,
        page_size=50,
        org_id="11111111-2222-3333-4444-555555555555",
    )
    defaults.update(kwargs)
    return CensysRunOptions(**defaults)


def _ok_credits() -> ClientResult:
    return ClientResult(ok=True, data=CreditBalance(balance=500, resets_at=None), error=None)


def _fail_auth(reason_code: str = AUTH_UNAUTHORIZED) -> ClientResult:
    return ClientResult(
        ok=False, data=None,
        error=ApiError(reason_code=reason_code, status_code=401, message="auth denied"),
    )


def _search_page(items: list, cursor: str | None = None) -> ClientResult:
    return ClientResult(
        ok=True,
        data=SearchPage(items=items, next_cursor=cursor, total_hits=len(items)),
        error=None,
    )


def _search_error(reason_code: str = NETWORK_ERROR, msg: str = "connection reset") -> ClientResult:
    return ClientResult(
        ok=False, data=None,
        error=ApiError(reason_code=reason_code, status_code=None, message=msg),
    )


def _items(n: int, base: str = "10.0.0") -> list:
    return [
        SearchResultItem(
            ip_address=f"{base}.{i + 1}",
            port=445,
            protocol="SMB",
            transport_protocol="TCP",
            banner=None,
            scan_time="2026-05-14T00:00:00",
            source_json='{"port":445}',
        )
        for i in range(n)
    ]


def _count_rows(db: Path, table: str) -> int:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Protocol guard
# ---------------------------------------------------------------------------


def test_smb_wrong_protocol_rejected(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    result = run_smb_discovery(_options(protocol="FTP"), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


# ---------------------------------------------------------------------------
# Preflight failure — user-scoped
# ---------------------------------------------------------------------------


def test_smb_preflight_unauthorized_no_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_smb_preflight_forbidden_no_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_FORBIDDEN)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_smb_preflight_network_error_no_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    net_err = ClientResult(
        ok=False, data=None,
        error=ApiError(NETWORK_ERROR, None, "connection refused"),
    )
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = net_err
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


# ---------------------------------------------------------------------------
# Preflight failure — org-scoped
# ---------------------------------------------------------------------------


def test_smb_org_scoped_preflight(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page([], cursor=None)
        run_smb_discovery(_options(org_id="org-abc"), db_path=db)

    inst.get_org_credits.assert_called_once_with("org-abc")
    inst.get_user_credits.assert_not_called()


# ---------------------------------------------------------------------------
# DB setup failure
# ---------------------------------------------------------------------------


def test_smb_db_setup_failure(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _ok_credits()
        with patch("experimental.censys_discovery.service.init_db", side_effect=RuntimeError("disk full")):
            result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert "DB setup failed" in result.error


# ---------------------------------------------------------------------------
# Auth failure — page 1 (uniform no-write policy)
# ---------------------------------------------------------------------------


def test_smb_page1_auth_failure_deletes_run_row(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert _count_rows(db, "censys_runs") == 0
    assert _count_rows(db, "censys_results") == 0


# ---------------------------------------------------------------------------
# Auth failure — page 2+ (uniform no-write policy)
# ---------------------------------------------------------------------------


def test_smb_page2_auth_failure_deletes_run_row(tmp_path: Path) -> None:
    """Page 1 returns items (accumulated); page 2 auth failure — zero rows in DB."""
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.side_effect = [
            _search_page(_items(3), cursor="tok1"),
            _fail_auth(AUTH_FORBIDDEN),
        ]
        result = run_smb_discovery(_options(max_pages=2), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert _count_rows(db, "censys_runs") == 0
    assert _count_rows(db, "censys_results") == 0


# ---------------------------------------------------------------------------
# Non-auth error — partial data committed, run row retained
# ---------------------------------------------------------------------------


def test_smb_partial_non_auth_error_commits_partial(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.side_effect = [
            _search_page(_items(3), cursor="tok1"),
            _search_error(NETWORK_ERROR),
        ]
        result = run_smb_discovery(_options(max_pages=2), db_path=db)

    assert result.ok is False
    assert result.run_id is not None
    assert result.status == RUN_STATUS_ERROR
    assert result.fetched_count == 3
    assert _count_rows(db, "censys_runs") == 1
    assert _count_rows(db, "censys_results") == 3


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_smb_success_single_page(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page(_items(5), cursor=None)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is True
    assert result.run_id is not None
    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 5
    assert result.deduped_count == 5
    assert result.error is None
    assert _count_rows(db, "censys_runs") == 1
    assert _count_rows(db, "censys_results") == 5


def test_smb_success_multi_page(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.side_effect = [
            _search_page(_items(3, base="10.1.0"), cursor="tok1"),
            _search_page(_items(2, base="10.2.0"), cursor=None),
        ]
        result = run_smb_discovery(_options(max_pages=5), db_path=db)

    assert result.ok is True
    assert result.fetched_count == 5
    assert result.deduped_count == 5
    assert inst.search_query.call_count == 2


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_smb_deduplication(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    same_item = SearchResultItem(
        ip_address="192.168.1.1", port=445, protocol="SMB",
        transport_protocol="TCP", banner=None, scan_time=None,
        source_json='{"port":445}',
    )
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page([same_item, same_item], cursor=None)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is True
    assert result.fetched_count == 2
    assert result.deduped_count == 1


# ---------------------------------------------------------------------------
# PAT safety
# ---------------------------------------------------------------------------


def test_smb_pat_not_in_error_on_preflight_failure(tmp_path: Path) -> None:
    unique_pat = "SMB-SECRET-PAT-ABC-99999"
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_smb_discovery(_options(pat=unique_pat), db_path=db)

    assert result.error is not None
    assert unique_pat not in result.error


def test_smb_pat_not_in_error_on_search_failure(tmp_path: Path) -> None:
    unique_pat = "SMB-SECRET-PAT-XYZ-77777"
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_error(NETWORK_ERROR, msg="connection reset")
        result = run_smb_discovery(_options(pat=unique_pat), db_path=db)

    assert result.error is not None
    assert unique_pat not in result.error


# ---------------------------------------------------------------------------
# Run insert failure (COMMIT 1)
# ---------------------------------------------------------------------------


def test_smb_run_insert_failure(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _ok_credits()
        with patch("experimental.censys_discovery.service.insert_run", side_effect=RuntimeError("DB locked")):
            result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert "Run insert failed" in result.error


# ---------------------------------------------------------------------------
# Protocol tag stored as SMB
# ---------------------------------------------------------------------------


def test_smb_protocol_tag_stored_as_SMB(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page(_items(1), cursor=None)
        result = run_smb_discovery(_options(), db_path=db)

    assert result.ok is True
    with sqlite3.connect(str(db)) as conn:
        proto = conn.execute(
            "SELECT protocol FROM censys_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert proto == "SMB"


# ---------------------------------------------------------------------------
# SMB extra fields wired into search_query call
# ---------------------------------------------------------------------------


def test_smb_extra_fields_passed_to_search_query(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page([], cursor=None)
        run_smb_discovery(_options(), db_path=db)

    assert inst.search_query.called
    _, kwargs = inst.search_query.call_args
    extra = kwargs.get("extra_fields") or []
    expected = get_extra_fields("SMB")
    for field in expected:
        assert field in extra, f"expected SMB extra field missing from call: {field}"
