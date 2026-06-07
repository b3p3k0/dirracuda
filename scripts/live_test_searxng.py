#!/usr/bin/env python3
"""
Live end-to-end validation of the SearXNG dork pipeline.  C11D.

Import-safe: pytest may import helpers, but import never executes live behavior.
Requires --confirm-live before any network access.
"""
from __future__ import annotations

import argparse
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experimental.se_dork.models import (
    DEFAULT_MAX_RESULTS,
    RunOptions,
    RunResult,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_DONE,
)
from experimental.se_dork.service import run_dork_search
from shared.config_store import get_config_store
from shared.path_service import get_paths

# ---------------------------------------------------------------------------
# Progress-message patterns (exact strings from service.py)
# ---------------------------------------------------------------------------
_RE_PAGE_START = re.compile(r"^Querying SearXNG page (\d+)\.\.\.$")
_RE_PAGE_RETRY = re.compile(r"^Querying SearXNG page (\d+) \(retry\)\.\.\.$")
_RE_PAGE_RECEIVED = re.compile(
    r"^Page (\d+): received \d+ results, (\d+) new \(\d+ unique total\)\.$"
)
_RE_PAGE_STORED = re.compile(r"^Page (\d+): stored \d+ rows\.$")
_RE_PAGE_CLASSIFIED = re.compile(
    r"^Page (\d+): classified \d+; retained (\d+) open indexes\.$"
)
_RE_PAGE_PROBED = re.compile(
    r"^Page (\d+): probed \d+ \(\d+ clean, \d+ flagged, \d+ unprobed\)\.$"
)
_RE_RUN_COMPLETE = re.compile(
    r"^Run complete: fetched \d+, verified \d+, retained \d+ open indexes\.$"
)
_RE_CANCELLED = re.compile(r"^Cancelled\.$")

_REQUIRED_RUNS_COLS = {
    "run_id", "started_at", "finished_at", "instance_url", "query",
    "max_results", "fetched_count", "deduped_count", "verified_count",
    "status", "error_message",
}
_REQUIRED_RESULTS_COLS = {
    "result_id", "run_id", "url", "url_normalized", "title", "snippet",
    "source_engine", "source_engines_json", "verdict", "reason_code",
    "http_status", "checked_at", "probe_status", "probe_indicator_matches",
    "probe_preview", "probe_checked_at", "probe_error", "probe_snapshot_json",
}

_MISSING = object()


@dataclass
class _Check:
    label: str
    passed: Optional[bool]   # None → SKIP
    detail: str = ""
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live end-to-end SearXNG dork pipeline validation.",
        epilog="Requires --confirm-live. Not for automated test suites.",
    )
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--instance-url", metavar="URL", default=None)
    p.add_argument("--query", metavar="TEXT", default=None)
    p.add_argument("--max-results", metavar="N", type=int, default=None)
    p.add_argument("--timeout", metavar="SECS", type=int, default=None,
                   dest="request_timeout")
    p.add_argument("--short-retry", metavar="SECS", type=int, default=None,
                   dest="short_retry_delay")
    p.add_argument("--long-retry", metavar="SECS", type=int, default=None,
                   dest="long_retry_delay")
    p.add_argument("--probe", action="store_true", default=False)
    p.add_argument("--keep-db", action="store_true", default=False)
    p.add_argument("--cancel-after-classify", metavar="N", type=int, default=None)
    args = p.parse_args(argv)

    if not args.confirm_live:
        p.error(
            "--confirm-live is required. This script performs live network "
            "requests against a real SearXNG instance."
        )

    _chk_range(p, "--max-results", args.max_results, 1, 1000)
    _chk_range(p, "--timeout", args.request_timeout, 5, 60)
    _chk_range(p, "--short-retry", args.short_retry_delay, 5, 60)
    _chk_range(p, "--long-retry", args.long_retry_delay, 60, 300)
    if args.cancel_after_classify is not None and args.cancel_after_classify < 1:
        p.error("--cancel-after-classify must be >= 1.")
    return args


