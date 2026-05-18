"""
Unit tests for experimental.censys_discovery.service.

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
from experimental.censys_discovery.service import run_ftp_discovery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _options(**kwargs) -> CensysRunOptions:
    defaults = dict(
        pat="testpat-secret",
        protocol="FTP",
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


def _items(n: int, base: str = "1.2.3") -> list:
    return [
        SearchResultItem(
            ip_address=f"{base}.{i + 1}",
            port=21,
            protocol="FTP",
            transport_protocol="TCP",
            banner=f"220 FTP server {i}",
            scan_time="2026-05-14T00:00:00",
            source_json='{"port":21}',
        )
        for i in range(n)
    ]


def _count_rows(db: Path, table: str) -> int:
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Preflight failure — user-scoped
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_auth_unauthorized_no_db_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_run_ftp_discovery_auth_forbidden_no_db_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_FORBIDDEN)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_run_ftp_discovery_preflight_network_error_no_db_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    net_err = ClientResult(
        ok=False, data=None,
        error=ApiError(NETWORK_ERROR, None, "connection refused"),
    )
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = net_err
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_run_ftp_discovery_preflight_exception_no_db_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.side_effect = RuntimeError("unexpected")
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


# ---------------------------------------------------------------------------
# Preflight failure — org-scoped
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_org_scope_uses_org_credits_preflight(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page([], cursor=None)
        run_ftp_discovery(_options(org_id="org-123"), db_path=db)

    inst.get_org_credits.assert_called_once_with("org-123")
    inst.get_user_credits.assert_not_called()


def test_run_ftp_discovery_org_scope_auth_fail_no_db_write(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_FORBIDDEN)
        result = run_ftp_discovery(_options(org_id="org-456"), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert not db.exists()


def test_run_ftp_discovery_requires_org_id(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    result = run_ftp_discovery(_options(org_id=None), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert "organization_id" in (result.error or "")
    assert not db.exists()


# ---------------------------------------------------------------------------
# DB setup failure
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_db_setup_failure(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _ok_credits()
        with patch("experimental.censys_discovery.service.init_db", side_effect=RuntimeError("disk full")):
            result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert "DB setup failed" in result.error


# ---------------------------------------------------------------------------
# Auth failure — page 1 (uniform no-write policy)
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_page1_auth_unauthorized_deletes_run_row(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert _count_rows(db, "censys_runs") == 0
    assert _count_rows(db, "censys_results") == 0


def test_run_ftp_discovery_page1_auth_forbidden_deletes_run_row(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _fail_auth(AUTH_FORBIDDEN)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert _count_rows(db, "censys_runs") == 0
    assert _count_rows(db, "censys_results") == 0


# ---------------------------------------------------------------------------
# Auth failure — page 2+ (uniform no-write policy)
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_page2_auth_forbidden_deletes_run_row_no_results(tmp_path: Path) -> None:
    """Page 1 returns 3 items (accumulated); page 2 returns AUTH_FORBIDDEN — zero rows in DB."""
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.side_effect = [
            _search_page(_items(3), cursor="tok1"),
            _fail_auth(AUTH_FORBIDDEN),
        ]
        result = run_ftp_discovery(_options(max_pages=2), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert _count_rows(db, "censys_runs") == 0
    assert _count_rows(db, "censys_results") == 0


# ---------------------------------------------------------------------------
# Non-auth error — partial data committed, run row retained
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_page2_network_error_commits_partial_data(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.side_effect = [
            _search_page(_items(3), cursor="tok1"),
            _search_error(NETWORK_ERROR),
        ]
        result = run_ftp_discovery(_options(max_pages=2), db_path=db)

    assert result.ok is False
    assert result.run_id is not None
    assert result.status == RUN_STATUS_ERROR
    assert result.fetched_count == 3
    assert _count_rows(db, "censys_runs") == 1
    assert _count_rows(db, "censys_results") == 3


# ---------------------------------------------------------------------------
# COMMIT 1 durability — non-auth first-page error
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_network_error_after_commit1(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_error(NETWORK_ERROR)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is not None
    assert result.status == RUN_STATUS_ERROR
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT status FROM censys_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == RUN_STATUS_ERROR


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_success_returns_ok(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page(_items(5), cursor=None)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is True
    assert result.run_id is not None
    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 5
    assert result.deduped_count == 5
    assert result.error is None


def test_run_ftp_discovery_success_persists_to_db(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page(_items(4), cursor=None)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is True
    assert _count_rows(db, "censys_runs") == 1
    assert _count_rows(db, "censys_results") == 4


def test_run_ftp_discovery_deduped_count_correct(tmp_path: Path) -> None:
    """Two items sharing the same dedupe key: fetched=2, deduped=1."""
    db = tmp_path / "censys.db"
    same_item = SearchResultItem(
        ip_address="10.0.0.1", port=21, protocol="FTP",
        transport_protocol="TCP", banner=None, scan_time=None,
        source_json='{"port":21}',
    )
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page([same_item, same_item], cursor=None)
        result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is True
    assert result.fetched_count == 2
    assert result.deduped_count == 1


def test_run_ftp_discovery_respects_max_pages(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        # Always returns a cursor — would loop unbounded without the max_pages cap
        inst.search_query.return_value = _search_page(_items(2), cursor="always")
        run_ftp_discovery(_options(max_pages=3), db_path=db)

    assert inst.search_query.call_count <= 3


def test_run_ftp_discovery_stops_when_no_next_cursor(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        inst = mock_cls.return_value
        inst.get_org_credits.return_value = _ok_credits()
        inst.search_query.return_value = _search_page(_items(2), cursor=None)
        run_ftp_discovery(_options(max_pages=5), db_path=db)

    assert inst.search_query.call_count == 1


# ---------------------------------------------------------------------------
# PAT safety
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_error_message_never_contains_pat(tmp_path: Path) -> None:
    unique_pat = "SUPER-SECRET-PAT-XYZ-12345"
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _fail_auth(AUTH_UNAUTHORIZED)
        result = run_ftp_discovery(_options(pat=unique_pat), db_path=db)

    assert result.error is not None
    assert unique_pat not in result.error


# ---------------------------------------------------------------------------
# Run insert failure (COMMIT 1)
# ---------------------------------------------------------------------------


def test_run_ftp_discovery_insert_run_fails_returns_no_run_id(tmp_path: Path) -> None:
    db = tmp_path / "censys.db"
    with patch("experimental.censys_discovery.service.CensysClient") as mock_cls:
        mock_cls.return_value.get_org_credits.return_value = _ok_credits()
        with patch("experimental.censys_discovery.service.insert_run", side_effect=RuntimeError("DB locked")):
            result = run_ftp_discovery(_options(), db_path=db)

    assert result.ok is False
    assert result.run_id is None
    assert result.status == RUN_STATUS_ERROR
    assert "Run insert failed" in result.error
