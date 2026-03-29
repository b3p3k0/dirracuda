"""
HTTP scan stage implementations (Card 4).

Replaces the skeleton stubs with real Shodan querying, TCP port-reachability
checks, and dual-scheme HTTP(S) verification with directory-index validation,
entry counting, and one-level subdirectory recursion.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, TYPE_CHECKING

from commands.http.models import (
    HttpCandidate,
    HttpDiscoveryOutcome,
    HttpAccessOutcome,
)
from commands.http.shodan_query import query_http_shodan
from commands.http.verifier import (
    port_check,
    try_http_request,
    validate_index_page,
    count_dir_entries,
    fetch_subdir_entries,
)
from shared.database import HttpPersistence

if TYPE_CHECKING:
    from shared.http_workflow import HttpWorkflow

# Bounds worst-case per-host time to ~160s at 8s subdir timeout.
MAX_SUBDIRS = 20


def _should_report_progress(completed: int, total: int, batch_size: int = 10) -> bool:
    """Match SMB concurrent-auth progress cadence: first, every N, and final."""
    return completed == 1 or completed == total or completed % batch_size == 0


def _report_concurrent_progress(
    workflow: "HttpWorkflow",
    completed: int,
    total: int,
    success_count: int,
    failed_count: int,
    active_threads: int,
) -> None:
    """Emit SMB-style aggregated concurrent progress line."""
    progress_pct = (completed / total) * 100
    success_rate = (success_count / max(1, completed)) * 100
    workflow.output.info(
        f"📊 Progress: {completed}/{total} ({progress_pct:.1f}%) | "
        f"Success: {success_count}, Failed: {failed_count} ({success_rate:.0f}%) | "
        f"Active: {active_threads} threads"
    )


def run_discover_stage(workflow: "HttpWorkflow") -> Tuple[List[HttpCandidate], int]:
    """
    Stage 1: Shodan query + TCP port reachability check.

    Returns (reachable_candidates, shodan_total) where:
      reachable_candidates — hosts that passed the port check (passed to stage 2)
      shodan_total         — total Shodan candidates that entered the port-check loop
                             (used for "Hosts Scanned" rollup metric)

    Port-failed hosts are persisted inside this function and excluded from the
    returned list. Raises HttpDiscoveryError (from query_http_shodan) on API failure.
    """
    out = workflow.output
    args = getattr(workflow, "args", None)
    country = getattr(args, "country", None) if args else None
    custom_filters = getattr(args, "filter", "") if args else ""

    http_cfg = workflow.config.get_http_config()
    verif = http_cfg.get("verification", {})
    connect_timeout = verif.get("connect_timeout", 5)

    # May raise HttpDiscoveryError — propagates to HttpWorkflow.run() which re-raises
    # to httpseek main() for clean exit(1) handling.
    candidates = query_http_shodan(workflow, country, custom_filters)

    shodan_total = len(candidates)
    if shodan_total == 0:
        return [], 0

    out.info(f"Checking port reachability for {shodan_total} hosts...")

    reachable: List[HttpCandidate] = []
    port_failed_outcomes: List[HttpDiscoveryOutcome] = []

    max_workers = min(workflow.config.get_max_concurrent_http_discovery_hosts(), shodan_total)

    def _check_host(candidate: HttpCandidate) -> Tuple[bool, str]:
        ok, reason = port_check(candidate.ip, candidate.port, timeout=float(connect_timeout))
        return ok, reason

    results_by_index = [None] * shodan_total
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_check_host, c): i
            for i, c in enumerate(candidates)
        }
        completed = 0
        progress_success_count = 0
        progress_failed_count = 0
        for future in as_completed(future_to_index):
            completed += 1
            idx = future_to_index[future]
            try:
                ok, reason = future.result()
                results_by_index[idx] = (ok, reason, None)
            except Exception as exc:
                results_by_index[idx] = (False, "connect_fail", str(exc))
            ok_v, reason_v, exc_v = results_by_index[idx]
            if ok_v:
                progress_success_count += 1
            else:
                progress_failed_count += 1
            if _should_report_progress(completed, shodan_total):
                active_threads = sum(1 for f in future_to_index if not f.done())
                _report_concurrent_progress(
                    workflow,
                    completed,
                    shodan_total,
                    progress_success_count,
                    progress_failed_count,
                    active_threads,
                )

    for i, candidate in enumerate(candidates):
        ok, reason, exc_detail = results_by_index[i]
        if ok:
            reachable.append(candidate)
        else:
            port_failed_outcomes.append(HttpDiscoveryOutcome(
                ip=candidate.ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                scheme=candidate.scheme,
                banner=candidate.banner,
                title=candidate.title,
                shodan_data=json.dumps(candidate.shodan_data),
                reason=reason,
                error_message=exc_detail or f"Port {candidate.port} unreachable: {reason}",
            ))

    unreachable_count = len(port_failed_outcomes)
    summary_msg = (
        f"Port check complete: {len(reachable)} reachable, "
        f"{unreachable_count} unreachable ({shodan_total} total)"
    )
    if unreachable_count == 0:
        out.success(summary_msg)
    elif len(reachable) == 0:
        out.error(summary_msg)
    else:
        out.warning(summary_msg)

    if port_failed_outcomes:
        HttpPersistence(workflow.db_path).persist_discovery_outcomes_batch(port_failed_outcomes)

    return reachable, shodan_total


def run_access_stage(workflow: "HttpWorkflow", candidates: List[HttpCandidate]) -> int:
    """
    Stage 2: HTTP(S) access verification for each reachable host.

    Returns the count of hosts where a genuine directory index was found.
    All outcomes (success and failure) are persisted in a single batch commit.
    """
    out = workflow.output

    if not candidates:
        workflow.last_accessible_directory_count = 0
        return 0

    # All three flags come exclusively from config — never from workflow.args.
    # The GUI writes them via scan_manager.py into config_overrides before subprocess
    # invocation, so they arrive as authoritative config values.
    http_cfg = workflow.config.get_http_config()
    verif = http_cfg.get("verification", {})
    request_timeout = float(verif.get("request_timeout", 10))
    subdir_timeout = float(verif.get("subdir_timeout", 8))
    allow_insecure_tls = verif.get("allow_insecure_tls", True)
    verify_http = verif.get("verify_http", True)
    verify_https = verif.get("verify_https", True)

    total = len(candidates)
    out.info(f"Testing HTTP(S) access for {total} reachable hosts...")

    outcomes: List[HttpAccessOutcome] = []

    max_workers = min(workflow.config.get_max_concurrent_http_access_hosts(), total)

    def _check_access(candidate: HttpCandidate) -> HttpAccessOutcome:
        ip = candidate.ip
        shodan_data_str = json.dumps(candidate.shodan_data)

        # Build attempt list on the Shodan-reported endpoint only.
        # We never fall back to canonical 80/443 unless Shodan itself returned
        # those ports. This keeps verification, browse, and probe behavior locked
        # to the exact search hit endpoint.
        attempts_to_try: List[Tuple[str, int]] = []
        if verify_http:
            attempts_to_try.append(("http", candidate.port))
        if verify_https:
            attempts_to_try.append(("https", candidate.port))

        if not attempts_to_try:
            return HttpAccessOutcome(
                ip=ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                scheme=candidate.scheme,
                banner=candidate.banner,
                title=candidate.title,
                shodan_data=shodan_data_str,
                accessible=False,
                status_code=0,
                is_index_page=False,
                dir_count=0,
                file_count=0,
                tls_verified=False,
                reason="connect_fail",
                error_message="No protocols enabled for verification",
                access_details=json.dumps({
                    "reason": "connect_fail",
                    "status_code": 0,
                    "tls_verified": False,
                    "dir_count": 0,
                    "file_count": 0,
                    "attempts": [],
                    "subdirs": [],
                }),
            )

        # Run all (scheme, port) combinations independently; collect all results.
        attempt_records = []
        for scheme, port in attempts_to_try:
            status_code, body, tls_verified, reason = try_http_request(
                ip, port, scheme, allow_insecure_tls, request_timeout
            )
            is_index = validate_index_page(body, status_code) if body else False
            dir_count_a = 0
            file_count_a = 0
            dir_paths_a: List[str] = []
            parse_ok = True

            if is_index:
                try:
                    dir_count_a, file_count_a, dir_paths_a = count_dir_entries(body)
                except ValueError:
                    dir_count_a, file_count_a, dir_paths_a = 0, 0, []
                    parse_ok = False
                    reason = "parse_error"

            attempt_records.append({
                "scheme": scheme,
                "port": port,
                "status_code": status_code,
                "is_index": is_index,
                "tls_verified": tls_verified,
                "reason": reason,
                "dir_count": dir_count_a,
                "file_count": file_count_a,
                "dir_paths": dir_paths_a,
                "parse_ok": parse_ok,
            })

        # Winner selection (5-tier):

        # Tier a: genuine index page with successful parse → highest count → prefer HTTPS
        index_ok = [a for a in attempt_records if a["is_index"] and a["parse_ok"]]
        if index_ok:
            winner = max(
                index_ok,
                key=lambda a: (a["dir_count"] + a["file_count"], 1 if a["scheme"] == "https" else 0),
            )
        else:
            winner = None

        # Tier b: parse_error (is_index=True but count_dir_entries raised)
        if winner is None:
            parse_errors = [a for a in attempt_records if a["is_index"] and not a["parse_ok"]]
            if parse_errors:
                winner = parse_errors[0]

        # Tier c: no index pages but at least one 200 response
        if winner is None:
            ok_200 = [a for a in attempt_records if a["status_code"] == 200]
            if ok_200:
                winner = dict(ok_200[0])
                winner["reason"] = "not_index_page"

        # Tier d: server responded with non-zero, non-200 status
        if winner is None:
            non_zero = [a for a in attempt_records if a["status_code"] != 0]
            if non_zero:
                winner = dict(max(non_zero, key=lambda a: a["status_code"]))
                winner["reason"] = "non_200"

        # Tier e: all attempts failed at network level
        if winner is None:
            winner = attempt_records[0]

        # One-level recursion — only for tier-a winners (is_index AND parse_ok).
        subdirs_list = []
        total_dirs = winner["dir_count"]
        total_files = winner["file_count"]

        if winner["is_index"] and winner["parse_ok"]:
            for path in winner["dir_paths"][:MAX_SUBDIRS]:
                sub_d, sub_f = fetch_subdir_entries(
                    ip, winner["port"], winner["scheme"], path,
                    allow_insecure_tls, subdir_timeout,
                )
                total_dirs += sub_d
                total_files += sub_f
                subdirs_list.append({"path": path, "dir_count": sub_d, "file_count": sub_f})

        accessible = winner["is_index"] and winner["parse_ok"]
        final_reason = "" if accessible else winner["reason"]

        return HttpAccessOutcome(
            ip=ip,
            country=candidate.country,
            country_code=candidate.country_code,
            port=winner["port"],
            scheme=winner["scheme"],
            banner=candidate.banner,
            title=candidate.title,
            shodan_data=shodan_data_str,
            accessible=accessible,
            status_code=winner["status_code"],
            is_index_page=winner["is_index"],
            dir_count=total_dirs,
            file_count=total_files,
            tls_verified=winner["tls_verified"],
            reason=final_reason,
            error_message="" if accessible else f"HTTP verification failed: {final_reason}",
            access_details=json.dumps({
                "reason": final_reason,
                "status_code": winner["status_code"],
                "tls_verified": winner["tls_verified"],
                "dir_count": total_dirs,
                "file_count": total_files,
                "attempts": [
                    {
                        "scheme": a["scheme"],
                        "port": a["port"],
                        "status_code": a["status_code"],
                        "is_index": a["is_index"],
                        "tls_verified": a["tls_verified"],
                        "reason": a["reason"],
                        "parse_ok": a["parse_ok"],
                    }
                    for a in attempt_records
                ],
                "subdirs": subdirs_list,
            }),
        )

    results_by_index: List = [None] * total
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_check_access, c): i
            for i, c in enumerate(candidates)
        }
        completed = 0
        progress_success_count = 0
        progress_failed_count = 0
        for future in as_completed(future_to_index):
            completed += 1
            idx = future_to_index[future]
            try:
                results_by_index[idx] = future.result()
            except Exception as exc:
                err_str = str(exc)
                candidate = candidates[idx]
                results_by_index[idx] = HttpAccessOutcome(
                    ip=candidate.ip,
                    country=candidate.country,
                    country_code=candidate.country_code,
                    port=candidate.port,
                    scheme=candidate.scheme,
                    banner=candidate.banner,
                    title=candidate.title,
                    shodan_data=json.dumps(candidate.shodan_data),
                    accessible=False,
                    status_code=0,
                    is_index_page=False,
                    dir_count=0,
                    file_count=0,
                    tls_verified=False,
                    reason="connect_fail",
                    error_message=f"Unexpected error: {err_str}",
                    access_details=json.dumps({"reason": "connect_fail", "error": err_str}),
                )
            outcome = results_by_index[idx]
            if outcome.accessible:
                progress_success_count += 1
            else:
                progress_failed_count += 1
            if _should_report_progress(completed, total):
                active_threads = sum(1 for f in future_to_index if not f.done())
                _report_concurrent_progress(
                    workflow,
                    completed,
                    total,
                    progress_success_count,
                    progress_failed_count,
                    active_threads,
                )

    for outcome in results_by_index:
        outcomes.append(outcome)

    accessible_count = sum(1 for o in outcomes if o.accessible)
    # Used by workflow summary rollup.
    workflow.last_accessible_directory_count = sum(o.dir_count for o in outcomes if o.accessible)

    summary_msg = f"Access verification complete: {accessible_count} accessible of {total} tested"
    if accessible_count > 0:
        out.success(summary_msg)
    else:
        out.warning(summary_msg)

    if outcomes:
        HttpPersistence(workflow.db_path).persist_access_outcomes_batch(outcomes)

    return accessible_count