def _chk_range(p: argparse.ArgumentParser, name: str,
               v: Optional[int], lo: int, hi: int) -> None:
    if v is not None and not (lo <= v <= hi):
        p.error(f"{name} must be {lo}–{hi}, got {v}.")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _load_user_prefs() -> dict:
    try:
        prefs = get_config_store(paths=get_paths()).load_user_prefs()
        return prefs if isinstance(prefs, dict) else {}
    except Exception as exc:
        print(f"[warn] Could not load user preferences: {exc}", file=sys.stderr)
        return {}


def _load_pref(data: dict, dotted: str, default: Any) -> Any:
    cur: Any = data
    for k in dotted.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, _MISSING)
        if cur is _MISSING:
            return default
    return default if (cur is None or cur == "") else cur


def _coerce_int(v: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(str(v)))))
    except (TypeError, ValueError, OverflowError):
        return default


def _resolve_run_options(args: argparse.Namespace) -> RunOptions:
    prefs = _load_user_prefs()

    url: Optional[str] = args.instance_url or str(
        _load_pref(prefs, "unified_scan_dialog.searxng_instance_url", "") or ""
    ) or None
    if not url:
        print(
            "ERROR: No SearXNG instance URL found.\n"
            "Provide --instance-url or save one via the Start Scan dialog.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    query = args.query or str(
        _load_pref(prefs, "unified_scan_dialog.searxng_query",
                   'site:* intitle:"index of /"')
    )

    max_results = (
        args.max_results
        if args.max_results is not None
        else _coerce_int(
            _load_pref(prefs, "unified_scan_dialog.searxng_max_results",
                       DEFAULT_MAX_RESULTS),
            default=DEFAULT_MAX_RESULTS, lo=1, hi=1000,
        )
    )
    request_timeout = (
        args.request_timeout
        if args.request_timeout is not None
        else _coerce_int(
            _load_pref(prefs, "unified_scan_dialog.searxng_request_timeout", 15),
            default=15, lo=5, hi=60,
        )
    )
    short_retry = (
        args.short_retry_delay
        if args.short_retry_delay is not None
        else _coerce_int(
            _load_pref(prefs, "unified_scan_dialog.searxng_short_retry_delay", 30),
            default=30, lo=5, hi=60,
        )
    )
    long_retry = (
        args.long_retry_delay
        if args.long_retry_delay is not None
        else _coerce_int(
            _load_pref(prefs, "unified_scan_dialog.searxng_long_retry_delay", 180),
            default=180, lo=60, hi=300,
        )
    )
    return RunOptions(
        instance_url=url,
        query=query,
        max_results=max_results,
        bulk_probe_enabled=args.probe,
        request_timeout=request_timeout,
        short_retry_delay=short_retry,
        long_retry_delay=long_retry,
    )


# ---------------------------------------------------------------------------
# Cancel trigger and progress callback
# ---------------------------------------------------------------------------
def _make_cancel_trigger(
    cancel_event: threading.Event, after_n: int
) -> Callable[[str], None]:
    lock = threading.Lock()
    seen: set = set()

    def _trigger(msg: str) -> None:
        m = _RE_PAGE_CLASSIFIED.match(msg)
        if not m:
            return
        with lock:
            seen.add(int(m.group(1)))
            if len(seen) >= after_n:
                cancel_event.set()

    return _trigger


def _make_progress_cb(
    messages: list,
    lock: threading.Lock,
    cancel_trigger: Optional[Callable[[str], None]] = None,
) -> Callable[[str], None]:
    def _cb(msg: str) -> None:
        with lock:
            messages.append(msg)
        print(msg, flush=True)
        if cancel_trigger is not None:
            cancel_trigger(msg)

    return _cb


