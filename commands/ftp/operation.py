"""
FTP scan stage implementations (Card 4).

Replaces the skeleton sleep-loop stubs with real Shodan querying,
TCP port checks, and anonymous FTP authentication / root listing.
"""
from __future__ import annotations

import json
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
    verbose = getattr(out, "verbose", False)

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

    for i, candidate in enumerate(candidates, start=1):
        pct = (i / shodan_total) * 100
        out.raw(f"📊 Progress: {i}/{shodan_total} ({pct:.1f}%)")

        ok, reason = port_check(candidate.ip, candidate.port, timeout=float(connect_timeout))

        if ok:
            reachable.append(candidate)
        else:
            if verbose:
                out.info(f"  {candidate.ip} — {reason} (port check)")
            port_failed_outcomes.append(FtpDiscoveryOutcome(
                ip=candidate.ip,
                country=candidate.country,
                country_code=candidate.country_code,
                port=candidate.port,
                banner=candidate.banner,
                shodan_data=json.dumps(candidate.shodan_data),
                reason=reason,
                error_message=f"Port {candidate.port} unreachable: {reason}",
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
    verbose = getattr(out, "verbose", False)

    if not candidates:
        return 0

    ftp_cfg = workflow.config.get_ftp_config()
    verif = ftp_cfg.get("verification", {})
    auth_timeout = verif.get("auth_timeout", 10)
    listing_timeout = verif.get("listing_timeout", 15)

    total = len(candidates)
    out.info(f"Testing anonymous FTP access for {total} reachable hosts...")

    outcomes: List[FtpAccessOutcome] = []

    for i, candidate in enumerate(candidates, start=1):
        pct = (i / total) * 100
        out.raw(f"📊 Progress: {i}/{total} ({pct:.1f}%)")

        login_ok, connect_banner, login_reason = try_anon_login(
            candidate.ip, candidate.port, timeout=float(auth_timeout)
        )

        if not login_ok:
            if verbose:
                out.info(f"  {candidate.ip} — {login_reason}")
            outcomes.append(FtpAccessOutcome(
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
            ))
            continue

        # Login succeeded — attempt root listing.
        list_ok, entry_count, list_reason = try_root_listing(
            candidate.ip, candidate.port, timeout=float(listing_timeout)
        )

        if list_ok:
            if verbose:
                out.info(f"  {candidate.ip} — anonymous OK, {entry_count} root entries")
            outcomes.append(FtpAccessOutcome(
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
            ))
        else:
            if verbose:
                out.info(f"  {candidate.ip} — {list_reason}")
            outcomes.append(FtpAccessOutcome(
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
            ))

    accessible_count = sum(1 for o in outcomes if o.accessible)
    out.info(f"Access verification complete: {accessible_count} accessible of {total} tested")

    if outcomes:
        FtpPersistence(workflow.db_path).persist_access_outcomes_batch(outcomes)

    return accessible_count
