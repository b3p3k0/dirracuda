"""
Run orchestration for the censys_discovery module.

Entry points:
    run_ftp_discovery(options: CensysRunOptions, db_path=None) -> CensysRunResult
    run_http_discovery(options: CensysRunOptions, db_path=None) -> CensysRunResult

Transaction ownership:
    - Scope-branched preflight (get_org_credits / get_user_credits) fires before any DB I/O.
      Any preflight failure returns ERROR with run_id=None and no DB writes.
    - init_db() + open_connection() handle DB setup; failures return structured RunResult.
    - COMMIT 1 durably records the run row before any search network I/O.
    - _fetch_all_pages() accumulates all pages into a list before returning.
      At that point zero result rows have been inserted, which enables uniform auth cleanup:
      if any page returns AUTH_UNAUTHORIZED or AUTH_FORBIDDEN, the run row is deleted and
      run_id=None is returned — no write persists on auth failure regardless of page number.
    - Non-auth paging errors commit accumulated items with ERROR status (run_id preserved).
    - COMMIT 2 records result rows and final status (done / partial-error).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from experimental.censys_discovery.client import CensysClient
from experimental.censys_discovery.models import (
    AUTH_FORBIDDEN,
    AUTH_UNAUTHORIZED,
    ApiError,
    CensysRunOptions,
    CensysRunResult,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
    SearchResultItem,
)
from experimental.censys_discovery.query_builder import build_query, get_extra_fields
from experimental.censys_discovery.store import (
    init_db,
    insert_result,
    insert_run,
    open_connection,
    update_run,
)

_AUTH_REASON_CODES = frozenset({AUTH_UNAUTHORIZED, AUTH_FORBIDDEN})


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _fetch_all_pages(
    client: CensysClient,
    query: str,
    max_pages: int,
    page_size: int,
    org_id: Optional[str],
    extra_fields: Optional[List[str]] = None,
) -> Tuple[List[SearchResultItem], Optional[ApiError]]:
    """
    Fetch up to max_pages from the Censys search API.

    Returns (items, None) on clean exhaustion.
    Returns (items_so_far, error) if any page call fails — items_so_far contains
    all items successfully retrieved before the error.
    """
    items: List[SearchResultItem] = []
    cursor: Optional[str] = None
    for _ in range(max(1, max_pages)):
        result = client.search_query(
            query, page_size=page_size, page_token=cursor,
            org_id=org_id, extra_fields=extra_fields,
        )
        if not result.ok:
            return items, result.error
        page = result.data
        items.extend(page.items)
        if not page.next_cursor or not page.items:
            break
        cursor = page.next_cursor
    return items, None


def _run_discovery(
    options: CensysRunOptions,
    db_path: Optional[Path],
    expected_protocol: str,
    extra_fields: Optional[List[str]] = None,
) -> CensysRunResult:
    """
    Shared orchestration for all protocol discovery runs.

    Always returns CensysRunResult — never raises.
    """
    # 1. Protocol guard
    try:
        if options.protocol.upper() != expected_protocol:
            return CensysRunResult(
                ok=False, run_id=None, fetched_count=0, deduped_count=0,
                status=RUN_STATUS_ERROR,
                error=f"unsupported protocol for {expected_protocol} service: {options.protocol!r}",
            )
    except Exception as exc:
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=str(exc),
        )

    # 2. Scope-branched auth preflight — no DB I/O before this resolves
    try:
        client = CensysClient(pat=options.pat)
        if options.org_id:
            preflight = client.get_org_credits(options.org_id)
        else:
            preflight = client.get_user_credits()
    except Exception as exc:
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=f"Preflight error: {exc}",
        )
    if not preflight.ok:
        err = preflight.error
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"Preflight failed ({err.reason_code}): {err.message}",
        )

    # 3. DB setup
    try:
        init_db(db_path)
        conn = open_connection(db_path)
    except Exception as exc:
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=f"DB setup failed: {exc}",
        )

    # 4. Build query before COMMIT 1 — query_text is an explicit arg to insert_run
    try:
        query_text = build_query(options.protocol, options.query_hours)
    except Exception as exc:
        conn.close()
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=f"Query build failed: {exc}",
        )

    # 5. COMMIT 1 — durable run row before any search network I/O
    started_at = _utcnow()
    try:
        run_id = insert_run(conn, options, started_at, query_text)
        conn.commit()
    except Exception as exc:
        conn.close()
        return CensysRunResult(
            ok=False, run_id=None, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=f"Run insert failed: {exc}",
        )

    # 6. Fetch all pages, then persist
    try:
        items, page_error = _fetch_all_pages(
            client, query_text, options.max_pages, options.page_size,
            options.org_id, extra_fields,
        )

        # Auth failure on any page: undo run row, return run_id=None
        if page_error is not None and page_error.reason_code in _AUTH_REASON_CODES:
            conn.execute("DELETE FROM censys_runs WHERE run_id = ?", (run_id,))
            conn.commit()
            return CensysRunResult(
                ok=False, run_id=None, fetched_count=0, deduped_count=0,
                status=RUN_STATUS_ERROR,
                error=f"Search auth failed ({page_error.reason_code}): {page_error.message}",
            )

        # Insert accumulated items
        fetched = 0
        deduped = 0
        for item in items:
            if insert_result(conn, run_id, item):
                deduped += 1
            fetched += 1

        if page_error is not None:
            # Non-auth partial error: commit what was accumulated, mark ERROR
            update_run(
                conn, run_id, _utcnow(), fetched, deduped,
                RUN_STATUS_ERROR, page_error.message,
            )
            conn.commit()
            return CensysRunResult(
                ok=False, run_id=run_id, fetched_count=fetched, deduped_count=deduped,
                status=RUN_STATUS_ERROR, error=page_error.message,
            )

        # Clean success
        update_run(conn, run_id, _utcnow(), fetched, deduped, RUN_STATUS_DONE)
        conn.commit()

    except Exception as exc:
        conn.rollback()
        try:
            update_run(conn, run_id, _utcnow(), 0, 0, RUN_STATUS_ERROR, str(exc))
            conn.commit()
        except Exception:
            pass
        return CensysRunResult(
            ok=False, run_id=run_id, fetched_count=0, deduped_count=0,
            status=RUN_STATUS_ERROR, error=str(exc),
        )
    finally:
        conn.close()

    return CensysRunResult(
        ok=True, run_id=run_id, fetched_count=fetched, deduped_count=deduped,
        status=RUN_STATUS_DONE, error=None,
    )


def run_ftp_discovery(
    options: CensysRunOptions,
    db_path: Optional[Path] = None,
) -> CensysRunResult:
    return _run_discovery(options, db_path, "FTP")


def run_http_discovery(
    options: CensysRunOptions,
    db_path: Optional[Path] = None,
) -> CensysRunResult:
    return _run_discovery(options, db_path, "HTTP", extra_fields=get_extra_fields("HTTP"))