# ---------------------------------------------------------------------------
# Stage-event extraction
# ---------------------------------------------------------------------------
def _extract_stage_events(messages: List[str], probe_enabled: bool) -> dict:
    ev: dict = {
        "page_start_idx": {}, "page_received_idx": {}, "page_received_new": {},
        "page_stored_idx": {}, "page_classified_idx": {}, "page_retained_count": {},
        "page_probed_idx": {},
        "run_complete_idx": None, "cancelled_idx": None, "retry_pages": set(),
    }
    for i, msg in enumerate(messages):
        if m := _RE_PAGE_START.match(msg):
            n = int(m.group(1))
            ev["page_start_idx"].setdefault(n, i)
        elif m := _RE_PAGE_RETRY.match(msg):
            ev["retry_pages"].add(int(m.group(1)))
        elif m := _RE_PAGE_RECEIVED.match(msg):
            n = int(m.group(1))
            ev["page_received_idx"][n] = i
            ev["page_received_new"][n] = int(m.group(2))
        elif m := _RE_PAGE_STORED.match(msg):
            ev["page_stored_idx"][int(m.group(1))] = i
        elif m := _RE_PAGE_CLASSIFIED.match(msg):
            n = int(m.group(1))
            ev["page_classified_idx"][n] = i
            ev["page_retained_count"][n] = int(m.group(2))
        elif probe_enabled and (m := _RE_PAGE_PROBED.match(msg)):
            ev["page_probed_idx"][int(m.group(1))] = i
        elif _RE_RUN_COMPLETE.match(msg):
            ev["run_complete_idx"] = i
        elif _RE_CANCELLED.match(msg):
            ev["cancelled_idx"] = i
    return ev


# ---------------------------------------------------------------------------
# Stage-order checking
# ---------------------------------------------------------------------------
def _check_stage_order(ev: dict, probe_enabled: bool) -> List[str]:
    failures: List[str] = []
    s_idx = ev["page_start_idx"]
    r_idx = ev["page_received_idx"]
    r_new = ev["page_received_new"]
    st_idx = ev["page_stored_idx"]
    cl_idx = ev["page_classified_idx"]
    retained = ev["page_retained_count"]
    pr_idx = ev["page_probed_idx"]
    retried = ev["retry_pages"]

    all_pages = sorted(set(s_idx) | set(r_idx) | set(st_idx) | set(cl_idx))

    def _terminal(n: int) -> Optional[int]:
        if probe_enabled and n in pr_idx:
            return pr_idx[n]
        if n in cl_idx:
            return cl_idx[n]
        if n in r_idx and r_new.get(n, 1) == 0:
            return r_idx[n]
        return None

    for n in all_pages:
        note = f" (had retries)" if n in retried else ""
        dup = r_new.get(n, 1) == 0

        if dup:
            si, ri = s_idx.get(n), r_idx.get(n)
            # Duplicate-only pages must have both start and received events.
            if si is None:
                failures.append(f"Page {n}{note}: missing start event")
            if ri is None:
                failures.append(f"Page {n}{note}: missing received event")
            if si is not None and ri is not None and si >= ri:
                failures.append(
                    f"Page {n}{note}: start({si}) >= received({ri})"
                )
        else:
            si = s_idx.get(n)
            ri = r_idx.get(n)
            sti = st_idx.get(n)
            cli = cl_idx.get(n)
            # Positive-new pages must have start, received, stored, and classified.
            if si is None:
                failures.append(f"Page {n}{note}: missing start event")
            if ri is None:
                failures.append(f"Page {n}{note}: missing received event")
            if sti is None:
                failures.append(f"Page {n}{note}: missing stored event")
            else:
                if si is not None and ri is not None and si >= ri:
                    failures.append(
                        f"Page {n}{note}: start({si}) >= received({ri})"
                    )
                if ri is not None and ri >= sti:
                    failures.append(
                        f"Page {n}{note}: received({ri}) >= stored({sti})"
                    )
            if cli is None:
                failures.append(f"Page {n}{note}: missing classified event")
            elif sti is not None and sti >= cli:
                failures.append(
                    f"Page {n}{note}: stored({sti}) >= classified({cli})"
                )
            # Probe is mandatory when probe_enabled and this page retained rows.
            if probe_enabled and cli is not None:
                page_retained = retained.get(n, 0)
                if page_retained > 0:
                    pri = pr_idx.get(n)
                    if pri is None:
                        failures.append(
                            f"Page {n}{note}: retained {page_retained} row(s) "
                            f"but missing probed event"
                        )
                    elif cli >= pri:
                        failures.append(
                            f"Page {n}{note}: classified({cli}) >= probed({pri})"
                        )

    # Cross-page ordering
    sorted_starts = sorted(s_idx.items())
    for k in range(len(sorted_starts) - 1):
        n, _ = sorted_starts[k]
        n1, s1 = sorted_starts[k + 1]
        t = _terminal(n)
        if t is not None and t >= s1:
            failures.append(
                f"Cross-page: terminal of page {n}({t}) >= start of page {n1}({s1})"
            )

    return failures


