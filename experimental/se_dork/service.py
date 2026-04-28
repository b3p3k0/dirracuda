"""
Run orchestration for the se_dork module.

Entry point:
    run_dork_search(options: RunOptions, db_path=None) -> RunResult

Transaction ownership:
    - Preflight fires before any DB I/O (avoids creating the sidecar file on a
      dead instance).
    - init_db() and open_connection() handle DB setup; failures return a
      structured RunResult without raising.
    - COMMIT 1 durably records the run row (status=running) before network I/O.
    - COMMIT 2 records result rows and final status (done/error).
    - If the post-fetch phase raises, the partial result inserts are rolled back
      and a best-effort error-status update is committed against the durable run row.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from experimental.se_dork.client import run_preflight
from experimental.se_dork.models import (
    RunOptions,
    RunResult,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
)
from experimental.se_dork.store import (
    count_open_index_results,
    delete_non_open_results,
    get_pending_results,
    get_results_for_run,
    init_db,
    insert_result,
    insert_run,
    open_connection,
    update_result_probe,
    update_result_verdict,
    update_run,
    update_run_verified_count,
)

_HARD_PAGE_CAP = 10
_FETCH_TIMEOUT = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _fetch_results(
    base_url: str,
    query: str,
    max_results: int,
    timeout: int = _FETCH_TIMEOUT,
) -> List[dict]:
    """
    Fetch up to max_results raw result dicts from SearXNG.

    Paginates using the ``pageno`` parameter (SearXNG's canonical name).
    Stops when accumulated >= max_results, SearXNG returns an empty results
    list, or _HARD_PAGE_CAP pages have been fetched.

    Raises urllib.error.URLError or ValueError on fetch/parse failure.
    """
    base = base_url.rstrip("/")
    accumulated: List[dict] = []

    for page in range(1, _HARD_PAGE_CAP + 1):
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "pageno": page,
        })
        url = f"{base}/search?{params}"

        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()

        payload = json.loads(raw.decode("utf-8", errors="replace"))
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            break

        accumulated.extend(results)
        if len(accumulated) >= max_results:
            break

    return accumulated


# ---------------------------------------------------------------------------
# C4: Classification helper
# ---------------------------------------------------------------------------


def _classify_run_results(
    run_id: int,
    db_path: Optional[Path],
    timeout: float = 10.0,
) -> int:
    """
    Open a fresh connection, classify all pending results for run_id,
    write verdicts, update verified_count, commit, close.

    Returns number of rows classified. Never raises — returns 0 on any error.
    """
    from experimental.se_dork.classifier import classify_url
    try:
        conn = open_connection(db_path)
        try:
            rows = get_pending_results(conn, run_id)
            checked_at = _utcnow()
            verified = 0
            for row in rows:
                result = classify_url(row["url"], timeout=timeout)
                update_result_verdict(
                    conn,
                    row["result_id"],
                    result.verdict,
                    result.reason_code,
                    result.http_status,
                    checked_at,
                )
                verified += 1
            update_run_verified_count(conn, run_id, verified)
            conn.commit()
            return verified
        finally:
            conn.close()
    except Exception:
        return 0


def _retain_open_index_only_for_run(
    run_id: int,
    fetched_count: int,
    db_path: Optional[Path],
) -> int:
    """
    Enforce OPEN_INDEX-only retention for one run.

    Deletes non-open rows, recalculates retained OPEN_INDEX count, and updates
    dork_runs.deduped_count to match retained rows.
    """
    conn = open_connection(db_path)
    try:
        delete_non_open_results(conn, run_id=run_id)
        retained_count = count_open_index_results(conn, run_id=run_id)
        update_run(conn, run_id, _utcnow(), fetched_count, retained_count, RUN_STATUS_DONE)
        conn.commit()
        return retained_count
    finally:
        conn.close()


def _probe_run_results(
    run_id: int,
    db_path: Optional[Path],
    *,
    config_path: Optional[str] = None,
    max_directories: int = 3,
    max_files: int = 5,
    timeout_seconds: int = 10,
    worker_count: int = 3,
) -> dict[str, int]:
    """
    Probe retained rows for one run and persist probe state.

    Returns probe summary counts:
      {
        "total": int,
        "clean": int,
        "issue": int,
        "unprobed": int,
      }
    Never raises.
    """
    from experimental.se_dork.probe import (
        ProbeOutcome,
        PROBE_STATUS_CLEAN,
        PROBE_STATUS_ISSUE,
        PROBE_STATUS_UNPROBED,
        build_indicator_patterns,
        probe_url,
    )

    counts = {"total": 0, "clean": 0, "issue": 0, "unprobed": 0}

    try:
        conn = open_connection(db_path)
    except Exception:
        return counts

    try:
        rows = get_results_for_run(conn, run_id)
        if not rows:
            return counts

        patterns = build_indicator_patterns(config_path)
        try:
            resolved_workers = max(1, min(8, int(worker_count)))
        except (TypeError, ValueError):
            resolved_workers = 3
        max_workers = max(1, min(resolved_workers, len(rows)))

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="se-dork-probe",
        ) as executor:
            future_to_row = {
                executor.submit(
                    probe_url,
                    row["url"],
                    config_path=config_path,
                    max_directories=max_directories,
                    max_files=max_files,
                    timeout_seconds=timeout_seconds,
                    indicator_patterns=patterns,
                ): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = ProbeOutcome(
                        probe_status=PROBE_STATUS_UNPROBED,
                        probe_indicator_matches=0,
                        probe_preview=None,
                        probe_checked_at=_utcnow(),
                        probe_error=str(exc),
                    )

                update_result_probe(
                    conn,
                    result_id=row["result_id"],
                    probe_status=outcome.probe_status,
                    probe_indicator_matches=outcome.probe_indicator_matches,
                    probe_preview=outcome.probe_preview,
                    probe_checked_at=outcome.probe_checked_at,
                    probe_error=outcome.probe_error,
                )

                counts["total"] += 1
                if outcome.probe_status == PROBE_STATUS_ISSUE:
                    counts["issue"] += 1
                elif outcome.probe_status == PROBE_STATUS_CLEAN:
                    counts["clean"] += 1
                elif outcome.probe_status == PROBE_STATUS_UNPROBED:
                    counts["unprobed"] += 1
                else:
                    counts["unprobed"] += 1

        conn.commit()
        return counts
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_dork_search(
    options: RunOptions,
    db_path: Optional[Path] = None,
) -> RunResult:
    """
    Run a dork search against the configured SearXNG instance.

    Always returns a RunResult — never raises.
    """
    # 1. Preflight — no DB I/O yet
    bulk_probe_enabled = bool(getattr(options, "bulk_probe_enabled", False))
    probe_config_path = getattr(options, "probe_config_path", None)
    raw_probe_worker_count = getattr(options, "probe_worker_count", None)
    try:
        probe_worker_count = (
            max(1, min(8, int(raw_probe_worker_count)))
            if raw_probe_worker_count is not None
            else 3
        )
    except (TypeError, ValueError):
        probe_worker_count = 3

    try:
        preflight = run_preflight(options.instance_url)
    except Exception as exc:
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"Preflight error: {exc}",
            probe_enabled=bulk_probe_enabled,
        )
    if not preflight.ok:
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"Preflight failed ({preflight.reason_code}): {preflight.message}",
            probe_enabled=bulk_probe_enabled,
        )

    # 2. DB setup — structured error if this fails
    try:
        init_db(db_path)
        conn = open_connection(db_path)
    except Exception as exc:
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"DB setup failed: {exc}",
            probe_enabled=bulk_probe_enabled,
        )

    # 3. Clamp max_results; insert durable run row (COMMIT 1 + clamping fix)
    try:
        max_results = max(1, min(500, int(options.max_results)))
    except (TypeError, ValueError):
        max_results = 50
    clamped_opts = RunOptions(
        instance_url=options.instance_url,
        query=options.query,
        max_results=max_results,
        bulk_probe_enabled=bulk_probe_enabled,
        probe_config_path=probe_config_path,
        probe_worker_count=probe_worker_count,
    )
    started_at = _utcnow()
    try:
        run_id = insert_run(conn, clamped_opts, started_at)
        conn.commit()  # COMMIT 1: run row is durable before any network I/O
    except Exception as exc:
        conn.close()
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"Run insert failed: {exc}",
            probe_enabled=bulk_probe_enabled,
        )

    # 4. Fetch + persist results
    try:
        raw_rows = _fetch_results(
            options.instance_url, options.query, max_results
        )
        capped = raw_rows[:max_results]
        fetched = len(capped)
        deduped = 0
        for row in capped:
            if insert_result(conn, run_id, row):
                deduped += 1
        update_run(conn, run_id, _utcnow(), fetched, deduped, RUN_STATUS_DONE)
        conn.commit()  # COMMIT 2: results + final status
    except Exception as exc:
        conn.rollback()  # undo partial result inserts
        try:
            update_run(conn, run_id, _utcnow(), 0, 0, RUN_STATUS_ERROR, str(exc))
            conn.commit()
        except Exception:
            pass  # best-effort; run row is already durable from COMMIT 1
        return RunResult(
            run_id=run_id,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=str(exc),
            probe_enabled=bulk_probe_enabled,
        )
    finally:
        conn.close()  # always closes — on success and on exception path

    # 5. Classify results (COMMIT 3 — best-effort, never fails the run)
    verified_count = 0
    try:
        verified_count = _classify_run_results(run_id, db_path)
    except Exception:
        pass

    # 6. Retain OPEN_INDEX only (best-effort). deduped_count becomes retained count.
    retained_count = deduped
    try:
        retained_count = _retain_open_index_only_for_run(run_id, fetched, db_path)
    except Exception:
        pass

    # 7. Optional bulk probe over retained rows only (best-effort).
    probe_total = 0
    probe_clean = 0
    probe_issue = 0
    probe_unprobed = 0
    if bulk_probe_enabled:
        summary = _probe_run_results(
            run_id,
            db_path,
            config_path=probe_config_path,
            worker_count=probe_worker_count,
        )
        probe_total = summary.get("total", 0)
        probe_clean = summary.get("clean", 0)
        probe_issue = summary.get("issue", 0)
        probe_unprobed = summary.get("unprobed", 0)

    return RunResult(
        run_id=run_id,
        fetched_count=fetched,
        deduped_count=retained_count,
        status=RUN_STATUS_DONE,
        error=None,
        verified_count=verified_count,
        probe_enabled=bulk_probe_enabled,
        probe_total=probe_total,
        probe_clean=probe_clean,
        probe_issue=probe_issue,
        probe_unprobed=probe_unprobed,
    )
