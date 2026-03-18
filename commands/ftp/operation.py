"""
FTP scan stage implementations (Card 4).

Replaces the skeleton sleep-loop stubs with real Shodan querying,
TCP port checks, and anonymous FTP authentication / root listing.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, TYPE_CHECKING

from commands.ftp.models import (
    FtpCandidate,
    FtpDiscoveryOutcome,
    FtpAccessOutcome,
)
from commands.ftp.shodan_query import query_ftp_shodan
from commands.ftp.verifier import port_check, try_anon_login, try_root_listing
from shared.database import FtpPersistence

if TYPE_CHECKING:
    from shared.ftp_workflow import FtpWorkflow


def _should_report_progress(completed: int, total: int, batch_size: int = 10) -> bool:
    """Match SMB concurrent-auth progress cadence: first, every N, and final."""
    return completed == 1 or completed == total or completed % batch_size == 0


def _report_concurrent_progress(
    workflow: "FtpWorkflow",
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


def run_discover_stage(workflow: "FtpWorkflow") -> Tuple[List[FtpCandidate], int]:
    """
    Stage 1: Shodan query + TCP port reachability check.

    Returns (reachable_candidates, shodan_total) where:
      reachable_candidates — hosts that passed the port check (passed to stage 2)
      shodan_total         — total Shodan candidates that entered the port-check loop
                             (used for "Hosts Scanned" rollup metric)

    Port-failed hosts are persisted inside this function and excluded from the
    returned list. Raises FtpDiscoveryError (from query_ftp_shodan) on API failure.
    """
    out = workflow.output
    args = getattr(workflow, "args", None)
    country = getattr(args, "country", None) if args else None

    ftp_cfg = workflow.config.get_ftp_config()
    verif = ftp_cfg.get("verification", {})
    connect_timeout = verif.get("connect_timeout", 5)

    # May raise FtpDiscoveryError — propagates to FtpWorkflow.run() which re-raises
    # to ftpseek main() for clean exit(1) handling.
    candidates = query_ftp_shodan(workflow, country)

    shodan_total = len(candidates)
    if shodan_total == 0:
        return [], 0

    out.info(f"Checking port reachability for {shodan_total} hosts...")

    reachable: List[FtpCandidate] = []
    port_failed_outcomes: List[FtpDiscoveryOutcome] = []

    max_workers = min(workflow.config.get_max_concurrent_ftp_discovery_hosts(), shodan_total)

    def _check_host(candidate: FtpCandidate) -> Tuple[bool, str]:
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
            port_failed_outcomes.append(FtpDiscoveryOutcome(
                ip=candidate.ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                banner=candidate.banner,
                shodan_data=json.dumps(candidate.shodan_data),
                reason=reason,
                error_message=exc_detail or f"Port {candidate.port} unreachable: {reason}",
            ))

    unreachable_count = len(port_failed_outcomes)
    out.info(
        f"Port check complete: {len(reachable)} reachable, "
        f"{unreachable_count} unreachable ({shodan_total} total)"
    )

    if port_failed_outcomes:
        FtpPersistence(workflow.db_path).persist_discovery_outcomes_batch(port_failed_outcomes)

    return reachable, shodan_total


def run_access_stage(workflow: "FtpWorkflow", candidates: List[FtpCandidate]) -> int:
    """
    Stage 2: Anonymous FTP login + root listing for each reachable host.

    Returns the count of hosts where anonymous access succeeded.
    All outcomes (success and failure) are persisted in a single batch commit.
    """
    out = workflow.output

    if not candidates:
        return 0

    ftp_cfg = workflow.config.get_ftp_config()
    verif = ftp_cfg.get("verification", {})
    auth_timeout = verif.get("auth_timeout", 10)
    listing_timeout = verif.get("listing_timeout", 15)

    total = len(candidates)
    out.info(f"Testing anonymous FTP access for {total} reachable hosts...")

    outcomes: List[FtpAccessOutcome] = []

    max_workers = min(workflow.config.get_max_concurrent_ftp_access_hosts(), total)

    def _check_access(candidate: FtpCandidate) -> FtpAccessOutcome:
        login_ok, connect_banner, login_reason = try_anon_login(
            candidate.ip, candidate.port, timeout=float(auth_timeout)
        )
        if not login_ok:
            return FtpAccessOutcome(
                ip=candidate.ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                banner=connect_banner or candidate.banner,
                shodan_data=json.dumps(candidate.shodan_data),
                accessible=False,
                auth_status=login_reason,
                root_listing_available=False,
                root_entry_count=0,
                error_message=f"Anonymous login failed: {login_reason}",
                access_details=json.dumps({
                    "reason": login_reason,
                    "banner": connect_banner or candidate.banner,
                }),
            )
        # Login succeeded — attempt root listing.
        list_ok, entry_count, list_reason = try_root_listing(
            candidate.ip, candidate.port, timeout=float(listing_timeout)
        )
        if list_ok:
            return FtpAccessOutcome(
                ip=candidate.ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                banner=connect_banner,
                shodan_data=json.dumps(candidate.shodan_data),
                accessible=True,
                auth_status="anonymous",
                root_listing_available=True,
                root_entry_count=entry_count,
                error_message="",
                access_details=json.dumps({
                    "reason": "anonymous",
                    "banner": connect_banner,
                }),
            )
        return FtpAccessOutcome(
            ip=candidate.ip,
            country=candidate.country,
            country_code=candidate.country_code,
            port=candidate.port,
            banner=connect_banner,
            shodan_data=json.dumps(candidate.shodan_data),
            accessible=False,
            auth_status=list_reason,
            root_listing_available=False,
            root_entry_count=0,
            error_message=f"Root listing failed: {list_reason}",
            access_details=json.dumps({
                "reason": list_reason,
                "banner": connect_banner,
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
                results_by_index[idx] = FtpAccessOutcome(
                    ip=candidate.ip,
                    country=candidate.country,
                    country_code=candidate.country_code,
                    port=candidate.port,
                    banner=candidate.banner,
                    shodan_data=json.dumps(candidate.shodan_data),
                    accessible=False,
                    auth_status="auth_fail",
                    root_listing_available=False,
                    root_entry_count=0,
                    error_message=f"Unexpected error: {err_str}",
                    access_details=json.dumps({"reason": "auth_fail", "error": err_str}),
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
    out.info(f"Access verification complete: {accessible_count} accessible of {total} tested")

    if outcomes:
        FtpPersistence(workflow.db_path).persist_access_outcomes_batch(outcomes)

    return accessible_count