def _terminal_event_indices(ev: dict, probe_enabled: bool) -> List[int]:
    """Return the terminal progress-event index for every completed page."""
    indices: List[int] = []
    for page in ev["page_start_idx"]:
        if probe_enabled and page in ev["page_probed_idx"]:
            indices.append(ev["page_probed_idx"][page])
        elif page in ev["page_classified_idx"]:
            indices.append(ev["page_classified_idx"][page])
        elif ev["page_received_new"].get(page) == 0:
            received = ev["page_received_idx"].get(page)
            if received is not None:
                indices.append(received)
    return indices


# ---------------------------------------------------------------------------
# DB integrity and RunResult consistency
# ---------------------------------------------------------------------------
def _check_db(db_path: Path, result: RunResult, probe_enabled: bool) -> List[_Check]:
    checks: List[_Check] = []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        checks.append(_Check("DB open", False, str(exc)))
        return checks

    try:
        # integrity_check
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        ok = len(rows) == 1 and rows[0][0] == "ok"
        checks.append(_Check("SQLite integrity_check", ok,
                              "ok" if ok else str([r[0] for r in rows])))

        # foreign_key_check
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        checks.append(_Check("SQLite foreign_key_check", not fk_rows,
                              f"{len(fk_rows)} violation(s)" if fk_rows else "0 violations"))

        # schema columns
        for table, required in [("dork_runs", _REQUIRED_RUNS_COLS),
                                 ("dork_results", _REQUIRED_RESULTS_COLS)]:
            actual = {r["name"] for r in
                      conn.execute(f"PRAGMA table_info({table})").fetchall()}
            missing = required - actual
            checks.append(_Check(f"DB schema ({table})", not missing,
                                  "ok" if not missing else f"missing {sorted(missing)}"))

        # FK declaration: must reference run_id column in dork_runs
        fk_list = conn.execute(
            "PRAGMA foreign_key_list('dork_results')"
        ).fetchall()
        has_fk = any(
            r["table"] == "dork_runs"
            and r["from"] == "run_id"
            and r["to"] == "run_id"
            for r in fk_list
        )
        checks.append(_Check("DB FK declaration", has_fk,
                              "run_id→dork_runs(run_id) declared" if has_fk
                              else "run_id FK to dork_runs(run_id) missing"))

        # RunResult consistency
        if result.run_id is None:
            checks.append(_Check("DB RunResult consistency", None,
                                  skip_reason="no run_id (failed before DB row)"))
            return checks

        # Guard: skip consistency queries if required tables are missing.
        schema_ok = not any(
            c.passed is False and "schema" in c.label for c in checks
        )
        if not schema_ok:
            checks.append(_Check("DB RunResult consistency", None,
                                  skip_reason="schema errors prevent consistency queries"))
            return checks

        count = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id=?",
            (result.run_id,)
        ).fetchone()[0]
        checks.append(_Check(
            "DB row count == deduped_count",
            count == result.deduped_count,
            f"{count} == {result.deduped_count}",
        ))

        run_row = conn.execute(
            "SELECT status, fetched_count, deduped_count, verified_count, "
            "error_message, finished_at FROM dork_runs WHERE run_id=?",
            (result.run_id,)
        ).fetchone()

        if run_row is None:
            checks.append(_Check("DB run row", False,
                                  f"no row for run_id={result.run_id}"))
            return checks

        fields_ok = (
            run_row["status"] == result.status
            and run_row["fetched_count"] == result.fetched_count
            and run_row["deduped_count"] == result.deduped_count
            and run_row["verified_count"] == result.verified_count
        )
        checks.append(_Check("DB status/count fields match RunResult", fields_ok,
                              "matched" if fields_ok else (
                                  f"status {run_row['status']!r}≠{result.status!r}, "
                                  f"fetched {run_row['fetched_count']}≠{result.fetched_count}, "
                                  f"deduped {run_row['deduped_count']}≠{result.deduped_count}, "
                                  f"verified {run_row['verified_count']}≠{result.verified_count}"
                              )))

        err_ok = (result.status != "error") == (run_row["error_message"] is None)
        checks.append(_Check("DB error_message correct", err_ok,
                              "NULL" if run_row["error_message"] is None
                              else run_row["error_message"]))

        checks.append(_Check("DB finished_at set", run_row["finished_at"] is not None,
                              str(run_row["finished_at"])))

        # Probe parity
        if probe_enabled:
            pr = conn.execute(
                """
                SELECT
                    COUNT(*) AS tot,
                    SUM(CASE WHEN probe_status='clean'    THEN 1 ELSE 0 END) AS clean,
                    SUM(CASE WHEN probe_status='issue'    THEN 1 ELSE 0 END) AS issue,
                    SUM(CASE WHEN probe_status='unprobed' THEN 1 ELSE 0 END) AS unprobed
                FROM dork_results
                WHERE run_id=? AND probe_checked_at IS NOT NULL
                """,
                (result.run_id,)
            ).fetchone()
            if pr:
                probe_ok = (
                    (pr["tot"] or 0) == result.probe_total
                    and (pr["clean"] or 0) == result.probe_clean
                    and (pr["issue"] or 0) == result.probe_issue
                    and (pr["unprobed"] or 0) == result.probe_unprobed
                )
                checks.append(_Check(
                    "Probe count consistency (probe_checked_at IS NOT NULL)",
                    probe_ok,
                    (f"tot={pr['tot']} clean={pr['clean']} "
                     f"issue={pr['issue']} unprobed={pr['unprobed']}")
                    if not probe_ok else "matched",
                ))

    finally:
        conn.close()

    return checks


