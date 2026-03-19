"""
HTTP scan stage implementations.

Card 2: Skeleton stubs only — no real Shodan query or HTTP verification yet.
Card 4 will replace these stubs with real implementation.
"""
from __future__ import annotations

from typing import List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from shared.http_workflow import HttpWorkflow


def run_discover_stage(workflow: "HttpWorkflow") -> Tuple[List, int]:
    """
    Stage 1: Shodan query + HTTP reachability check.

    Card 4 will implement real Shodan query + HTTP(S) verification.
    Returns (candidates, shodan_total).
    """
    workflow.output.info("HTTP Discovery: skeleton mode (no Shodan query yet)")
    return [], 0


def run_access_stage(workflow: "HttpWorkflow", candidates: List) -> int:
    """
    Stage 2: HTTP(S) access verification.

    Card 4 will implement real HTTP(S) access checking.
    Returns count of accessible hosts.
    """
    workflow.output.info("HTTP Access Verification: skeleton mode")
    workflow.last_accessible_directory_count = 0
    return 0
