"""
Data models for the SearXNG Dork module.

C2: PreflightResult and reason code constants.
C3: RunOptions, RunResult, and run status constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Reason codes returned by run_preflight on failure.
INSTANCE_UNREACHABLE = "instance_unreachable"
INSTANCE_FORMAT_FORBIDDEN = "instance_format_forbidden"
INSTANCE_NON_JSON = "instance_non_json"
SEARCH_HTTP_ERROR = "search_http_error"
SEARCH_PARSE_ERROR = "search_parse_error"


@dataclass
class PreflightResult:
    """Result of a SearXNG instance preflight check."""

    ok: bool
    reason_code: Optional[str]  # None when ok=True
    message: str


# ---------------------------------------------------------------------------
# C3: Run status constants
# ---------------------------------------------------------------------------

RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE = "done"
RUN_STATUS_ERROR = "error"


@dataclass
class RunOptions:
    """Options for a single dork search run."""

    instance_url: str
    query: str
    max_results: int = 50
    bulk_probe_enabled: bool = False
    probe_config_path: Optional[str] = None
    probe_worker_count: Optional[int] = None


@dataclass
class RunResult:
    """Outcome of run_dork_search()."""

    run_id: Optional[int]   # None when failure occurs before DB row is created
    fetched_count: int
    deduped_count: int      # rows actually inserted (after URL dedupe per run)
    status: str             # RUN_STATUS_*
    error: Optional[str]    # None on success
    verified_count: int = 0 # rows classified (C4); 0 on pre-classification failure
    probe_enabled: bool = False
    probe_total: int = 0
    probe_clean: int = 0
    probe_issue: int = 0
    probe_unprobed: int = 0