# ---------------------------------------------------------------------------
# Full check suite
# ---------------------------------------------------------------------------
def _run_all_checks(
    result: RunResult,
    messages: List[str],
    db_path: Path,
    args: argparse.Namespace,
) -> List[_Check]:
    checks: List[_Check] = []
    probe_enabled: bool = args.probe

    # Run status
    deterministic_cancel = args.cancel_after_classify is not None
    expected = RUN_STATUS_CANCELLED if deterministic_cancel else RUN_STATUS_DONE
    checks.append(_Check(
        f"Run status (expected: {expected!r})",
        result.status == expected,
        result.status,
    ))

    # Mandatory invariants for successful completion or deterministic cancellation.
    requires_durable_run = (
        result.status == RUN_STATUS_DONE
        or (deterministic_cancel and result.status == RUN_STATUS_CANCELLED)
    )
    if requires_durable_run:
        checks.append(_Check(
            "Run result has no error",
            result.error is None,
            "none" if result.error is None else str(result.error),
        ))
        checks.append(_Check("Pages fetched ≥ 1", result.pages_fetched >= 1,
                              str(result.pages_fetched)))
        checks.append(_Check(
            "Run ID present",
            result.run_id is not None,
            "present" if result.run_id is not None else "run_id is None — run completed without a DB row",
        ))
        checks.append(_Check(
            "DB file present",
            db_path.exists(),
            str(db_path) if db_path.exists() else f"missing at {db_path}",
        ))

    # Stage ordering and page-event presence
    ev = _extract_stage_events(messages, probe_enabled)
    order_fails = _check_stage_order(ev, probe_enabled)
    n_pages = len(ev["page_start_idx"])
    retry_note = (f" ({len(ev['retry_pages'])} retried)"
                  if ev["retry_pages"] else "")
    checks.append(_Check(
        "Stage ordering",
        not order_fails,
        f"{n_pages} page(s) checked{retry_note}"
        if not order_fails else "; ".join(order_fails),
    ))

    # Page traces must account for every successful SearXNG response.
    if requires_durable_run:
        received_pages = len(ev["page_received_idx"])
        checks.append(_Check(
            "Page events present",
            bool(ev["page_start_idx"]),
            f"{n_pages} start event(s)" if ev["page_start_idx"]
            else "no page start events in progress messages",
        ))
        checks.append(_Check(
            "Fetched-page trace count",
            received_pages == result.pages_fetched,
            f"trace={received_pages}, result={result.pages_fetched}",
        ))

    terminal_indices = _terminal_event_indices(ev, probe_enabled)
    if result.status == RUN_STATUS_DONE:
        complete_idx = ev["run_complete_idx"]
        checks.append(_Check(
            "Run complete message present",
            complete_idx is not None,
            "present" if complete_idx is not None
            else "no 'Run complete' message — run may not have finished normally",
        ))
        checks.append(_Check(
            "Run complete follows page processing",
            complete_idx is not None
            and bool(terminal_indices)
            and complete_idx > max(terminal_indices),
            (
                f"complete={complete_idx}, final_page={max(terminal_indices)}"
                if complete_idx is not None and terminal_indices
                else "missing run-complete or terminal page event"
            ),
        ))

    if deterministic_cancel and result.status == RUN_STATUS_CANCELLED:
        classified_indices = list(ev["page_classified_idx"].values())
        required_pages = int(args.cancel_after_classify)
        cancel_idx = ev["cancelled_idx"]
        checks.append(_Check(
            "Cancellation classification boundary reached",
            len(classified_indices) >= required_pages,
            f"classified={len(classified_indices)}, required={required_pages}",
        ))
        checks.append(_Check(
            "Cancellation message present",
            cancel_idx is not None,
            "present" if cancel_idx is not None else "no 'Cancelled.' message",
        ))
        checks.append(_Check(
            "Cancellation follows classified boundary",
            cancel_idx is not None
            and bool(classified_indices)
            and cancel_idx > max(classified_indices),
            (
                f"cancelled={cancel_idx}, final_classified={max(classified_indices)}"
                if cancel_idx is not None and classified_indices
                else "missing cancellation or classified event"
            ),
        ))

    # Unique URLs
    if result.fetched_count > 0:
        checks.append(_Check("Unique URLs fetched", True, str(result.fetched_count)))
    else:
        checks.append(_Check("Unique URLs fetched", None,
                              skip_reason="0 fetched — upstream engine availability not guaranteed"))

    # DB checks
    if db_path.exists():
        checks.extend(_check_db(db_path, result, probe_enabled))
    elif result.run_id is not None:
        checks.append(_Check("DB file present", False,
                              "temp DB missing but run_id is set"))
    else:
        checks.append(_Check("DB checks", None, skip_reason="no run_id"))

    return checks


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def _cleanup(tmp_dir: str, keep_db: bool, db_path: Path) -> str:
    if keep_db:
        note = f"[INFO] Temp directory retained (--keep-db): {db_path}"
        print(note)
        return note
    try:
        shutil.rmtree(tmp_dir)
    except Exception as exc:
        note = f"[WARN] Cleanup raised {exc} — directory retained: {tmp_dir}"
        print(note, file=sys.stderr)
        return note
    if Path(tmp_dir).exists():
        note = f"[WARN] rmtree completed but directory still exists: {tmp_dir}"
        print(note, file=sys.stderr)
        return note
    note = f"[PASS] Temp directory deleted and verified gone: {tmp_dir}"
    print(note)
    return note


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _print_report(
    result: RunResult,
    opts: RunOptions,
    checks: List[_Check],
    ev: dict,
    db_path: Path,
    cleanup_note: str,
    probe_enabled: bool,
) -> None:
    print()
    print("=== SearXNG Live Validation ===")
    print(f"Instance:     {opts.instance_url}")
    print(f"Query:        {opts.query}")
    print(f"Max results:  {opts.max_results}  "
          f"Timeout: {opts.request_timeout}s  "
          f"Short retry: {opts.short_retry_delay}s  "
          f"Long retry: {opts.long_retry_delay}s")
    print(f"Temp DB:      {db_path}")
    print()
    print("--- Run telemetry ---")
    print(f"Pages fetched:        {result.pages_fetched}")
    print(f"URLs fetched:        {result.fetched_count}   [fetched_count]")
    print(f"Classified:          {result.verified_count}   [verified_count]")
    print(f"Retained (open idx): {result.deduped_count}   [deduped_count]")
    if probe_enabled:
        print(f"Probe:               {result.probe_total} "
              f"({result.probe_clean} clean, {result.probe_issue} flagged, "
              f"{result.probe_unprobed} unprobed)")
    else:
        print("Probe:               disabled")
    print(f"Status:              {result.status}")
    if result.error:
        print(f"Error:               {result.error}")
    print(f"Hard retries:        {result.hard_retry_count}")
    print(f"Pacing delay:        {result.pacing_delay_seconds:.1f}s")
    print(f"Hard retry delay:    {result.hard_retry_delay_seconds:.1f}s")
    if ev.get("retry_pages"):
        print(f"Retried pages:       {sorted(ev['retry_pages'])}")
    print()
    print("--- Assertions ---")
    for chk in checks:
        if chk.passed is None:
            print(f"[SKIP] {chk.label}: {chk.skip_reason}")
        elif chk.passed:
            suffix = f": {chk.detail}" if chk.detail else ""
            print(f"[PASS] {chk.label}{suffix}")
        else:
            suffix = f": {chk.detail}" if chk.detail else ""
            print(f"[FAIL] {chk.label}{suffix}")
    print()
    print("--- Cleanup ---")
    print(cleanup_note)
    print()
    fail_count = sum(1 for c in checks if c.passed is False)
    if fail_count == 0 and "[WARN]" not in cleanup_note:
        print("=== RESULT: PASS ===")
    else:
        total = fail_count + (1 if "[WARN]" in cleanup_note else 0)
        print(f"=== RESULT: FAIL ({total} failure(s)) ===")


