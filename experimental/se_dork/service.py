"""
Run orchestration for the se_dork module.

Entry point:
    run_dork_search(options: RunOptions, db_path=None) -> RunResult

Transaction ownership:
    - Reachability check fires before any DB I/O (avoids creating runtime
      tables against a dead instance without issuing a throwaway search).
    - init_db() and open_connection() handle DB setup; failures return a
      structured RunResult without raising.
    - The run row is committed before network I/O.
    - Each page is inserted, classified, retained, and optionally probed with
      short commits between network phases.
    - No classifier or probe request runs while a SQLite transaction is open.
    - Final status is committed after pagination ends; completed pages remain
      durable if a later fetch fails.
"""

from __future__ import annotations

from concurrent.futures import (
    CancelledError,
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait as _futures_wait,
)
import datetime
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from experimental.se_dork.client import run_reachability_check
from experimental.se_dork.models import (
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS,
    RunOptions,
    RunResult,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
)
from experimental.se_dork.store import (
    count_open_index_results,
    delete_non_open_results_by_ids,
    delete_unclassified_results_by_ids,
    get_results_by_ids,
    init_db,
    insert_page_results,
    insert_run,
    normalize_url,
    open_connection,
    update_result_probe,
    update_result_verdict,
    update_run,
    update_run_progress,
)

_HARD_PAGE_CAP = 40
_FETCH_TIMEOUT = 15
_MAX_RETRY_AFTER_SECONDS = 300
_COOLDOWN_PROGRESS_INTERVAL = 30
_SOFT_BACKOFF_DELAYS = (10.0, 20.0, 30.0)


class _Cancelled(Exception):
    """Raised internally at safe boundaries when cancel_event fires."""


def _coerce_policy_seconds(value: object, *, default: int, lo: int, hi: int) -> int:
    """Clamp a policy value to [lo, hi]; return default on non-numeric input."""
    try:
        return max(lo, min(hi, int(float(str(value)))))
    except (TypeError, ValueError, OverflowError):
        return default


def _is_mature(productive_pages: int, unique_count: int) -> bool:
    return productive_pages >= 5 or unique_count >= 50
_THROTTLE_MARKERS = (
    "403",
    "429",
    "access denied",
    "captcha",
    "cloudflare",
    "rate limit",
    "recaptcha",
    "too many request",
)


@dataclass
class _FetchOutcome:
    rows: list[dict]
    unique_count: int = 0
    pages_fetched: int = 0
    pacing_delay_seconds: float = 0.0
    throttled_page_count: int = 0
    hard_retry_count: int = 0
    hard_retry_delay_seconds: float = 0.0
    throttle_engines: tuple[str, ...] = ()
    stopped_early: bool = False
    warning: Optional[str] = None
    error: Optional[str] = None


@dataclass
class _PageProcessTotals:
    fetched: int = 0
    verified: int = 0
    retained: int = 0
    probe_total: int = 0
    probe_clean: int = 0
    probe_issue: int = 0
    probe_unprobed: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_run_result(
    status: str,
    error: "Optional[str]",
    *,
    run_id: "Optional[int]",
    totals: _PageProcessTotals,
    fetch_outcome: _FetchOutcome,
    probe_enabled: bool,
) -> "RunResult":
    return RunResult(
        run_id=run_id,
        fetched_count=totals.fetched,
        deduped_count=totals.retained,
        status=status,
        error=error,
        verified_count=totals.verified,
        probe_enabled=probe_enabled,
        probe_total=totals.probe_total,
        probe_clean=totals.probe_clean,
        probe_issue=totals.probe_issue,
        probe_unprobed=totals.probe_unprobed,
        pages_fetched=fetch_outcome.pages_fetched,
        pacing_delay_seconds=fetch_outcome.pacing_delay_seconds,
        throttled_page_count=fetch_outcome.throttled_page_count,
        hard_retry_count=fetch_outcome.hard_retry_count,
        hard_retry_delay_seconds=fetch_outcome.hard_retry_delay_seconds,
        throttle_engines=fetch_outcome.throttle_engines,
        stopped_early=fetch_outcome.stopped_early,
        fetch_warning=fetch_outcome.warning,
    )