# ---------------------------------------------------------------------------
# Worker wait
# ---------------------------------------------------------------------------
def _wait_for_worker(thread: threading.Thread) -> None:
    iteration = 0
    while thread.is_alive():
        thread.join(timeout=30)
        if thread.is_alive():
            iteration += 1
            print(f"[info] Waiting for worker… ({iteration * 30}s elapsed)",
                  flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    opts = _resolve_run_options(args)

    tmp_dir = tempfile.mkdtemp(prefix="searxng_live_test_")
    db_path = Path(tmp_dir) / "se_dork_live.db"

    cancel_event = threading.Event()
    messages: list = []
    msg_lock = threading.Lock()

    cancel_trigger = (
        _make_cancel_trigger(cancel_event, args.cancel_after_classify)
        if args.cancel_after_classify is not None
        else None
    )
    progress_cb = _make_progress_cb(messages, msg_lock, cancel_trigger)
    result_holder: list = [None]

    def _target() -> None:
        result_holder[0] = run_dork_search(
            opts, db_path=db_path, progress_cb=progress_cb,
            cancel_event=cancel_event,
        )

    thread = threading.Thread(target=_target, name="searxng-live")

    _state = {"interrupted": False, "sigint_count": 0}

    def _handle_sigint(signum: int, frame: object) -> None:
        _state["sigint_count"] += 1
        _state["interrupted"] = True
        cancel_event.set()
        if _state["sigint_count"] >= 2:
            signal.default_int_handler(signum, frame)

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        try:
            thread.start()
        except Exception as exc:
            print(f"ERROR: Worker thread could not be started: {exc}", file=sys.stderr)
            _cleanup(tmp_dir, args.keep_db, db_path)
            return 2
        try:
            while thread.is_alive():
                thread.join(timeout=0.25)
        except KeyboardInterrupt:
            cancel_event.set()
            _state["interrupted"] = True
            _wait_for_worker(thread)
    finally:
        signal.signal(signal.SIGINT, old_handler)

    result: Optional[RunResult] = result_holder[0]

    if _state["interrupted"]:
        print("[info] Run was cancelled by SIGINT — exiting 3.")
        _cleanup(tmp_dir, args.keep_db, db_path)
        return 3

    if result is None:
        print("ERROR: Worker returned no result.", file=sys.stderr)
        _cleanup(tmp_dir, args.keep_db, db_path)
        return 1

    checks = _run_all_checks(result, messages, db_path, args)
    ev = _extract_stage_events(messages, args.probe)
    cleanup_note = _cleanup(tmp_dir, args.keep_db, db_path)
    _print_report(result, opts, checks, ev, db_path, cleanup_note, args.probe)

    fail_count = sum(1 for c in checks if c.passed is False)
    if fail_count > 0:
        return 1
    if "[WARN]" in cleanup_note:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