def _finalize_run_status(db_path, run_id, totals, status, error_text=None):
    c = open_connection(db_path)
    try:
        update_run(c, run_id, _utcnow(), totals.fetched, totals.retained, status, error_text)
        c.commit()
    finally:
        try:
            c.close()
        except Exception:
            pass


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _emit_progress(progress_cb: Optional[Callable[[str], None]], message: str) -> None:
    if progress_cb:
        try:
            progress_cb(message)
        except Exception:
            pass


def _page_delay_seconds(
    page: int,
    *,
    jitter_fn: Optional[Callable[[float, float], float]] = None,
) -> float:
    """Return the adaptive delay before a pagination request."""
    if page <= 1:
        return 0.0
    if page <= 5:
        base_delay = 2.0
    elif page <= 10:
        base_delay = 4.0
    elif page <= 20:
        base_delay = 6.0
    else:
        base_delay = 8.0
    jitter = jitter_fn or random.uniform
    return max(0.0, float(jitter(base_delay * 0.8, base_delay * 1.2)))


def _soft_backoff_seconds(streak: int) -> float:
    """Return the next-page delay after productive throttled responses."""
    if streak <= 0:
        return 0.0
    return _SOFT_BACKOFF_DELAYS[min(streak, len(_SOFT_BACKOFF_DELAYS)) - 1]


def _throttle_engines(payload: dict) -> tuple[str, ...]:
    """Extract engines reporting upstream blocking from a SearXNG response."""
    raw_entries = payload.get("unresponsive_engines")
    if not raw_entries:
        return ()
    if isinstance(raw_entries, dict):
        entries = list(raw_entries.items())
    elif isinstance(raw_entries, (list, tuple)):
        entries = list(raw_entries)
    else:
        entries = [raw_entries]

    engines: list[str] = []
    for entry in entries:
        engine = ""
        detail = ""
        if isinstance(entry, dict):
            engine = str(
                entry.get("engine")
                or entry.get("name")
                or entry.get("source")
                or ""
            ).strip()
            detail = " ".join(str(value) for value in entry.values())
        elif isinstance(entry, (list, tuple)):
            if entry:
                engine = str(entry[0] or "").strip()
            detail = " ".join(str(value) for value in entry[1:])
        else:
            detail = str(entry)

        normalized = detail.lower()
        if any(marker in normalized for marker in _THROTTLE_MARKERS):
            label = engine or "unknown"
            if label not in engines:
                engines.append(label)
    return tuple(engines)


def _retry_after_seconds(
    exc: urllib.error.HTTPError,
    *,
    now_fn: Optional[Callable[[], datetime.datetime]] = None,
) -> Optional[int]:
    """Return a bounded valid Retry-After delay, or None when unavailable."""
    raw_value = None
    try:
        raw_value = exc.headers.get("Retry-After")
    except Exception:
        pass
    if raw_value is None:
        return None

    try:
        delay = int(str(raw_value).strip())
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(raw_value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
            current = (
                now_fn()
                if now_fn is not None
                else datetime.datetime.now(datetime.timezone.utc)
            )
            if current.tzinfo is None:
                current = current.replace(tzinfo=datetime.timezone.utc)
            delay = int((retry_at - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return max(1, min(_MAX_RETRY_AFTER_SECONDS, delay))


def _wait_for_cooldown(
    seconds: int,
    *,
    retry_number: int,
    max_retries: int = 2,
    progress_cb: Optional[Callable[[str], None]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> float:
    sleeper = sleep_fn or time.sleep
    remaining = max(1, int(seconds))
    elapsed = 0.0
    _emit_progress(
        progress_cb,
        f"Upstream retry {retry_number}/{max_retries}: waiting {remaining}s...",
    )
    while remaining > 0:
        interval = min(_COOLDOWN_PROGRESS_INTERVAL, remaining)
        if cancel_event is not None:
            t_before = time.monotonic()
            fired = cancel_event.wait(interval)
            elapsed += min(interval, time.monotonic() - t_before)
            if fired:
                return elapsed
        else:
            sleeper(interval)
            elapsed += interval
        remaining -= interval
        if remaining:
            _emit_progress(
                progress_cb,
                f"Upstream retry {retry_number}/{max_retries}: "
                f"{remaining}s remaining...",
            )
    return elapsed


def _throttle_message(
    engines: tuple[str, ...],
    *,
    throttled_pages: int,
    hard_retries: int,
    persistent: bool,
) -> str:
    engine_text = f" ({', '.join(engines)})" if engines else ""
    if persistent:
        return (
            f"Upstream throttling persisted after {hard_retries} retries; "
            f"pagination stopped early{engine_text}."
        )
    if hard_retries:
        retry_label = "retry" if hard_retries == 1 else "retries"
        return (
            f"Upstream availability recovered after {hard_retries} {retry_label}"
            f"{engine_text}."
        )
    return (
        f"Temporary upstream engine failures affected {throttled_pages} pages; "
        f"continued with soft backoff{engine_text}."
    )


def _fetch_page(
    base_url: str,
    query: str,
    page: int,
    timeout: int = _FETCH_TIMEOUT,
) -> tuple[list[dict], tuple[str, ...]]:
    """Fetch and validate one SearXNG JSON result page."""
    base = base_url.rstrip("/")
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "pageno": page,
    })
    url = f"{base}/search?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read()

    payload = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("SearXNG response is not a JSON object.")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("SearXNG response is missing a valid 'results' list.")
    return results, _throttle_engines(payload)


def _sleep_for_remaining_pacing(
    *,
    completed_page: int,
    next_page: int,
    delay_seconds: float,
    deadline: float,
    progress_cb: Optional[Callable[[str], None]],
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
    cancel_event: Optional[threading.Event] = None,
) -> float:
    remaining = max(0.0, deadline - monotonic_fn())
    if remaining <= 0:
        _emit_progress(
            progress_cb,
            f"Page {completed_page}: processing covered the "
            f"{delay_seconds:.1f}s pacing window; continuing.",
        )
        return 0.0

    _emit_progress(
        progress_cb,
        f"Page {completed_page}: processing complete; waiting "
        f"{remaining:.1f}s before page {next_page}.",
    )
    t0 = monotonic_fn()
    if cancel_event is not None:
        cancel_event.wait(remaining)
        elapsed = min(remaining, monotonic_fn() - t0)
    else:
        sleep_fn(remaining)
        elapsed = remaining
    return elapsed


def _paginate_results(
    base_url: str,
    query: str,
    max_results: int,
    timeout: int = _FETCH_TIMEOUT,
    progress_cb: Optional[Callable[[str], None]] = None,
    *,
    page_processor: Optional[Callable[[int, list[dict]], None]] = None,
    collect_rows: bool = True,
    sleep_fn: Optional[Callable[[float], None]] = None,
    jitter_fn: Optional[Callable[[float, float], float]] = None,
    monotonic_fn: Optional[Callable[[], float]] = None,
    short_retry: int = 30,
    long_retry: int = 180,
    cancel_event: Optional[threading.Event] = None,
) -> _FetchOutcome:
    accumulated: list[dict] = []
    seen_urls: set[str] = set()
    pages_seen: set[int] = set()
    throttled_pages: set[int] = set()
    throttle_labels: list[str] = []
    pacing_delay = 0.0
    hard_retry_delay = 0.0
    hard_retry_count = 0
    productive_pages = 0
    soft_throttle_streak = 0
    retrying_page = False
    stopped_early = False
    stop_warning: Optional[str] = None
    sleeper = sleep_fn or time.sleep

    def _exec_retry(retry_delay: int) -> Optional[bool]:
        # Returns None → stopped_early (caller breaks); True → cancelled; False → continue.
        nonlocal hard_retry_count, hard_retry_delay, retrying_page, stopped_early
        is_m = _is_mature(productive_pages, len(seen_urls))
        mx = 1 if is_m else 2
        if hard_retry_count >= mx:
            stopped_early = True
            return None
        hard_retry_count += 1
        w = _wait_for_cooldown(retry_delay, retry_number=hard_retry_count, max_retries=mx,
                               progress_cb=progress_cb, sleep_fn=sleeper, cancel_event=cancel_event)
        hard_retry_delay += w
        retrying_page = True
        return bool(cancel_event and cancel_event.is_set())
    monotonic = monotonic_fn or time.monotonic

    try:
        page = 1
        while page <= _HARD_PAGE_CAP:
            if cancel_event and cancel_event.is_set():
                raise _Cancelled()
            suffix = " (retry)" if retrying_page else ""
            _emit_progress(progress_cb, f"Querying SearXNG page {page}{suffix}...")
            try:
                results, page_throttles = _fetch_page(
                    base_url,
                    query,
                    page,
                    timeout=timeout,
                )
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    if seen_urls:
                        stopped_early = True
                        stop_warning = (
                            f"SearXNG pagination stopped after page {page - 1}: {exc}"
                        )
                        break
                    raise
                retry_after = _retry_after_seconds(exc)
                rd = (retry_after if retry_after is not None
                      else (short_retry if hard_retry_count == 0 else long_retry))
                r = _exec_retry(rd)
                if r is None: break
                if r: raise _Cancelled()
                continue
            except Exception as exc:
                if seen_urls:
                    stopped_early = True
                    stop_warning = (
                        f"SearXNG pagination stopped after page {page - 1}: {exc}"
                    )
                    break
                raise

            if cancel_event and cancel_event.is_set():
                raise _Cancelled()

            response_received_at = monotonic()
            pages_seen.add(page)

            new_rows: list[dict] = []
            for row in results:
                if not isinstance(row, dict):
                    continue
                normalized_url = normalize_url(str(row.get("url") or "").strip())
                if not normalized_url or normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                new_rows.append(row)
                if len(seen_urls) >= max_results:
                    break
            if collect_rows:
                accumulated.extend(new_rows)
            _emit_progress(
                progress_cb,
                f"Page {page}: received {len(results)} results, "
                f"{len(new_rows)} new ({len(seen_urls)} unique total).",
            )

            if results:
                if new_rows and page_processor is not None:
                    page_processor(page, new_rows)
                    productive_pages += 1

                if len(seen_urls) >= max_results or page >= _HARD_PAGE_CAP:
                    break

                for engine in page_throttles:
                    if engine not in throttle_labels:
                        throttle_labels.append(engine)
                if page_throttles:
                    throttled_pages.add(page)
                    soft_throttle_streak = min(
                        soft_throttle_streak + 1,
                        len(_SOFT_BACKOFF_DELAYS),
                    )
                    _emit_progress(
                        progress_cb,
                        f"Page {page}: {len(page_throttles)} upstream engine "
                        "warning(s); continuing with soft backoff.",
                    )
                else:
                    soft_throttle_streak = 0

                next_page = page + 1
                delay = (
                    _soft_backoff_seconds(soft_throttle_streak)
                    or _page_delay_seconds(next_page, jitter_fn=jitter_fn)
                )
                if delay:
                    pacing_delay += _sleep_for_remaining_pacing(
                        completed_page=page,
                        next_page=next_page,
                        delay_seconds=delay,
                        deadline=response_received_at + delay,
                        progress_cb=progress_cb,
                        sleep_fn=sleeper,
                        monotonic_fn=monotonic,
                        cancel_event=cancel_event,
                    )
                    if cancel_event and cancel_event.is_set():
                        raise _Cancelled()
                retrying_page = False
                page = next_page
                continue

            for engine in page_throttles:
                if engine not in throttle_labels:
                    throttle_labels.append(engine)
            if page_throttles:
                rd = short_retry if hard_retry_count == 0 else long_retry
                r = _exec_retry(rd)
                if r is None: break
                if r: raise _Cancelled()
                continue

            break

    except _Cancelled:
        stopped_early = True
        if not stop_warning:
            stop_warning = "Cancelled."

    warning = None
    error = None
    if stopped_early:
        warning = stop_warning or _throttle_message(
            tuple(throttle_labels),
            throttled_pages=len(throttled_pages),
            hard_retries=hard_retry_count,
            persistent=True,
        )
        _emit_progress(progress_cb, warning)
        if not seen_urls:
            error = warning
            warning = None
    elif throttled_pages or hard_retry_count:
        warning = _throttle_message(
            tuple(throttle_labels),
            throttled_pages=len(throttled_pages),
            hard_retries=hard_retry_count,
            persistent=False,
        )
        _emit_progress(progress_cb, warning)

    return _FetchOutcome(
        rows=accumulated,
        unique_count=len(seen_urls),
        pages_fetched=len(pages_seen),
        pacing_delay_seconds=pacing_delay,
        throttled_page_count=len(throttled_pages),
        hard_retry_count=hard_retry_count,
        hard_retry_delay_seconds=hard_retry_delay,
        throttle_engines=tuple(throttle_labels),
        stopped_early=stopped_early,
        warning=warning,
        error=error,
    )


# _fetch_results: collect_rows=True is already the default in _paginate_results.
_fetch_results = _paginate_results


# ---------------------------------------------------------------------------
# Sequential page processing
# ---------------------------------------------------------------------------


def _cleanup_unclassified_page(
    db_path: Optional[Path],
    result_ids: list[int],
) -> None:
    """Remove page rows that never received a durable classification."""
    if not result_ids:
        return
    try:
        conn = open_connection(db_path)
        try:
            delete_unclassified_results_by_ids(conn, result_ids)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _classify_page_rows(
    page: int,
    rows: list[dict],
    *,
    timeout: float = 10.0,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> list[tuple[dict, object]]:
    """Classify one page without holding a database connection."""
    from experimental.se_dork.classifier import (
        ClassifyResult,
        VERDICT_ERROR,
        classify_url,
    )

    outcomes: list[tuple[dict, object]] = []
    total = len(rows)
    step = max(1, total // 10)
    for index, row in enumerate(rows):
        if cancel_event and cancel_event.is_set():
            raise _Cancelled()
        try:
            outcome = classify_url(row["url"], timeout=timeout)
        except Exception as exc:
            outcome = ClassifyResult(
                verdict=VERDICT_ERROR,
                reason_code=str(exc) or "classification_error",
                http_status=None,
            )
        outcomes.append((row, outcome))
        if (index + 1) % step == 0 or index + 1 == total:
            _emit_progress(
                progress_cb,
                f"Page {page}: classifying {index + 1}/{total}...",
            )
    return outcomes


def _persist_page_classification(
    *,
    run_id: int,
    inserted_rows: list[dict],
    classifications: list[tuple[dict, object]],
    totals: _PageProcessTotals,
    db_path: Optional[Path],
) -> tuple[list[dict], int]:
    """Persist verdicts, retain open rows, and update cumulative run counts."""
    result_ids = [int(row["result_id"]) for row in inserted_rows]
    conn = open_connection(db_path)
    try:
        checked_at = _utcnow()
        for row, outcome in classifications:
            update_result_verdict(
                conn,
                int(row["result_id"]),
                outcome.verdict,
                outcome.reason_code,
                outcome.http_status,
                checked_at,
            )
        delete_non_open_results_by_ids(conn, result_ids)
        retained_rows = get_results_by_ids(conn, result_ids)
        retained_total = count_open_index_results(conn, run_id)
        update_run_progress(
            conn,
            run_id,
            totals.fetched + len(inserted_rows),
            retained_total,
            totals.verified + len(classifications),
        )
        conn.commit()
        return retained_rows, retained_total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _probe_page_rows(
    page: int,
    rows: list[dict],
    *,
    config_path: Optional[str],
    indicator_patterns: list[tuple[str, object]],
    worker_count: int,
    progress_cb: Optional[Callable[[str], None]],
    cancel_event: Optional[threading.Event] = None,
) -> tuple[list[tuple[dict, object]], dict[str, int]]:
    from experimental.se_dork.probe import (
        ProbeOutcome,
        PROBE_STATUS_CLEAN,
        PROBE_STATUS_ISSUE,
        PROBE_STATUS_UNPROBED,
        probe_url,
    )

    counts = {"total": 0, "clean": 0, "issue": 0, "unprobed": 0}
    if not rows:
        return [], counts

    outcomes: list[tuple[dict, object]] = []
    max_workers = max(1, min(worker_count, len(rows)))
    step = max(1, len(rows) // 10)
    rows_iter = iter(rows)
    active: dict = {}  # future → row

    def _fill() -> None:
        while len(active) < max_workers:
            if cancel_event and cancel_event.is_set():
                return
            try:
                row = next(rows_iter)
            except StopIteration:
                return
            f = executor.submit(
                probe_url,
                row["url"],
                config_path=config_path,
                max_directories=3,
                max_files=5,
                timeout_seconds=10,
                indicator_patterns=indicator_patterns,
                cancel_event=cancel_event,
            )
            active[f] = row

    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="se-dork-probe",
    ) as executor:
        _fill()
        while active:
            done, _ = _futures_wait(set(active), return_when=FIRST_COMPLETED)
            for future in done:
                row = active.pop(future)
                try:
                    outcome = future.result()
                except CancelledError:
                    continue  # cancelled before execution; skip
                except Exception as exc:
                    outcome = ProbeOutcome(
                        probe_status=PROBE_STATUS_UNPROBED,
                        probe_indicator_matches=0,
                        probe_preview=None,
                        probe_checked_at=_utcnow(),
                        probe_error=str(exc),
                    )
                outcomes.append((row, outcome))
                counts["total"] += 1
                if outcome.probe_status == PROBE_STATUS_ISSUE:
                    counts["issue"] += 1
                elif outcome.probe_status == PROBE_STATUS_CLEAN:
                    counts["clean"] += 1
                else:
                    counts["unprobed"] += 1
                if counts["total"] % step == 0 or counts["total"] == len(rows):
                    _emit_progress(
                        progress_cb,
                        f"Page {page}: probing {counts['total']}/{len(rows)}...",
                    )

            if cancel_event and cancel_event.is_set():
                # Cancel queued futures; running ones continue and are drained.
                for f in list(active):
                    f.cancel()
                # Do NOT break — continue draining until active is empty.
            else:
                _fill()

    return outcomes, counts


def _persist_probe_outcomes(
    db_path: Optional[Path],
    outcomes: list[tuple[dict, object]],
) -> None:
    """Persist a page's completed probe outcomes in one short transaction."""
    if not outcomes:
        return
    conn = open_connection(db_path)
    try:
        for row, outcome in outcomes:
            update_result_probe(
                conn,
                result_id=int(row["result_id"]),
                probe_status=outcome.probe_status,
                probe_indicator_matches=outcome.probe_indicator_matches,
                probe_preview=outcome.probe_preview,
                probe_checked_at=outcome.probe_checked_at,
                probe_error=outcome.probe_error,
                probe_snapshot_payload=getattr(
                    outcome,
                    "probe_snapshot_payload",
                    None,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _process_result_page(
    *,
    run_id: int,
    page: int,
    rows: list[dict],
    totals: _PageProcessTotals,
    db_path: Optional[Path],
    probe_enabled: bool,
    probe_config_path: Optional[str],
    probe_worker_count: int,
    indicator_patterns: list[tuple[str, object]],
    progress_cb: Optional[Callable[[str], None]],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Persist, classify, retain, and optionally probe one fetched page."""
    if cancel_event and cancel_event.is_set():
        raise _Cancelled()

    conn = open_connection(db_path)
    try:
        inserted_rows = insert_page_results(conn, run_id, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _emit_progress(
        progress_cb,
        f"Page {page}: stored {len(inserted_rows)} rows.",
    )
    if not inserted_rows:
        return

    # Wrap classify + persist under a committed flag so _Cancelled raised at
    # any point before the commit triggers cleanup of unclassified rows.
    result_ids = [int(row["result_id"]) for row in inserted_rows]
    classification_committed = False
    try:
        if cancel_event and cancel_event.is_set():
            raise _Cancelled()

        classifications = _classify_page_rows(
            page,
            inserted_rows,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
        )
        retained_rows, retained_total = _persist_page_classification(
            run_id=run_id,
            inserted_rows=inserted_rows,
            classifications=classifications,
            totals=totals,
            db_path=db_path,
        )
        classification_committed = True

        # Update totals BEFORE cancel check so they are never stale on cancel.
        totals.fetched += len(inserted_rows)
        totals.verified += len(classifications)
        totals.retained = retained_total

        if cancel_event and cancel_event.is_set():
            raise _Cancelled()
    except Exception:
        if not classification_committed:
            _cleanup_unclassified_page(db_path, result_ids)
        raise

    _emit_progress(
        progress_cb,
        f"Page {page}: classified {len(classifications)}; retained "
        f"{len(retained_rows)} open indexes.",
    )

    if not probe_enabled or not retained_rows:
        return

    if cancel_event and cancel_event.is_set():
        raise _Cancelled()

    probe_outcomes, probe_counts = _probe_page_rows(
        page,
        retained_rows,
        config_path=probe_config_path,
        indicator_patterns=indicator_patterns,
        worker_count=probe_worker_count,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
    )
    try:
        _persist_probe_outcomes(db_path, probe_outcomes)
    except Exception as exc:
        _emit_progress(
            progress_cb,
            f"Page {page}: probe results could not be saved: {exc}",
        )
        probe_counts = {
            "total": len(retained_rows),
            "clean": 0,
            "issue": 0,
            "unprobed": len(retained_rows),
        }

    # Update probe totals BEFORE cancel check.
    totals.probe_total += probe_counts["total"]
    totals.probe_clean += probe_counts["clean"]
    totals.probe_issue += probe_counts["issue"]
    totals.probe_unprobed += probe_counts["unprobed"]

    if cancel_event and cancel_event.is_set():
        raise _Cancelled()

    _emit_progress(
        progress_cb,
        f"Page {page}: probed {probe_counts['total']} "
        f"({probe_counts['clean']} clean, {probe_counts['issue']} flagged, "
        f"{probe_counts['unprobed']} unprobed).",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_dork_search(
    options: RunOptions,
    db_path: Optional[Path] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> RunResult:
    """
    Run a dork search against the configured SearXNG instance.

    Always returns a RunResult — never raises.
    """
    def _emit(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    request_timeout = _coerce_policy_seconds(
        getattr(options, "request_timeout", 15), default=15, lo=5, hi=60)
    short_retry = _coerce_policy_seconds(
        getattr(options, "short_retry_delay", 30), default=30, lo=5, hi=60)
    long_retry = _coerce_policy_seconds(
        getattr(options, "long_retry_delay", 180), default=180, lo=60, hi=300)

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

    totals = _PageProcessTotals()
    fetch_outcome = _FetchOutcome(rows=[])

    if cancel_event and cancel_event.is_set():
        return _make_run_result(RUN_STATUS_CANCELLED, None, run_id=None, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)

    _emit(f"Reachability: checking {options.instance_url}...")
    try:
        preflight = run_reachability_check(options.instance_url, request_timeout)
    except Exception as exc:
        _emit(f"Reachability error: {exc}")
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"Reachability error: {exc}",
            probe_enabled=bulk_probe_enabled,
        )
    if not preflight.ok:
        _emit(f"Reachability failed: {preflight.message}")
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=(
                f"Reachability failed ({preflight.reason_code}): "
                f"{preflight.message}"
            ),
            probe_enabled=bulk_probe_enabled,
        )
    _emit("Instance reachable")

    if cancel_event and cancel_event.is_set():
        return _make_run_result(RUN_STATUS_CANCELLED, None, run_id=None, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)

    _emit("Opening database...")
    try:
        init_db(db_path)
    except Exception as exc:
        _emit(f"Database error: {exc}")
        return RunResult(
            run_id=None,
            fetched_count=0,
            deduped_count=0,
            status=RUN_STATUS_ERROR,
            error=f"DB setup failed: {exc}",
            probe_enabled=bulk_probe_enabled,
        )

    if cancel_event and cancel_event.is_set():
        return _make_run_result(RUN_STATUS_CANCELLED, None, run_id=None, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)

    # Clamp max_results; insert a durable RUNNING row.
    try:
        max_results = max(1, min(MAX_RESULTS, int(options.max_results)))
    except (TypeError, ValueError):
        max_results = DEFAULT_MAX_RESULTS
    clamped_opts = RunOptions(
        instance_url=options.instance_url,
        query=options.query,
        max_results=max_results,
        bulk_probe_enabled=bulk_probe_enabled,
        probe_config_path=probe_config_path,
        probe_worker_count=probe_worker_count,
    )
    started_at = _utcnow()
    run_id: Optional[int] = None
    conn = None
    try:
        conn = open_connection(db_path)
        if cancel_event and cancel_event.is_set():
            return _make_run_result(RUN_STATUS_CANCELLED, None, run_id=None, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)
        run_id = insert_run(conn, clamped_opts, started_at)
        conn.commit()
    except Exception as exc:
        _emit(f"Run setup failed: {exc}")
        return _make_run_result(RUN_STATUS_ERROR, f"Run setup failed: {exc}", run_id=None, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _emit(f"Run registered (#{run_id}). Starting fetch...")

    indicator_patterns: list[tuple[str, object]] = []
    if bulk_probe_enabled:
        try:
            from experimental.se_dork.probe import build_indicator_patterns

            indicator_patterns = build_indicator_patterns(probe_config_path)
        except Exception as exc:
            _emit(f"Probe indicator setup warning: {exc}")

    def _process_page(page: int, rows: list[dict]) -> None:
        try:
            _process_result_page(
                run_id=run_id,
                page=page,
                rows=rows,
                totals=totals,
                db_path=db_path,
                probe_enabled=bulk_probe_enabled,
                probe_config_path=probe_config_path,
                probe_worker_count=probe_worker_count,
                indicator_patterns=indicator_patterns,
                progress_cb=_emit,
                cancel_event=cancel_event,
            )
        except _Cancelled:
            raise
        except Exception as exc:
            raise RuntimeError(f"Page {page} processing failed: {exc}") from exc

    try:
        if cancel_event and cancel_event.is_set():
            raise _Cancelled()

        fetch_outcome = _paginate_results(
            options.instance_url,
            options.query,
            max_results,
            timeout=request_timeout,
            progress_cb=_emit,
            page_processor=_process_page,
            collect_rows=False,
            short_retry=short_retry,
            long_retry=long_retry,
            cancel_event=cancel_event,
        )

        if cancel_event and cancel_event.is_set():
            raise _Cancelled()

        if fetch_outcome.error and fetch_outcome.unique_count == 0:
            raise RuntimeError(fetch_outcome.error)

        final_conn = open_connection(db_path)
        try:
            update_run(
                final_conn,
                run_id,
                _utcnow(),
                totals.fetched,
                totals.retained,
                RUN_STATUS_DONE,
            )
            final_conn.commit()
        finally:
            final_conn.close()
        _emit(
            f"Run complete: fetched {totals.fetched}, verified "
            f"{totals.verified}, retained {totals.retained} open indexes."
        )

    except _Cancelled:
        try:
            _finalize_run_status(db_path, run_id, totals, RUN_STATUS_CANCELLED)
            _emit("Cancelled.")
            return _make_run_result(RUN_STATUS_CANCELLED, None, run_id=run_id, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)
        except Exception as _e:
            _fin_err = f"Cancellation finalization failed: {_e}"
            _emit(_fin_err)
            return _make_run_result(RUN_STATUS_ERROR, _fin_err, run_id=run_id, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)

    except Exception as exc:
        error_text = str(exc)
        error_label = (
            "Processing error"
            if error_text.startswith("Page ") and " processing failed:" in error_text
            else "Fetch error"
        )
        _emit(f"{error_label}: {error_text}")
        try:
            _finalize_run_status(db_path, run_id, totals, RUN_STATUS_ERROR, error_text)
        except Exception:
            pass
        return _make_run_result(RUN_STATUS_ERROR, error_text, run_id=run_id, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)

    return _make_run_result(RUN_STATUS_DONE, None, run_id=run_id, totals=totals, fetch_outcome=fetch_outcome, probe_enabled=bulk_probe_enabled)
